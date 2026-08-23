#!/usr/bin/env python3
"""sing-box 进程管理器 + REST API 客户端。

统一管理 sing-box 生命周期，通过 REST API 热重载配置，消除各项目测试
脚本中重复的 SingboxProcess 实现。

用法:
  # 作为 zigtester 插件启动（由 plugin.yaml 调用）
  python3 singbox_ctl.py serve --api-listen 127.0.0.1:9090 --base-config configs/base.json

  # 作为库使用
  from plugins.sing_box import singbox_ctl
  ctl = singbox_ctl.SingboxController()
  ctl.start("configs/base.json")
  ctl.reload({"inbounds": [...], "outbounds": [...]})
  ctl.stop()

sing-box REST API 文档:
  https://sing-box.sagernet.org/configuration/experimental/clash-api/
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import select
import shutil
import signal
import subprocess
import sys
import time
from typing import Optional

import urllib.error
import urllib.request

logger = logging.getLogger("singbox")


class _HttpError(Exception):
    """本地 API 请求失败（连接/超时/协议错误）。"""


class _HttpResp:
    """最小 HTTP 响应对象 — 仅暴露 status_code / text，替代 requests.Response。"""

    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self.text = body.decode("utf-8", errors="replace")


# 本地 API opener — ProxyHandler({}) 绕过 HTTP_PROXY/http_proxy 等环境变量。
# 否则对 127.0.0.1 的请求会被代理劫持（代理无法回环到本机的 zigtester 端口），
# 导致 health check 假失败、插件被误判为启动失败。
# 使用 stdlib urllib 而非 requests——插件子进程的 python3 可能无 requests 依赖。
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_call(method: str, url: str, *, json_body=None, timeout: float = 10.0) -> _HttpResp:
    """向本地 sing-box REST API 发请求，返回最小响应对象。

    非 2xx 仍返回响应（调用方按 status 判断）；连接/超时错误抛 _HttpError。
    """
    data = None
    headers = {}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return _HttpResp(resp.status, resp.read())
    except urllib.error.HTTPError as e:
        # 非 2xx：保留状态码与响应体，供调用方按 status 分支
        return _HttpResp(e.code, e.read() or b"")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise _HttpError(str(e)) from e

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


SINGBOX_BIN = _resolve_bin("sing-box", ["/opt/homebrew/bin/sing-box", "/usr/local/bin/sing-box"])
DEFAULT_API = "127.0.0.1:9090"
DEFAULT_READY_TIMEOUT = 15
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


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


def _wait_tcp(host: str, port: int, timeout: float = 15.0, interval: float = 0.2) -> bool:
    """轮询 TCP 端口直到可连接。"""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=0.3)
            s.close()
            return True
        except OSError:
            time.sleep(interval)
    return False


def _wait_udp(port: int, timeout: float = 15.0) -> bool:
    """检测 UDP 端口是否已被监听。

    macOS: lsof -iUDP:<port>
    Linux: ss -uln sport = :<port>
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if IS_MACOS:
                result = subprocess.run(
                    ["lsof", "-iUDP:{}".format(port), "-P", "-n"],
                    capture_output=True, text=True, timeout=5,
                )
                if str(port) in result.stdout:
                    return True
            elif IS_LINUX:
                result = subprocess.run(
                    ["ss", "-uln", "sport", "= :{}".format(port)],
                    capture_output=True, text=True, timeout=5,
                )
                if str(port) in result.stdout:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _stderr_drain(proc: subprocess.Popen, label: str = "") -> str:
    """非阻塞读取 stderr 的最后几行（用于超时诊断）。"""
    lines: list[str] = []
    try:
        while True:
            ready, _, _ = select.select([proc.stderr], [], [], 0.1)
            if not ready:
                break
            line = proc.stderr.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip()
            if decoded:
                lines.append(decoded)
    except Exception:
        pass
    if lines:
        prefix = f"[{label}] " if label else ""
        return prefix + ("\n" + prefix).join(lines[-5:])
    return ""


