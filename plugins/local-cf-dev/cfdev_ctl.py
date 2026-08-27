"""local-cf-dev 控制器 — 本地部署 CF Workers 代理（VLESS/Trojan-over-WS）供协议 E2E。

复用 singbox_ctl.py / xray_ctl.py 的 serve 模式：渲染 wrangler 项目 → 启 `wrangler dev`
（workerd 运行时）→ 阻塞直到停止信号 → 清理。插件 config 经 PLUGIN_* 环境变量注入
（plugin.py 解析 plugin.yaml config 段后设置），本脚本据此渲染 wrangler.toml + .dev.vars。

worker 源：默认引用 vendor/Cloudflare-vless-trojan（唯一真相源），经
PLUGIN_VENDOR_WORKER_DIR 覆盖；本插件不复制 2465 行混淆 JS。

**双 worker 常驻（阶段 27.2 收尾，2026-08-28）**：VLESS 与 Trojan 是两个独立的 HTTP 端点
（各自独立端口），wrangler 不支持「单 config 双脚本」（且单命令多 `-c` 时仅 primary 暴露
HTTP URL，auxiliary 只能经 service bindings 访问）——故本控制器管理**两个** `wrangler dev`
子进程，每个 worker 渲染一份独立 workdir + 独立 wrangler.toml + 独立 .dev.vars + 独立端口。

关键事实（详见 README + zigtester findings §11）：
- workerd 本地支持 `cloudflare:sockets connect()`，且本地模式不拦回环地址。
- 一律用 127.0.0.1（localhost 有 IPv4/IPv6 二义，workers-sdk PR #12913）。
- ECH 本地不可测（workerd 不做 ECH 终止，ECH 是 CF 边缘能力，与 worker 无关）。
- worker 会把 IPv4 目标重写为 sslip.io → E2E 客户端须用域名目标（localhost）。

真实部署到 Cloudflare（本插件只做本地 dev；真连 CF 边缘时用 vendor 项目原部署流程）：
- 环境搭建：Node.js 18+；wrangler 经 npx 提供（`npx wrangler dev` 本地调试，无需全局安装）。
- 更新上传（一次性认证 + 一键部署）：
  1. `npx wrangler login` — 浏览器授权一次，本机获得 CF 账号凭证。
  2. Workers：`npx wrangler deploy` — 读 wrangler.toml 上传到 workers.dev，打印公网 URL；
     Pages：`npx wrangler pages deploy <静态目录>` — 首次交互选「新建项目」+ 命名。
  3. 云端密钥：`.dev.vars` 不会随部署上传，`npx wrangler secret put <KEY>` 手动添加
     （或 CF 后台「环境变量」）；D1/KV/R2 绑定同理需云端同名资源，wrangler 会提示自动创建。
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

logger = logging.getLogger("cfdev")

# 插件目录（本脚本所在目录 = zigtester/plugins/local-cf-dev/）
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# worker 源（vendor 唯一真相源）。相对路径以插件目录为基准；绝对路径原样用。
# 默认 '../../../vendor/Cloudflare-vless-trojan' 指向 fixnet/vendor（zigtester 上三级）。
DEFAULT_VENDOR_DIR = os.path.normpath(
    os.path.join(PLUGIN_DIR, "../../../vendor/Cloudflare-vless-trojan")
)

# 临时 workdir 基名（每个 worker 一个独立 workdir：<BASE>-vless / <BASE>-trojan；每次 serve 重建）
WORKDIR_BASE = "/tmp/zigtester-cfdev"

# TLS 模式（local_protocol: https）复用的生态 localhost 证书（与 local-echo/sing-box/xray 同源）
DEFAULT_CERT_PATH = os.path.normpath(
    os.path.join(PLUGIN_DIR, "../local-echo/certs/localhost.crt")
)
DEFAULT_KEY_PATH = os.path.normpath(
    os.path.join(PLUGIN_DIR, "../local-echo/certs/localhost.key")
)

# 双 worker 常驻：VLESS + Trojan 各一个 wrangler dev 子进程、各自独立端口/独立 workdir。
# 每个 worker 描述其 vendor 源文件、.dev.vars 键、plugin.yaml config 端口键/凭证键。
WORKERS = [
    {
        "key": "vless",
        "subdir": "Vless_workers_pages",
        "filename": "_worker明.js",
        "dev_key": "uuid",                  # .dev.vars 键（worker 经 env.<key> 读）
        "port_key": "vless_workers_port",  # plugin.yaml config 端口键
        "secret_key": "uuid",               # plugin.yaml config 凭证键
        "compatibility_date": "2024-01-01",  # VLESS 不用 Node 内置模块，保持已验证 PASS 的原值
        "compatibility_flags": [],
    },
    {
        "key": "trojan",
        "subdir": "Trojan_workers_pages",
        "filename": "_worker明.js",
        "dev_key": "pswd",
        "port_key": "trojan_workers_port",
        "secret_key": "trojan_password",
        # 内嵌 js-sha256 用 require("crypto")/require("buffer")（Node 内置），esbuild 静态
        # 解析不到 → 构建失败。nodejs_compat 须配合 compatibility_date >= 2024-09-23 才进
        # 入 v2 模式解析「裸 require」（v1 模式只解析 node: 前缀）。实测 wrangler 4.127。
        "compatibility_date": "2024-09-23",
        "compatibility_flags": ["nodejs_compat"],
    },
]

# ── 配置读取（PLUGIN_* 环境变量 ← plugin.yaml config 段）──────────


def _env(key: str, default: str) -> str:
    return os.environ.get(f"PLUGIN_{key.upper()}", default)


def load_config() -> dict:
    """从 PLUGIN_* 环境变量读取插件配置（含默认值，独立手动运行兜底）。"""
    return {
        "vless_workers_port": int(_env("vless_workers_port", "18787")),
        "trojan_workers_port": int(_env("trojan_workers_port", "18789")),
        "pages_port": int(_env("pages_port", "18788")),
        "local_protocol": _env("local_protocol", "http"),
        "uuid": _env("uuid", "86c50e3a-5b87-49dd-bd20-03c7f2735e40"),
        "trojan_password": _env("trojan_password", "trojan"),
        "echo_host": _env("echo_host", "127.0.0.1"),
        "https_cert_path": _env("https_cert_path", DEFAULT_CERT_PATH),
        "https_key_path": _env("https_key_path", DEFAULT_KEY_PATH),
        "vendor_worker_dir": _env("vendor_worker_dir", DEFAULT_VENDOR_DIR),
    }


# ── 渲染 ────────────────────────────────────────────────────────


def _resolve_path(p: str) -> str:
    """解析插件配置里的路径（相对路径以插件目录为基准，绝对路径原样返回）。"""
    if not os.path.isabs(p):
        p = os.path.normpath(os.path.join(PLUGIN_DIR, p))
    return p


def _resolve_vendor_dir(cfg: dict) -> str:
    """解析 vendor worker 源目录。"""
    return _resolve_path(cfg["vendor_worker_dir"])


def _worker_source_path(worker: dict, cfg: dict) -> str:
    """定位单个 worker 的源文件路径；缺失抛错（给出明确指引）。"""
    vendor_dir = _resolve_vendor_dir(cfg)
    path = os.path.join(vendor_dir, worker["subdir"], worker["filename"])
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"worker 源缺失: {path}\n"
            "请在 zigtester.yaml plugins.local-cf-dev.config.vendor_worker_dir 指向 "
            "vendor/Cloudflare-vless-trojan（唯一真相源，本插件不复制混淆 JS）"
        )
    return path


def _render_workdir(worker: dict, cfg: dict) -> str:
    """为单个 worker 重建 workdir（/tmp/zigtester-cfdev-<key>）：拷 worker → 写
    wrangler.toml + .dev.vars。返回 workdir 路径。"""
    workdir = f"{WORKDIR_BASE}-{worker['key']}"
    src = _worker_source_path(worker, cfg)

    if os.path.isdir(workdir):
        shutil.rmtree(workdir)
    os.makedirs(os.path.join(workdir, "src"), exist_ok=True)

    # 拷贝 worker 到 src/index.js（wrangler.toml main 指向它）
    shutil.copy2(src, os.path.join(workdir, "src", "index.js"))

    # wrangler.toml：本地 Workers 项目，绑定 127.0.0.1 + 指定端口/协议。
    # compatibility_date / compatibility_flags 按 worker 声明（Trojan 的 js-sha256 需
    # nodejs_compat + date >= 2024-09-23 才能解析裸 require("crypto")/require("buffer")）。
    port = cfg[worker["port_key"]]
    date = worker["compatibility_date"]
    flags = worker.get("compatibility_flags", [])
    flags_line = ""
    if flags:
        flags_list = ", ".join(f'"{f}"' for f in flags)
        flags_line = f"compatibility_flags = [{flags_list}]\n"
    wrangler_toml = (
        f"name = \"local-cf-dev-{worker['key']}\"\n"
        "main = \"src/index.js\"\n"
        f"compatibility_date = \"{date}\"\n"
        f"{flags_line}"
        "\n"
        "[dev]\n"
        f"port = {port}\n"
        "ip = \"127.0.0.1\"\n"
        f"local_protocol = \"{cfg['local_protocol']}\"\n"
    )
    with open(os.path.join(workdir, "wrangler.toml"), "w", encoding="utf-8") as f:
        f.write(wrangler_toml)

    # .dev.vars：worker 经 env.<key> 读（vless=uuid / trojan=pswd）
    secret = cfg[worker["secret_key"]]
    with open(os.path.join(workdir, ".dev.vars"), "w", encoding="utf-8") as f:
        f.write(f"{worker['dev_key']} = \"{secret}\"\n")

    return workdir


# ── 进程生命周期 ─────────────────────────────────────────────────


def _resolve_npx() -> tuple[str, dict[str, str]]:
    """定位 npx 命令 + 带 node bin 目录的 env。

    zigtester MCP server 以极简 PATH（/usr/bin:/bin）启动，`npx`/`node`（经 n 版本
    管理器装在 ~/.n/bin）不在其中 → 直接 `subprocess.Popen(["npx", ...])` 抛
    FileNotFoundError。此处先 `shutil.which` 探测，再兜底常见 node 安装目录，找到后
    把 node bin 目录前置注入 PATH（npx 是 `#!/usr/bin/env node` 脚本，必须能在 PATH
    里找到 node）。

    返回 (npx 命令路径, 注入 PATH 的 env)。找不到 node 时返回 ("npx", 原 env)，
    由 Popen 的 FileNotFoundError 保持原有报错语义。
    """
    node = shutil.which("node")
    node_dir = os.path.dirname(node) if node else None
    if node_dir is None:
        for cand in (
            "~/.n/bin",
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/usr/local/opt/node/bin",
            "/opt/homebrew/opt/node/bin",
        ):
            d = os.path.expanduser(cand)
            if os.path.isfile(os.path.join(d, "node")):
                node_dir = d
                break
    if node_dir is None:
        return "npx", dict(os.environ)
    npx = shutil.which("npx")
    if npx is None:
        npx = os.path.join(node_dir, "npx")
    env = dict(os.environ)
    env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
    return npx, env


def _wait_port_ready(port: int, timeout: float) -> bool:
    """阻塞等待本机 TCP 端口就绪（worker 内自检用，与 zigtester ready_on 解耦）。

    双 worker 常驻下，zigtester ready_on 只探 vless 端口；trojan worker 是否起来由
    本控制器自行把关——任一 worker 端口未就绪即退出（非零），让 ready_on 探测失败。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.5)
    return False


