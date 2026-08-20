#!/usr/bin/env python3
"""xray-core 进程管理器 — 为 zigtester 插件和独立 CLI 调用提供统一入口。

设计目标:
  - 与 plugins/sing-box/singbox_ctl.py 同构（同生命周期模型、同 CLI 风格）
  - 接收 zigtester 注入的 PLUGIN_* 环境变量，渲染 configs/test_server.json 模板
  - 提供轻量 TCP readiness 服务（默认 :9190），供 zigtester ready_on 探针使用
  - 端口默认与 sing-box 插件错开 +100（详见 plugin.yaml config 字段）

用法:
  # 作为 zigtester 插件启动（由 plugin.yaml 调用）
  python3 xray_ctl.py serve --server-config configs/test_server.json

  # 单独使用
  python3 xray_ctl.py start --server-config configs/test_server.json
  python3 xray_ctl.py stop
  python3 xray_ctl.py status
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

logger = logging.getLogger("xray")

# ============================================================================
# 常量
# ============================================================================

def _resolve_bin(name: str, candidates: list[str]) -> str:
    """解析二进制绝对路径，摆脱对 PATH 的依赖。

    zigtester 插件子进程继承 MCP server 的 launchd 最小 PATH（/usr/bin:/bin:...），
    不含 /opt/homebrew/bin 与 /usr/local/bin，Popen([name]) 会 FileNotFoundError。
    先 shutil.which，再回退到常见绝对路径；都找不到则保留原名，让错误清晰暴露。
    """
    found = shutil.which(name)
    if found:
        return found
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return name


XRAY_BIN = _resolve_bin("xray", ["/usr/local/bin/xray", "/opt/homebrew/bin/xray"])
DEFAULT_READINESS_PORT = 9190          # 与 plugin.yaml ready_on.port 一致
DEFAULT_START_TIMEOUT = 15.0           # 与 plugin.yaml lifecycle.start.timeout 一致


# ============================================================================
# 工具函数
# ============================================================================

def _kill_process(name: str) -> None:
    """清理残留进程（pkill -f 模式匹配）。"""
    try:
        subprocess.run(
            ["pkill", "-f", name],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass


def _wait_tcp(host: str, port: int, timeout: float, interval: float = 0.2) -> bool:
    """轮询 TCP 端口直到可连接。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=0.3)
            s.close()
            return True
        except OSError:
            time.sleep(interval)
    return False


def _readiness_server(host: str, port: int, stop_event: threading.Event) -> None:
    """轻量 TCP readiness 服务：接受任意连接即可。

    zigtester 的 ready_on 探针通过 TCP 连接判定 xray 已就绪。
    不解析协议内容，避免依赖 xray stats API（部分二进制未启用）。
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((host, port))
        srv.listen(8)
        srv.settimeout(0.5)
    except OSError as e:
        logger.warning("[xray] readiness server bind failed: %s", e)
        srv.close()
        return

    logger.info("[xray] readiness server listening on %s:%d", host, port)
    while not stop_event.is_set():
        try:
            conn, _ = srv.accept()
            conn.close()
        except socket.timeout:
            continue
        except OSError:
            break
    srv.close()
    logger.info("[xray] readiness server stopped")


# ============================================================================
# 配置渲染
# ============================================================================

# 模板中的占位符形式为 __KEY__，渲染时从 PLUGIN_* 环境变量读取
# （zigtester 注入）或从默认 plugin.yaml 拷贝的常量读取（CLI 直调）
PLACEHOLDER_RE = re.compile(r"__([A-Z][A-Z0-9_]+)__")


def _render_config(template_path: str, env: dict[str, str]) -> str:
    """替换 JSON 模板中的 __KEY__ 占位符。

    占位符命名规范：UPPER_SNAKE_CASE，从 env 中读取同名键。
    """
    with open(template_path, "r", encoding="utf-8") as f:
        raw = f.read()

    def _repl(m: re.Match) -> str:
        key = m.group(1)
        val = env.get(key)
        if val is None:
            raise ValueError(f"config template references undefined variable: {key}")
        return val

    rendered = PLACEHOLDER_RE.sub(_repl, raw)
    return rendered


def _plugin_env_to_template_env(plugin_env: dict[str, str]) -> dict[str, str]:
    """将 zigtester 的 PLUGIN_* 注入转换为模板 __KEY__ 形式。

    plugin.yaml 中 config 字段:
      vless_tls_port: 16901
      vless_uuid: "e8b9a5b8-..."

    zigtester 注入到子进程:
      PLUGIN_VLESS_TLS_PORT=16901
      PLUGIN_VLESS_UUID=e8b9a5b8-...

    模板中占位符: __VLESS_TLS_PORT__, __VLESS_UUID__
    """
    out: dict[str, str] = {}
    for k, v in plugin_env.items():
        if k.startswith("PLUGIN_"):
            template_key = k[len("PLUGIN_"):]
            out[template_key] = v
    return out


def _render_config_to_path(
    template_path: str,
    output_path: str,
    extra_env: Optional[dict[str, str]] = None,
    plugin_dir: Optional[str] = None,
) -> None:
    """从模板渲染完整 JSON 配置并写入目标路径。

    渲染后自动将 cert/key 路径解析为绝对路径（相对于插件目录），
    避免 xray 进程因 cwd 不同而找不到证书。
    """
    env = _plugin_env_to_template_env(os.environ)
    if extra_env:
        env.update(extra_env)
    rendered = _render_config(template_path, env)

    # 解析为 dict 以便修正 cert/key 路径
    try:
        config = json.loads(rendered)
    except json.JSONDecodeError as e:
        raise ValueError(f"rendered config is not valid JSON: {e}\n--- rendered ---\n{rendered}")

    # plugin_dir 缺省时从 template_path 向上推一级（template 通常在 configs/ 子目录）
    if plugin_dir is None:
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(template_path)))
    _absolutize_cert_paths(config, plugin_dir)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    logger.info("[xray] config rendered: %s", output_path)


def _absolutize_cert_paths(config: dict, base_dir: str) -> None:
    """将 inbound.streamSettings.tlsSettings.certificates[*] 中的
    certificateFile / keyFile 转换为绝对路径（相对于 base_dir）。

    xray 进程的 cwd 不一定是插件目录，必须用绝对路径。
    """
    for ib in config.get("inbounds", []):
        ss = ib.get("streamSettings", {})
        tls = ss.get("tlsSettings", {})
        for cert in tls.get("certificates", []):
            for key in ("certificateFile", "keyFile"):
                rel = cert.get(key)
                if rel and not os.path.isabs(rel):
                    cert[key] = os.path.join(base_dir, rel)


# ============================================================================
# 端口冲突检测（启动前）
# ============================================================================

def _collect_inbound_ports(config: dict) -> list[int]:
    """从渲染后的 xray 配置中提取所有 inbound 监听端口。"""
    ports: list[int] = []
    for ib in config.get("inbounds", []):
        port = ib.get("port")
        if isinstance(port, int):
            ports.append(port)
    return ports


def _check_port_free(host: str, port: int) -> Optional[str]:
    """检测端口是否空闲。返回 None 表示空闲，否则返回占用进程信息。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        try:
            s.connect((host, port))
        except OSError:
            return None
        finally:
            s.close()
        return f"{host}:{port} 已被占用（可能由其他 zigtester 插件残留）"
    except Exception:
        return None


