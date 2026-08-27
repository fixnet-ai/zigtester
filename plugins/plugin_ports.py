"""fixnet 生态端口/凭证单一真相源 — 统一从 zigtester/plugins/*/plugin.yaml 派生。

背景：端口配置曾分散在 ≥7 处同步点（各项目 tests/config.py、zigoutbounds
tests/lib、插件 configs/test_server.json、文档表等），历史出过 2080 冲突事故
（某 config.py 注释实证：curl 连到 sing-box 而非被测 zigbox）。

本模块是「唯一派生实现」：所有消费方（跨仓 config.py、插件 ctl 脚本、各项目
tests 库）只从 plugin.yaml 读取端口/凭证，禁止各自硬编码。plugin.yaml 是唯一
权威源；zigtester 自检（冲突预检/端口归属）仍走 plugin.py 的 yaml 全量解析。

为什么不用 PyYAML：跨仓测试脚本以 `python3 tests/...` 运行，系统 python3 可能
未装 PyYAML（zigtester 自身 pytest 走 venv 不受影响）。本模块因此内联一个
「极简 YAML 子集解析」，只提取消费方需要的顶层 `config`（扁平 key: scalar）
与 `ports`（标量列表）；其余段（build/lifecycle/description）一律忽略。
解析结果与 plugin.py 的 yaml 全量解析交叉校验（tests/test_plugin_ports.py）。

用法（跨仓 config.py 标准接法）：
    _PLUGINS_DIR = os.environ.get("ZIGTESTER_PLUGINS_DIR") or \\
        os.path.normpath(os.path.join(PROJECT_ROOT, "..", "zigtester", "plugins"))
    if os.path.isdir(_PLUGINS_DIR) and _PLUGINS_DIR not in sys.path:
        sys.path.insert(0, _PLUGINS_DIR)
    from plugin_ports import echo_tcp_port, singbox_port, singbox_credential
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Optional

# plugins 根目录：环境变量可覆盖（ZIGTESTER_PLUGINS_DIR），默认本文件所在目录。
# 本文件固定位于 zigtester/plugins/plugin_ports.py。
PLUGINS_ROOT = os.environ.get("ZIGTESTER_PLUGINS_DIR") or os.path.dirname(
    os.path.abspath(__file__)
)


# ── 极简 YAML 子集解析（无 PyYAML 依赖）───────────────────────────


def _parse_scalar(raw: str) -> Any:
    """解析标量值：去引号 / 整数化。"""
    s = raw.strip()
    if not s:
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    try:
        return int(s)
    except ValueError:
        return s


def _load_plugin_yaml_lite(path: str) -> dict[str, Any]:
    """读取 plugin.yaml 的 `config`（扁平 key: scalar）与 `ports`（标量列表）。

    只解析这两个段；顶层其余键（name/description/build/lifecycle）不做深入解析
    （name 作为顶层标量附带返回）。行内注释以第一个 `#` 截断（各 plugin.yaml
    的值不含 `#`）。
    """
    with open(path, "r", encoding="utf-8") as f:
        raw_lines = f.read().splitlines()

    data: dict[str, Any] = {}
    section: Optional[str] = None
    for raw in raw_lines:
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()

        if indent == 0:
            key, sep, val = content.partition(":")
            key = key.strip()
            if key == "name":
                data["name"] = _parse_scalar(val)
                section = None
            elif key == "config":
                data["config"] = {}
                section = "config"
            elif key == "ports":
                data["ports"] = []
                section = "ports"
            else:
                section = None
            continue

        if section is None:
            continue
        if section == "ports":
            if content.startswith("- "):
                data["ports"].append(_parse_scalar(content[2:]))
            continue
        # section == "config"：扁平 key: scalar（本插件 config 段不允许嵌套）
        key, sep, val = content.partition(":")
        if sep and val.strip():
            data["config"][key.strip()] = _parse_scalar(val)

    return data


# ── 插件加载 ───────────────────────────────────────────────────


def plugins_root() -> str:
    """返回 plugins 根目录（消费方诊断/报告用）。"""
    return PLUGINS_ROOT


def plugin_yaml_path(name: str) -> str:
    """插件 plugin.yaml 的绝对路径。"""
    return os.path.join(PLUGINS_ROOT, name, "plugin.yaml")


@lru_cache(maxsize=None)
def load_plugin(name: str) -> dict[str, Any]:
    """解析插件 plugin.yaml（lite 子集）。缺失/失败给出明确错误而非静默回退。"""
    path = plugin_yaml_path(name)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"插件 {name} 的 plugin.yaml 不存在: {path}\n"
            "端口真相源缺失 — 请在 zigtester/plugins/ 下补齐，禁止在消费方硬编码端口。"
        )
    data = _load_plugin_yaml_lite(path)
    if not isinstance(data.get("config"), dict):
        raise ValueError(f"插件 {name} plugin.yaml 缺少 config 段（端口真相源不完整）: {path}")
    return data


def config_value(name: str, key: str, default: Any = None) -> Any:
    """插件 config 段中的命名值（端口/凭证/地址）。"""
    cfg = load_plugin(name).get("config", {})
    return cfg.get(key, default)


def config_port(name: str, key: str) -> int:
    """插件 config 段中的端口（int）。权威源缺失即报错，杜绝回退硬编码。"""
    v = config_value(name, key)
    if v is None:
        raise KeyError(
            f"插件 {name} config 缺端口键 `{key}`（plugin.yaml 权威源不完整）"
        )
    return int(v)


def config_credential(name: str, key: str) -> str:
    """插件 config 段中的凭证（str）。"""
    v = config_value(name, key)
    if v is None:
        raise KeyError(
            f"插件 {name} config 缺凭证键 `{key}`（plugin.yaml 权威源不完整）"
        )
    return str(v)


def ports_list(name: str) -> list[int]:
    """插件声明的端口列表（用于 zigtester 冲突预检 / 消费方自检）。"""
    raw = load_plugin(name).get("ports", [])
    return [int(p) for p in raw if isinstance(p, (int, float))]


# ── local-echo 插件（协议自适应 echo）命名端口 ───────────────────

_ECHO_KEYS: dict[str, str] = {
    "tcp": "tcp_port",
    "dns": "dns_port",
    "real_dns": "real_dns_port",
    "h2": "h2_port",
    "h3": "h3_port",
    "http": "http_port",
    "tls": "tls_port",
    "reality_tls": "reality_tls_port",
    "bench": "bench_port",
    "stream": "stream_port",
}


def echo_port(key: str) -> int:
    """local-echo 命名端口（tcp/dns/real_dns/h2/h3/http/tls/reality_tls/bench/stream）。"""
    if key not in _ECHO_KEYS:
        raise KeyError(f"未知 local-echo 端口名: {key!r}，可用: {sorted(_ECHO_KEYS)}")
    return config_port("local-echo", _ECHO_KEYS[key])


def echo_tcp_port() -> int:
    return echo_port("tcp")


def echo_dns_port() -> int:
    return echo_port("dns")


def echo_real_dns_port() -> int:
    return echo_port("real_dns")


def echo_h2_port() -> int:
    return echo_port("h2")


def echo_h3_port() -> int:
    return echo_port("h3")


def echo_http_port() -> int:
    return echo_port("http")


def echo_tls_port() -> int:
    return echo_port("tls")


def echo_reality_tls_port() -> int:
    return echo_port("reality_tls")


def echo_bench_port() -> int:
    return echo_port("bench")


def echo_stream_port() -> int:
    return echo_port("stream")


# ── sing-box 插件 ──────────────────────────────────────────────


def singbox_port(key: str) -> int:
    """sing-box 插件协议 inbound 端口（mixed/ss/trojan/hysteria2/vmess/vless/...）。"""
    return config_port("sing-box", key)


def singbox_credential(key: str) -> str:
    """sing-box 插件凭证（ss_psk/trojan_password/vless_uuid/...）。"""
    return config_credential("sing-box", key)


def singbox_api_listen() -> str:
    """sing-box Clash API 监听地址（config.api_listen，如 127.0.0.1:9090）。"""
    return config_credential("sing-box", "api_listen")


def singbox_api_port() -> int:
    """sing-box Clash API 端口（从 api_listen 提取，供消费方探测）。"""
    listen = singbox_api_listen()
    return int(listen.rsplit(":", 1)[-1])


# ── xray-core 插件 ────────────────────────────────────────────


def xray_port(key: str) -> int:
    """xray-core 插件协议 inbound 端口（socks/ss/trojan/vmess/vless_*/...）。"""
    return config_port("xray-core", key)


def xray_credential(key: str) -> str:
    """xray-core 插件凭证（ss_psk/trojan_password/vless_uuid/...）。"""
    return config_credential("xray-core", key)


def xray_readiness_port() -> int:
    """xray 插件 readiness 探针端口（config.readiness_port）。"""
    return config_port("xray-core", "readiness_port")


# ── local-cf-dev 插件 ──────────────────────────────────────────


def cfdev_vless_port() -> int:
    """local-cf-dev VLESS worker 端口（wrangler dev 监听）。"""
    return config_port("local-cf-dev", "vless_workers_port")


def cfdev_trojan_port() -> int:
    """local-cf-dev Trojan worker 端口（wrangler dev 监听）。"""
    return config_port("local-cf-dev", "trojan_workers_port")


def cfdev_pages_port() -> int:
    """local-cf-dev Pages worker 端口（预留）。"""
    return config_port("local-cf-dev", "pages_port")


def cfdev_uuid() -> str:
    """local-cf-dev VLESS worker 默认 uuid。"""
    return config_credential("local-cf-dev", "uuid")


def cfdev_trojan_password() -> str:
    """local-cf-dev Trojan worker 默认密码。"""
    return config_credential("local-cf-dev", "trojan_password")


def cfdev_local_protocol() -> str:
    """local-cf-dev 本地协议（http | https，https 供 vless+ws+tls 客户端连入）。"""
    return config_credential("local-cf-dev", "local_protocol")