class WranglerDev:
    """封装多个 `npx wrangler dev` 子进程（每 worker 一个，start_new_session 便于杀整棵树）。"""

    def __init__(self) -> None:
        self._procs: list[subprocess.Popen] = []

    def start(self, workdir: str, cfg: dict) -> bool:
        """启动一个 wrangler dev 子进程（追加到 _procs）。失败返回 False。"""
        npx, env = _resolve_npx()
        # --inspector-port 0：禁用 devtools 调试器端口。双 worker 常驻 = 两个 wrangler dev
        # 并发，默认都抢 9229（workerd inspector 端口）→ 后起者 "Address already in use"。
        # 本地 E2E 不需要调试器，禁掉避免端口冲突（否则两个 worker 无法同时运行）。
        cmd = [npx, "wrangler", "dev", "--inspector-port", "0"]
        if cfg["local_protocol"] == "https":
            cert = _resolve_path(cfg["https_cert_path"])
            key = _resolve_path(cfg["https_key_path"])
            if os.path.isfile(cert) and os.path.isfile(key):
                cmd += ["--https-cert-path", cert, "--https-key-path", key]
            else:
                logger.warning(
                    "[cfdev] 自定义证书缺失（%s / %s），回退 wrangler 默认自签名证书",
                    cert,
                    key,
                )
        logger.info("[cfdev] starting wrangler dev: workdir=%s cmd=%s", workdir, cmd)
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=env,
            )
        except FileNotFoundError:
            logger.error("[cfdev] npx/node not found. Install Node.js 18+ first")
            return False
        except Exception as e:
            logger.error("[cfdev] wrangler dev start failed: %s", e)
            return False
        # 排空 stdout/stderr 到日志文件：不排空会管道满阻塞 wrangler（预存隐患），
        # 同时保留 workerd console.log 供 E2E 失败取证（worker 侧错误只能在这看到）。
        # 二进制模式：Popen 未开 text=True，stdout/stderr 是 bytes 流，直接写 wb。
        log_path = f"{workdir}.log"
        logf = open(log_path, "wb")

        def _drain(stream) -> None:
            for line in stream:
                logf.write(line)
                logf.flush()

        threading.Thread(target=_drain, args=(proc.stdout,), daemon=True).start()
        threading.Thread(target=_drain, args=(proc.stderr,), daemon=True).start()
        proc._log_file = logf  # type: ignore[attr-defined]
        proc._log_path = log_path  # type: ignore[attr-defined]
        self._procs.append(proc)
        return True

    def is_running(self) -> bool:
        return any(p is not None and p.poll() is None for p in self._procs)

    def stop(self) -> None:
        """终止全部 wrangler 及 workerd 子进程（各进程组 SIGTERM → SIGKILL）。"""
        if not self._procs:
            return
        logger.info("[cfdev] stopping %d wrangler dev...", len(self._procs))
        for proc in self._procs:
            if proc is None:
                continue
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                pass
        for proc in self._procs:
            if proc is None:
                continue
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    proc.wait(timeout=3)
                except Exception:
                    pass
            except Exception:
                pass
            log_file = getattr(proc, "_log_file", None)
            if log_file is not None:
                try:
                    log_file.close()
                except Exception:
                    pass
        self._procs = []
        logger.info("[cfdev] wrangler dev stopped")