def check_port_conflicts(config: dict, host: str = "127.0.0.1") -> list[str]:
    """检查渲染后配置中所有 inbound 端口的占用情况。

    返回冲突列表（空列表 = 无冲突）。
    zigtester 启动插件前调用，失败时阻止插件启动。
    """
    conflicts: list[str] = []
    for port in _collect_inbound_ports(config):
        msg = _check_port_free(host, port)
        if msg is not None:
            conflicts.append(msg)
    return conflicts


# ============================================================================
# XrayController
# ============================================================================

class XrayController:
    """xray-core 进程管理器。

    使用方式:
        ctl = XrayController()
        ctl.start("configs/test_server.json")     # 渲染模板 + 启动进程
        ctl.wait_ready()                          # 等待 readiness 端口
        ...
        ctl.stop()                                # SIGTERM → SIGKILL
    """

    def __init__(self, readiness_host: str = "127.0.0.1", readiness_port: int = DEFAULT_READINESS_PORT):
        self.readiness_host = readiness_host
        self.readiness_port = readiness_port
        self._proc: subprocess.Popen | None = None
        self._readiness_stop: Optional[threading.Event] = None
        self._readiness_thread: Optional[threading.Thread] = None
        self._rendered_config_path: Optional[str] = None

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self, server_config: str, label: str = "") -> bool:
        """渲染配置 + 启动 readiness 探针 + 启动 xray 进程。

        Args:
            server_config: configs/test_server.json 模板路径
            label: 日志标签

        返回 True 表示成功。
        """
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        if not os.path.isabs(server_config):
            server_config = os.path.join(plugin_dir, server_config)

        if not os.path.exists(server_config):
            logger.error("[%s] server config not found: %s", label or "xray", server_config)
            return False

        # 渲染模板到运行时路径
        runtime_config = os.path.join(plugin_dir, "configs", ".rendered.json")
        try:
            _render_config_to_path(server_config, runtime_config, plugin_dir=plugin_dir)
        except Exception as e:
            logger.error("[%s] config render failed: %s", label or "xray", e)
            return False
        self._rendered_config_path = runtime_config

        # 启动前端口冲突检测
        with open(runtime_config, "r", encoding="utf-8") as f:
            rendered = json.load(f)
        conflicts = check_port_conflicts(rendered, host="127.0.0.1")
        if conflicts:
            logger.error("[%s] port conflicts detected:", label or "xray")
            for msg in conflicts:
                logger.error("  - %s", msg)
            return False

        # 启动 readiness 服务（独立线程）
        self._readiness_stop = threading.Event()
        self._readiness_thread = threading.Thread(
            target=_readiness_server,
            args=(self.readiness_host, self.readiness_port, self._readiness_stop),
            daemon=True,
        )
        self._readiness_thread.start()

        # 清理残留 xray 进程
        _kill_process("xray")

        # 启动 xray
        logger.info("[%s] starting xray-core: config=%s", label or "xray", runtime_config)
        try:
            self._proc = subprocess.Popen(
                [XRAY_BIN, "run", "-c", runtime_config],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # 排空 xray-core stdout/stderr 到插件日志（无人读管道会满 → xray 阻塞；
            # reality show:true 调试输出也依赖此路径可见）
            def _drain(pipe: object, tag: str) -> None:
                assert self._proc is not None
                for line in pipe:
                    logger.info("[xray-core %s] %s", tag, line.rstrip())

            self._drain_threads = [
                threading.Thread(target=_drain, args=(self._proc.stdout, "out"), daemon=True),
                threading.Thread(target=_drain, args=(self._proc.stderr, "err"), daemon=True),
            ]
            for t in self._drain_threads:
                t.start()
        except FileNotFoundError:
            logger.error(
                "[%s] xray binary not found. Install: "
                "brew install xray (macOS) 或下载 https://github.com/XTLS/Xray-core/releases",
                label or "xray",
            )
            self._stop_readiness()
            return False
        except Exception as e:
            logger.error("[%s] xray start failed: %s", label or "xray", e)
            self._stop_readiness()
            return False

        # 等待 readiness 端口（给 zigtester 探针使用）
        if not _wait_tcp(self.readiness_host, self.readiness_port, timeout=10.0, interval=0.2):
            logger.error(
                "[%s] readiness port %s:%d not ready in 10s",
                label or "xray", self.readiness_host, self.readiness_port,
            )
            self.stop()
            return False

        n_inbounds = len(rendered.get("inbounds", []))
        logger.info(
            "[%s] xray ready: readiness=%s:%d, %d inbounds",
            label or "xray",
            self.readiness_host, self.readiness_port,
            n_inbounds,
        )
        return True

    def stop(self, timeout: int = 10) -> None:
        """停止 xray 进程 + readiness 服务。"""
        if self._proc is not None:
            logger.info("[xray] stopping xray-core...")
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    logger.warning("[xray] xray did not exit after SIGTERM, sending SIGKILL")
                    self._proc.kill()
                    self._proc.wait(timeout=3)
            except Exception as e:
                logger.warning("[xray] xray stop error: %s", e)
            self._proc = None

        self._stop_readiness()

        if self._rendered_config_path and os.path.exists(self._rendered_config_path):
            try:
                os.remove(self._rendered_config_path)
            except OSError:
                pass
            self._rendered_config_path = None

        logger.info("[xray] xray-core stopped")

    def is_running(self) -> bool:
        """检查进程存活。"""
        return self._proc is not None and self._proc.poll() is None

    def _stop_readiness(self) -> None:
        """停止 readiness 服务线程。"""
        if self._readiness_stop is not None:
            self._readiness_stop.set()
        if self._readiness_thread is not None:
            self._readiness_thread.join(timeout=2)
            self._readiness_thread = None
        self._readiness_stop = None


# ============================================================================
# CLI 子命令
# ============================================================================

def _serve(args: argparse.Namespace) -> int:
    """serve 模式：渲染配置 + 启动 xray + 阻塞直到停止信号。

    由 plugin.yaml 的 start 命令调用。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [xray] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    ctl = XrayController(
        readiness_host=os.environ.get("PLUGIN_READINESS_HOST", "127.0.0.1"),
        readiness_port=int(os.environ.get("PLUGIN_READINESS_PORT", DEFAULT_READINESS_PORT)),
    )
    if not ctl.start(args.server_config, label="serve"):
        return 1

    stop_event = False

    def _on_signal(signum, _frame):
        nonlocal stop_event
        stop_event = True
        logger.info("[xray] received signal %d, shutting down...", signum)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        while not stop_event and ctl.is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        ctl.stop()

    return 0


def _render(args: argparse.Namespace) -> int:
    """render 模式：仅渲染模板到目标路径，不启动进程。供调试使用。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [xray] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    template = args.server_config
    if not os.path.isabs(template):
        template = os.path.join(plugin_dir, template)
    output = args.output
    if not os.path.isabs(output):
        output = os.path.join(plugin_dir, output)

    try:
        _render_config_to_path(template, output)
    except Exception as e:
        logger.error("[xray] render failed: %s", e)
        return 1

    logger.info("[xray] rendered config: %s", output)
    return 0


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="xray-core 进程管理器（zigtester 插件）",
    )
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="渲染配置 + 启动 xray 并阻塞（供 zigtester 插件调用）")
    p_serve.add_argument(
        "--server-config",
        default="configs/test_server.json",
        help="xray 配置模板路径（默认: configs/test_server.json）",
    )

    p_render = sub.add_parser("render", help="仅渲染配置模板到目标路径（调试用）")
    p_render.add_argument(
        "--server-config",
        default="configs/test_server.json",
        help="xray 配置模板路径",
    )
    p_render.add_argument(
        "--output",
        default="configs/.rendered.json",
        help="渲染输出路径",
    )

    args = parser.parse_args()

    if args.command == "serve":
        return _serve(args)
    elif args.command == "render":
        return _render(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(_main())