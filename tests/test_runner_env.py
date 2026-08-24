#!/usr/bin/env python3
"""套件命令串 ${VAR} 插件 env 注入单元测试（zt-2 遗留①收敛）。

验证：
  1. _load_plugin_envs 从 plugins/*/plugin.yaml config 段派生 PLUGIN_* 通用名 +
     插件限定名（SINGBOX_/XRAY_/LOCALECHO_）。
  2. _build_cmd 对命令串 ${VAR} 正确替换（含 sh -c 多段场景）。
  3. config._env_subst 保留未定义 VAR（延迟到 runner），os.environ 定义 VAR 正常替换。
  4. _build_env suite.env 显式值优先（setdefault 不覆盖）。

运行（zigtester venv）：
    .venv/bin/python -m pytest tests/test_runner_env.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zigtester.config import _env_subst, SuiteConfig  # noqa: E402
from zigtester.runner import _build_cmd, _build_env, _load_plugin_envs  # noqa: E402


def test_load_plugin_envs_generates_qualified_names():
    """插件 config 段派生通用 PLUGIN_* 与插件限定 SINGBOX_*/XRAY_*/LOCALECHO_*。"""
    env = _load_plugin_envs()
    assert env["PLUGIN_MIXED_PORT"] == "2080"  # sing-box
    # 跨插件同名 key：限定名各归其位，通用名取后遍历者（xray）
    assert env["SINGBOX_VLESS_REALITY_PORT"] == "16812"
    assert env["XRAY_VLESS_REALITY_PORT"] == "16906"
    assert env["XRAY_REALITY_SHORT_ID"] == "0123abcd"
    assert env["LOCALECHO_BENCH_PORT"] == "13337"


def test_build_cmd_substitutes_plugin_env():
    """命令串 ${PLUGIN_<KEY>} 替换为 plugin.yaml 派生值。"""
    suite = SuiteConfig(
        name="xray-reality-verify",
        command=(
            "go -C tests/tools/xray-reality-verify run . "
            "-server 127.0.0.1:${XRAY_VLESS_REALITY_PORT} "
            "-uuid ${XRAY_VLESS_UUID} "
            "-short-id ${XRAY_REALITY_SHORT_ID}"
        ),
        timeout=90,
    )
    parts = _build_cmd(suite)
    assert "127.0.0.1:16906" in parts
    assert "e8b9a5b8-7c8a-4a87-b3f1-2e5d4a7c9b1a" in parts
    assert "0123abcd" in parts


def test_build_cmd_sh_c_compound():
    """sh -c 多段命令串（reality-verify 三场景）整体 ${VAR} 替换。"""
    suite = SuiteConfig(
        name="reality-verify",
        command=(
            'sh -c "go run . -scenario reality -server 127.0.0.1:${SINGBOX_VLESS_REALITY_PORT} '
            '-uuid ${SINGBOX_VLESS_UUID} && '
            'go run . -scenario tls-vision -server 127.0.0.1:${XRAY_VLESS_FLOW_PORT} && '
            'go run . -scenario tls-plain -server 127.0.0.1:${XRAY_VLESS_TLS_PORT}"'
        ),
        timeout=90,
    )
    parts = _build_cmd(suite)
    joined = " ".join(parts)
    assert "127.0.0.1:16812" in joined
    assert "387b71a7-15ce-4ee0-a791-ab5a08aaa9dc" in joined
    assert "127.0.0.1:16908" in joined
    assert "127.0.0.1:16901" in joined


def test_env_subst_preserves_undefined_var():
    """_env_subst 对未定义 ${VAR} 保留原样（待 runner 二次替换），os.environ 定义值正常替换。"""
    assert _env_subst("a ${NOT_DEFINED_VAR_XYZ} b") == "a ${NOT_DEFINED_VAR_XYZ} b"
    os.environ["ZIGBOX_TEST_ENV_1"] = "hello"
    try:
        assert _env_subst("a ${ZIGBOX_TEST_ENV_1} b") == "a hello b"
    finally:
        del os.environ["ZIGBOX_TEST_ENV_1"]


def test_build_env_suite_env_priority():
    """suite.env 显式值优先于插件注入（setdefault 不覆盖）。"""
    suite = SuiteConfig(
        name="t",
        command="echo ${SINGBOX_VLESS_REALITY_PORT}",
        timeout=60,
        env={"SINGBOX_VLESS_REALITY_PORT": "99999"},
    )
    env = _build_env(suite)
    assert env["SINGBOX_VLESS_REALITY_PORT"] == "99999"
    # 未显式覆盖的键保留插件派生值
    assert env["XRAY_VLESS_REALITY_PORT"] == "16906"