def _serve(args: argparse.Namespace) -> int:
    """serve 模式：渲染全部 worker + 启动各 wrangler dev + 阻塞等待停止信号。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [cfdev] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    try:
        cfg = load_config()
        workdirs = [_render_workdir(w, cfg) for w in WORKERS]
    except Exception as e:
        logger.error("[cfdev] render failed: %s", e)
        return 1

    ctl = WranglerDev()
    for w, workdir in zip(WORKERS, workdirs):
        if not ctl.start(workdir, cfg):
            ctl.stop()
            return 1

    # 双 worker 内自检：任一端口未就绪即退出（非零），让 zigtester ready_on 探测失败
    # （否则 trojan worker 启动失败时 vless 端口仍可连，插件会"假就绪"）。
    for w in WORKERS:
        port = cfg[w["port_key"]]
        if not _wait_port_ready(port, timeout=30):
            logger.error("[cfdev] worker %s 端口 %d 未就绪，退出", w["key"], port)
            ctl.stop()
            return 1
    logger.info("[cfdev] 全部 worker 就绪: %s", {w["key"]: cfg[w["port_key"]] for w in WORKERS})

    # 阻塞等待停止信号
    stop_event = False

    def _on_signal(signum, frame):
        nonlocal stop_event
        stop_event = True
        logger.info("[cfdev] received signal %d, shutting down...", signum)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        while not stop_event and ctl.is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        ctl.stop()
        # 清理渲染产物（避免污染 /tmp，防误提交）
        for workdir in workdirs:
            if os.path.isdir(workdir):
                shutil.rmtree(workdir, ignore_errors=True)

    return 0


def _render(args: argparse.Namespace) -> int:
    """render 模式：仅渲染全部 workdir 到 /tmp，不启动。供调试 / host 侧预渲染。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [cfdev] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    try:
        cfg = load_config()
        workdirs = [_render_workdir(w, cfg) for w in WORKERS]
        logger.info("[cfdev] rendered workdirs: %s", workdirs)
        return 0
    except Exception as e:
        logger.error("[cfdev] render failed: %s", e)
        return 1


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="local-cf-dev 控制器 — 本地部署 CF Workers 代理（wrangler dev / workerd）",
    )
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="渲染并启动 wrangler dev 后阻塞（供 zigtester 插件调用）")
    p_render = sub.add_parser("render", help="仅渲染 workdir 到 /tmp（调试/预渲染用）")

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
