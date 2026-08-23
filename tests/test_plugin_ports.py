#!/usr/bin/env python3
"""plugin_ports（端口/凭证单一真相源）单元测试。

验证：
  1. 极简 lite YAML 解析器与 plugin.py 的 yaml.safe_load 全量解析结果一致
     （config 段 + ports 列表）——真实 plugins/*/plugin.yaml。
  2. 命名访问器（echo_tcp_port/singbox_port/xray_port/...）从 config 段派生正确。
  3. sing-box test_server.json 模板经 singbox_ctl._render_config_to_path 渲染：
     所有 __KEY__ 占位符可被 plugin.yaml 值填充，结果结构合法（18 个 inbound、
     端口/凭证与 plugin.yaml 一致）。
  4. xray-core test_server.json 模板的占位符全部可解析。

运行（zigtester venv，因 pyyaml 交叉校验依赖 venv 的 yaml）：
    .venv/bin/python -m pytest tests/test_plugin_ports.py -q
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile

import pytest

_PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
for _d in (_PLUGINS_DIR, os.path.join(_PLUGINS_DIR, "sing-box")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import plugin_ports  # noqa: E402
import singbox_ctl  # noqa: E402

try:
    import yaml  # noqa: E402
except ImportError:
    yaml = None

REAL_PLUGINS = ["local-echo", "sing-box", "xray-core"]


def _strip_jsonc(raw: str) -> str:
    return re.sub(r"//.*$", "", raw, flags=re.MULTILINE)


@pytest.mark.skipif(yaml is None, reason="需要 venv 的 PyYAML（系统 python3 未安装）")
def test_lite_parser_matches_pyyaml():
    """lite 解析器的 config/ports 必须与 yaml.safe_load 完全一致。"""
    for name in REAL_PLUGINS:
        path = plugin_ports.plugin_yaml_path(name)
        full = yaml.safe_load(open(path, encoding="utf-8"))
        lite = plugin_ports._load_plugin_yaml_lite(path)

        assert lite["config"] == full["config"], f"{name}: config 不一致"
        assert lite["ports"] == full["ports"], f"{name}: ports 不一致"
        assert lite["name"] == full["name"], f"{name}: name 不一致"


def test_named_accessors_match_config():
    """命名访问器必须从 plugin.yaml config 段派生（不是独立硬编码）。"""
    assert plugin_ports.echo_tcp_port() == plugin_ports.config_port("local-echo", "tcp_port")
    assert plugin_ports.echo_dns_port() == plugin_ports.config_port("local-echo", "dns_port")
    assert plugin_ports.echo_real_dns_port() == plugin_ports.config_port("local-echo", "real_dns_port")
    assert plugin_ports.echo_http_port() == plugin_ports.config_port("local-echo", "http_port")
    assert plugin_ports.echo_tls_port() == plugin_ports.config_port("local-echo", "tls_port")
    assert plugin_ports.singbox_api_port() == 9090
    assert plugin_ports.singbox_port("ss_port") == plugin_ports.config_port("sing-box", "ss_port")
    assert plugin_ports.xray_readiness_port() == plugin_ports.config_port("xray-core", "readiness_port")


def test_singbox_template_renders_via_singbox_ctl():
    """sing-box 模板必须能被 singbox_ctl 的实际渲染实现完整渲染。"""
    sb = plugin_ports.load_plugin("sing-box")["config"]
    tpl = os.path.join(_PLUGINS_DIR, "sing-box", "configs", "test_server.json")

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "rendered.json")
        singbox_ctl._render_config_to_path(tpl, out)

        cfg = json.loads(_strip_jsonc(open(out, encoding="utf-8").read()))
        inbounds = cfg["inbounds"]
        assert len(inbounds) == 18, f"预期 18 个 inbound，实际 {len(inbounds)}"

        by_tag = {ib["tag"]: ib for ib in inbounds}
        assert by_tag["mixed-in"]["listen_port"] == sb["mixed_port"]
        assert by_tag["ss-in"]["listen_port"] == sb["ss_port"]
        assert by_tag["trojan-in"]["listen_port"] == sb["trojan_port"]
        assert by_tag["hysteria2-in"]["listen_port"] == sb["hysteria2_port"]
        assert by_tag["vless-reality-in"]["listen_port"] == sb["vless_reality_port"]
        # 凭证抽查
        assert by_tag["ss-in"]["password"] == sb["ss_psk"]
        assert by_tag["trojan-in"]["users"][0]["password"] == sb["trojan_password"]
        assert by_tag["vless-in"]["users"][0]["uuid"] == sb["vless_uuid"]
        assert by_tag["shadowtls-in"]["users"][0]["password"] == sb["shadowtls_password"]
        assert by_tag["vless-reality-in"]["tls"]["reality"]["private_key"] == sb["reality_private_key"]


def test_xray_template_placeholder_keys_resolvable():
    """xray-core 模板的每个 __KEY__ 必须能从 plugin.yaml config 解析（zigtester 注入）。"""
    xr = plugin_ports.load_plugin("xray-core")["config"]
    env = {k.upper(): str(v) for k, v in xr.items() if v is not None}
    tpl = os.path.join(_PLUGINS_DIR, "xray-core", "configs", "test_server.json")
    raw = open(tpl, encoding="utf-8").read()
    keys = sorted(set(re.findall(r"__([A-Z][A-Z0-9_]+)__", raw)))
    missing = [k for k in keys if k not in env]
    assert not missing, f"xray 模板占位符在 plugin.yaml 中缺失: {missing}"


def test_plugins_dir_present():
    """真实 plugin.yaml 文件必须存在（真相源完整性）。"""
    assert os.path.isdir(plugin_ports.PLUGINS_ROOT)
    for name in REAL_PLUGINS:
        assert os.path.isfile(plugin_ports.plugin_yaml_path(name))
