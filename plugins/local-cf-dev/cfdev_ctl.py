"""local-cf-dev 控制器 — 本地部署 CF Workers 代理（VLESS/Trojan-over-WS）供协议 E2E。

复用 singbox_ctl.py / xray_ctl.py 的 serve 模式：渲染 wrangler 项目 → 启 `wrangler dev`
（workerd 运行时）→ 阻塞直到停止信号 → 清理。插件 config 经 PLUGIN_* 环境变量注入
（plugin.py 解析 plugin.yaml config 段后设置），本脚本据此渲染 wrangler.toml + .dev.vars。

worker 源：默认引用 vendor/Cloudflare-vless-trojan（唯一真相源），经
PLUGIN_VENDOR_WORKER_DIR 覆盖；本插件不复制 2465 行混淆 JS。

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
import subprocess
import sys
import time

logger = logging.getLogger("cfdev")

# 插件目录（本脚本所在目录 = zigtester/plugins/local-cf-dev/）
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# worker 源（vendor 唯一真相源）。相对路径以插件目录为基准；绝对路径原样用。
# 默认 '../../../vendor/Cloudflare-vless-trojan' 指向 fixnet/vendor（zigtester 上三级）。
DEFAULT_VENDOR_DIR = os.path.normpath(
    os.path.join(PLUGIN_DIR, "../../../vendor/Cloudflare-vless-trojan")
)

# 临时 workdir（渲染 wrangler 项目 + 拷贝 worker；每次 serve 重建）
WORKDIR = "/tmp/zigtester-cfdev"

# TLS 模式（local_protocol: https）复用的生态 localhost 证书（与 local-echo/sing-box/xray 同源）
DEFAULT_CERT_PATH = os.path.normpath(
    os.path.join(PLUGIN_DIR, "../local-echo/certs/localhost.crt")
)
DEFAULT_KEY_PATH = os.path.normpath(
    os.path.join(PLUGIN_DIR, "../local-echo/certs/localhost.key")
)

# 各 worker_type 对应的 vendor 源文件与 .dev.vars 键
WORKER_SOURCE = {
    "vless": ("Vless_workers_pages", "_worker明.js", "uuid"),
    "trojan": ("Trojan_workers_pages", "_worker明.js", "pswd"),
}

# ── 配置读取（PLUGIN_* 环境变量 ← plugin.yaml config 段）──────────


def _env(key: str, default: str) -> str:
    return os.environ.get(f"PLUGIN_{key.upper()}", default)


def load_config() -> dict:
    """从 PLUGIN_* 环境变量读取插件配置（含默认值，独立手动运行兜底）。"""
    return {
        "workers_port": int(_env("workers_port", "18787")),
        "pages_port": int(_env("pages_port", "18788")),
        "local_protocol": _env("local_protocol", "http"),
        "worker_type": _env("worker_type", "vless"),
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


def _worker_source_path(cfg: dict) -> str:
    """定位选中 worker 的源文件路径；缺失抛错（给出明确指引）。"""
    worker_type = cfg["worker_type"]
    if worker_type not in WORKER_SOURCE:
        raise ValueError(
            f"未知 worker_type: {worker_type!r}（可用: {sorted(WORKER_SOURCE)}）"
        )
    subdir, filename, _dev_key = WORKER_SOURCE[worker_type]
    vendor_dir = _resolve_vendor_dir(cfg)
    path = os.path.join(vendor_dir, subdir, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"worker 源缺失: {path}\n"
            "请在 zigtester.yaml plugins.local-cf-dev.config.vendor_worker_dir 指向 "
            "vendor/Cloudflare-vless-trojan（唯一真相源，本插件不复制混淆 JS）"
        )
    return path


def _render_workdir(cfg: dict) -> str:
    """重建 /tmp/zigtester-cfdev：拷 worker → 写 wrangler.toml + .dev.vars。返回 workdir。"""
    worker_type = cfg["worker_type"]
    _subdir, _filename, dev_key = WORKER_SOURCE[worker_type]
    src = _worker_source_path(cfg)

    if os.path.isdir(WORKDIR):
        shutil.rmtree(WORKDIR)
    os.makedirs(os.path.join(WORKDIR, "src"), exist_ok=True)

    # 拷贝 worker 到 src/index.js（wrangler.toml main 指向它）
    shutil.copy2(src, os.path.join(WORKDIR, "src", "index.js"))

    # wrangler.toml：本地 Workers 项目，绑定 127.0.0.1 + 指定端口/协议
    wrangler_toml = (
        "name = \"local-cf-dev\"\n"
        "main = \"src/index.js\"\n"
        "compatibility_date = \"2024-01-01\"\n"
        "\n"
        "[dev]\n"
        f"port = {cfg['workers_port']}\n"
        "ip = \"127.0.0.1\"\n"
        f"local_protocol = \"{cfg['local_protocol']}\"\n"
    )
    with open(os.path.join(WORKDIR, "wrangler.toml"), "w", encoding="utf-8") as f:
        f.write(wrangler_toml)

    # .dev.vars：worker 经 env.<key> 读（vless=uuid / trojan=pswd）
    secret = cfg["uuid"] if worker_type == "vless" else cfg["trojan_password"]
    with open(os.path.join(WORKDIR, ".dev.vars"), "w", encoding="utf-8") as f:
        f.write(f"{dev_key} = \"{secret}\"\n")

    return WORKDIR


# ── 进程生命周期 ─────────────────────────────────────────────────


class WranglerDev:
    """封装 `npx wrangler dev` 子进程（start_new_session，便于杀整棵进程树）。"""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    def start(self, workdir: str, cfg: dict) -> bool:
        cmd = ["npx", "wrangler", "dev"]
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
            self._proc = subprocess.Popen(
                cmd,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError:
            logger.error("[cfdev] npx not found. Install Node.js 18+ first")
            return False
        except Exception as e:
            logger.error("[cfdev] wrangler dev start failed: %s", e)
            return False
        return True

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        """终止 wrangler 及 workerd 子进程（进程组 SIGTERM → SIGKILL）。"""
        if self._proc is None:
            return
        logger.info("[cfdev] stopping wrangler dev...")
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("[cfdev] wrangler did not exit after SIGTERM, sending SIGKILL")
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                self._proc.wait(timeout=3)
        except Exception as e:
            logger.warning("[cfdev] wrangler stop error: %s", e)
        self._proc = None
        logger.info("[cfdev] wrangler dev stopped")


def _serve(args: argparse.Namespace) -> int:
    """serve 模式：渲染 + 启动 wrangler dev + 阻塞等待停止信号。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [cfdev] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    try:
        cfg = load_config()
        workdir = _render_workdir(cfg)
    except Exception as e:
        logger.error("[cfdev] render failed: %s", e)
        return 1

    ctl = WranglerDev()
    if not ctl.start(workdir, cfg):
        return 1

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
        if os.path.isdir(workdir):
            shutil.rmtree(workdir, ignore_errors=True)

    return 0


def _render(args: argparse.Namespace) -> int:
    """render 模式：仅渲染 workdir 到 /tmp，不启动。供调试 / host 侧预渲染。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [cfdev] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    try:
        cfg = load_config()
        workdir = _render_workdir(cfg)
        logger.info("[cfdev] rendered workdir: %s", workdir)
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