# ============================================================================
# 配置渲染（模板 __KEY__ 占位符 → 实际值；单一真相源 = plugin.yaml config）
# ============================================================================
#
# configs/test_server.json 是模板：端口/凭证以 __UPPER_SNAKE__ 占位符引用
# plugins/sing-box/plugin.yaml config 段（唯一权威）。渲染取值优先级：
#   1) PLUGIN_* 环境变量（zigtester 插件启动时按 plugin.yaml 注入）
#   2) plugin.yaml config 段（CLI 独立渲染 / 直跑时的兜底）
# 两者同源于 plugin.yaml，结果必然一致；任何一侧缺键都显式报错，不静默。

PLACEHOLDER_RE = re.compile(r"__([A-Z][A-Z0-9_]+)__")


def _plugin_yaml_fallback_env() -> dict[str, str]:
    """从 plugins/sing-box/plugin.yaml config 段生成模板 env（CLI 直调兜底）。

    config 键大写后即模板占位符（mixed_port → __MIXED_PORT__，ss_psk →
    __SS_PSK__），保证不依赖 zigtester 注入也能渲染出与插件启动一致的配置。
    plugin_ports.py 缺失（插件目录未随版本同步）时返回空 dict —— 模板若含
    未定义占位符会在 _render_config 中显式报错，不会静默产出残缺配置。
    """
    plugins_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if plugins_dir not in sys.path:
        sys.path.insert(0, plugins_dir)
    try:
        import plugin_ports
    except ImportError:
        logger.warning(
            "[sing-box] plugin_ports.py 缺失 — 渲染仅依赖 PLUGIN_* 环境变量"
        )
        return {}
    cfg = plugin_ports.load_plugin("sing-box").get("config", {})
    env = {k.upper(): str(v) for k, v in cfg.items() if v is not None}
    # local-echo 端口（shadowtls/reality handshake 目标 TLS echo、route override 的
    # stream echo）不在 sing-box plugin.yaml 中，但模板 __ECHO_TLS_PORT__ /
    # __ECHO_STREAM_PORT__ 需要它们——直接从 local-echo 插件权威源派生，
    # 保证改 local-echo 端口只动 plugins/local-echo/plugin.yaml 一处。
    env["ECHO_TLS_PORT"] = str(plugin_ports.echo_tls_port())
    env["ECHO_STREAM_PORT"] = str(plugin_ports.echo_stream_port())
    return env


def _plugin_env_to_template_env(plugin_env: dict[str, str]) -> dict[str, str]:
    """将 zigtester 注入的 PLUGIN_* 环境变量转换为模板 __KEY__ 形式。"""
    out: dict[str, str] = {}
    for k, v in plugin_env.items():
        if k.startswith("PLUGIN_"):
            out[k[len("PLUGIN_"):]] = v
    return out


def _render_config(template_path: str, env: dict[str, str]) -> str:
    """替换 JSON 模板中的 __KEY__ 占位符；引用未定义键即报错（不静默）。"""
    with open(template_path, "r", encoding="utf-8") as f:
        raw = f.read()

    def _repl(m: re.Match) -> str:
        key = m.group(1)
        val = env.get(key)
        if val is None:
            raise ValueError(f"config template references undefined variable: {key}")
        return val

    return PLACEHOLDER_RE.sub(_repl, raw)


def _absolutize_cert_paths(config: dict, base_dir: str) -> None:
    """将 inbound.tls 的 certificate_path / key_path 转换为绝对路径（相对 base_dir）。

    sing-box 进程 cwd 不一定是插件目录（CLI 直跑 / host 侧启动时），
    必须用绝对路径才能找到 certs/ 下的证书。
    """
    for ib in config.get("inbounds", []):
        tls = ib.get("tls")
        if not isinstance(tls, dict):
            continue
        for key in ("certificate_path", "key_path"):
            rel = tls.get(key)
            if rel and not os.path.isabs(rel):
                tls[key] = os.path.join(base_dir, rel)


def _render_config_to_path(
    template_path: str,
    output_path: str,
    extra_env: Optional[dict[str, str]] = None,
    plugin_dir: Optional[str] = None,
) -> None:
    """从模板渲染完整 JSON 配置并写入目标路径（serve / render 共用）。

    渲染后解析为 dict 校验 JSON 合法性，并把证书路径绝对化，再写回。
    """
    env = dict(_plugin_yaml_fallback_env())
    env.update(_plugin_env_to_template_env(os.environ))
    if extra_env:
        env.update(extra_env)
    rendered = _render_config(template_path, env)

    # JSONC：模板含 // 注释（sing-box 支持），解析前先剥离
    cleaned = re.sub(r'//.*$', '', rendered, flags=re.MULTILINE)
    try:
        config = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"rendered config is not valid JSON: {e}\n--- rendered ---\n{rendered[:800]}"
        )

    if plugin_dir is None:
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(template_path)))
    _absolutize_cert_paths(config, plugin_dir)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    logger.info("[sing-box] config rendered: %s", output_path)


# ============================================================================
# SingboxController
# ============================================================================

class SingboxController:
    """sing-box 进程管理器 + REST API 客户端。

    使用方式:
        ctl = SingboxController(api_addr="127.0.0.1:9090")
        ctl.start("configs/base.json")        # 启动进程
        ctl.reload({...})                      # 热切换配置
        ctl.stop()                             # 停止进程
    """

    def __init__(self, api_addr: str = DEFAULT_API):
        self.api_addr = api_addr
        self.api_url = f"http://{api_addr}"
        self._proc: subprocess.Popen | None = None
        self._label = ""

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self, base_config: str, label: str = "") -> bool:
        """启动 sing-box（仅 base config），等待 REST API 就绪。

        Args:
            base_config: base.json 路径（最小配置，仅 API + empty inbounds）
            label: 日志标签（如 "server" / "client"）

        返回 True 表示启动成功，API 可访问。
        """
        self._label = label
        _kill_process("sing-box")

        if not os.path.exists(base_config):
            logger.error(f"[{self._label}] base config not found: {base_config}")
            return False

        logger.info(f"[{self._label}] starting sing-box: config={base_config}")
        try:
            self._proc = subprocess.Popen(
                [SINGBOX_BIN, "run", "-c", base_config],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error(
                f"[{self._label}] sing-box binary not found. "
                f"Install: brew install sing-box (macOS) or equivalent"
            )
            return False
        except Exception as e:
            logger.error(f"[{self._label}] sing-box start failed: {e}")
            return False

        # 等待 API 就绪
        api_host, api_port_str = self.api_addr.rsplit(":", 1)
        api_port = int(api_port_str)
        if not _wait_tcp(api_host, api_port, timeout=10.0):
            stderr_info = _stderr_drain(self._proc, self._label)
            logger.error(
                f"[{self._label}] sing-box API {self.api_addr} not ready in 10s\n{stderr_info}"
            )
            self.stop()
            return False

        # 验证 API 可访问
        if not self._check_api():
            logger.error(f"[{self._label}] sing-box API health check failed")
            self.stop()
            return False

        logger.info(f"[{self._label}] sing-box ready: api={self.api_addr}")
        return True

    def stop(self, timeout: int = 10) -> None:
        """停止 sing-box 进程。

        SIGTERM → wait → SIGKILL 标准流程。
        """
        if self._proc is None:
            return
        logger.info(f"[{self._label}] stopping sing-box...")
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning(f"[{self._label}] sing-box did not exit after SIGTERM, sending SIGKILL")
                self._proc.kill()
                self._proc.wait(timeout=3)
        except Exception as e:
            logger.warning(f"[{self._label}] sing-box stop error: {e}")
        self._proc = None
        logger.info(f"[{self._label}] sing-box stopped")

    def is_running(self) -> bool:
        """检查进程存活 + API 可访问。"""
        if self._proc is None:
            return False
        if self._proc.poll() is not None:
            return False
        return self._check_api()

    # ── 配置热重载 ────────────────────────────────────────────

    def reload(self, config: dict) -> bool:
        """PUT /configs，热切换完整配置。

        传入完整配置 JSON（dict），直接 PUT 到 API。
        成功返回 True（HTTP 204），失败返回 False。

        注意: 需要传完整配置，不是 patch —— API 不接受增量更新。
        """
        try:
            r = _http_call("PUT", f"{self.api_url}/configs", json_body=config, timeout=10)
            if r.status_code in (200, 204):
                logger.info(f"[{self._label}] config reloaded: {len(config.get('inbounds',[]))} inbounds, {len(config.get('outbounds',[]))} outbounds")
                return True
            else:
                logger.warning(
                    f"[{self._label}] config reload failed: HTTP {r.status_code}\n{r.text[:500]}"
                )
                return False
        except _HttpError as e:
            logger.error(f"[{self._label}] config reload request failed: {e}")
            return False

    def reload_file(self, config_path: str) -> bool:
        """从文件加载配置并热重载。支持 sing-box JSONC 格式（含 // 注释）。"""
        with open(config_path, "r", encoding="utf-8") as f:
            raw = f.read()
        # 去除 // 行注释（sing-box JSONC 格式）
        cleaned = re.sub(r'//.*$', '', raw, flags=re.MULTILINE)
        config = json.loads(cleaned)
        return self.reload(config)

    # ── 就绪检测（协议端口）───────────────────────────────────

    def wait_ready(self, port: int, protocol: str = "tcp", timeout: float = DEFAULT_READY_TIMEOUT) -> bool:
        """等待指定协议的端口就绪。

        Args:
            port: 端口号
            protocol: "tcp" 或 "udp"
            timeout: 超时秒数

        返回 True 表示端口就绪；超时返回 False（自动 dump stderr）。
        """
        if protocol == "tcp":
            ok = _wait_tcp("127.0.0.1", port, timeout)
        elif protocol == "udp":
            ok = _wait_udp(port, timeout)
        else:
            logger.error(f"[{self._label}] unknown protocol: {protocol}")
            return False

        if not ok:
            stderr_info = _stderr_drain(self._proc, self._label) if self._proc else ""
            logger.error(
                f"[{self._label}] port {protocol}:{port} not ready in {timeout}s\n{stderr_info}"
            )
        return ok

    # ── 配置生成（对应现有测试脚本的 config builder）───────────

    def generate_ss_config(self, port: int, method: str, psk: str, log_level: str = "warn") -> dict:
        """动态生成 SS2022 服务端配置。

        对应 test_protocols.py / benchmark.py 的 _generate_ss_config()。
        """
        return {
            "log": {"level": log_level},
            "inbounds": [{
                "type": "shadowsocks",
                "tag": "ss-in",
                "listen": "127.0.0.1",
                "listen_port": port,
                "method": method,
                "password": psk,
            }],
            "outbounds": [{"type": "direct", "tag": "direct"}],
        }

    def build_client_config(
        self,
        server_port: int,
        protocol: str,
        client_port: int = 0,
        tag: str = "test-client",
        extra_outbound: dict | None = None,
        log_level: str = "warn",
    ) -> dict:
        """生成客户端配置 — mixed 入站 + 协议出站 → 127.0.0.1:server_port。

        Args:
            server_port: 目标 sing-box 服务端端口
            protocol: 协议类型 (shadowsocks / trojan / vmess / vless / hysteria2 / tuic)
            client_port: 客户端 mixed 入站端口（0 = 自动分配随机端口）
            tag: 出站标签
            extra_outbound: 额外出站字段（如 TLS 配置）

        对应 test_all_protocols.py 的 _build_client_config()。
        """
        if client_port == 0:
            import random
            client_port = random.randint(14000, 14999)

        outbound: dict = {
            "type": protocol,
            "tag": tag,
            "server": "127.0.0.1",
            "server_port": server_port,
        }
        if extra_outbound:
            outbound.update(extra_outbound)

        return {
            "log": {"level": log_level},
            "inbounds": [{
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "127.0.0.1",
                "listen_port": client_port,
            }],
            "outbounds": [outbound, {"type": "direct", "tag": "direct"}],
        }

    def build_server_config(
        self,
        inbounds: list[dict],
        log_level: str = "warn",
    ) -> dict:
        """生成服务端配置 — 多协议 inbound + direct 出站。

        Args:
            inbounds: inbound 定义列表（每个包含 type/tag/listen/listen_port + 协议字段）
            log_level: 日志级别

        对应 test_all_protocols.py 的 singbox_all_inbounds.json 模式。
        """
        return {
            "log": {"level": log_level},
            "inbounds": inbounds,
            "outbounds": [{"type": "direct", "tag": "direct"}],
        }

    # ── 内部方法 ──────────────────────────────────────────────

    def _check_api(self) -> bool:
        """GET /version 验证 API 可访问。"""
        try:
            r = _http_call("GET", f"{self.api_url}/version", timeout=3)
            return r.status_code == 200
        except _HttpError:
            return False


# ============================================================================
# CLI: serve 模式（供 zigtester 插件调用）
# ============================================================================

def _serve(args: argparse.Namespace) -> int:
    """渲染模板 + 启动 sing-box 并阻塞直到收到停止信号。

    server_config（test_server.json）是模板：先经 _render_config_to_path
    渲染（__KEY__ → plugin.yaml config 值）到 configs/.rendered.json，再以
    渲染结果启动。包含所有协议 inbound + API。
    注意: sing-box Clash API 不支持原生格式热重载（仅接受 Clash 格式），
    需要切换配置时直接重启进程。

    退出时自动清理 sing-box 进程。由 plugin.yaml 的 start 命令调用。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [sing-box] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    plugin_dir = os.path.dirname(os.path.abspath(__file__))

    server_config = args.server_config
    if not os.path.isabs(server_config):
        server_config = os.path.join(plugin_dir, server_config)

    if not os.path.exists(server_config):
        logger.error("[sing-box] server config not found: %s", server_config)
        return 1

    # 渲染模板（__KEY__ 占位符 → plugin.yaml 值）到运行时路径，再启动
    runtime_config = os.path.join(plugin_dir, "configs", ".rendered.json")
    try:
        _render_config_to_path(server_config, runtime_config, plugin_dir=plugin_dir)
    except Exception as e:
        logger.error("[sing-box] config render failed: %s", e)
        return 1

    ctl = SingboxController(api_addr=args.api_listen)
    if not ctl.start(runtime_config, label="serve"):
        return 1

    try:
        with open(runtime_config, "r", encoding="utf-8") as f:
            n_inbounds = len(json.load(f).get("inbounds", []))
    except Exception:
        n_inbounds = 0
    logger.info("[sing-box] %d inbounds ready on %s", n_inbounds, args.api_listen)

    # 阻塞等待停止信号
    stop_event = False

    def _on_signal(signum, frame):
        nonlocal stop_event
        stop_event = True
        logger.info("[sing-box] received signal %d, shutting down...", signum)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        while not stop_event and ctl.is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        ctl.stop()
        # 清理运行时渲染产物（避免污染工作区/被误提交）
        if os.path.exists(runtime_config):
            try:
                os.remove(runtime_config)
            except OSError:
                pass

    return 0


def _render(args: argparse.Namespace) -> int:
    """render 模式：仅渲染模板到目标路径，不启动进程。供调试 / host 侧预渲染。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [sing-box] %(levelname)s %(message)s",
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
        _render_config_to_path(template, output, plugin_dir=plugin_dir)
    except Exception as e:
        logger.error("[sing-box] render failed: %s", e)
        return 1

    logger.info("[sing-box] rendered config: %s", output)
    return 0


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="sing-box 进程管理器 + REST API 客户端",
    )
    sub = parser.add_subparsers(dest="command")

    # serve — 渲染 + 启动并阻塞（zigtester 插件用）
    p_serve = sub.add_parser("serve", help="渲染配置并启动 sing-box 后阻塞（供 zigtester 插件调用）")
    p_serve.add_argument("--api-listen", default=DEFAULT_API, help="API 监听地址，需与配置中一致 (默认: 127.0.0.1:9090)")
    p_serve.add_argument("--server-config", default="configs/test_server.json", help="sing-box 配置模板路径")

    # render — 仅渲染（调试 / host 侧预渲染）
    p_render = sub.add_parser("render", help="仅渲染配置模板到目标路径（调试/预渲染用）")
    p_render.add_argument("--server-config", default="configs/test_server.json", help="sing-box 配置模板路径")
    p_render.add_argument("--output", default="configs/.rendered.json", help="渲染输出路径")

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
