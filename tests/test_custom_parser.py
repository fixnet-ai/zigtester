"""zt-12：`parser: custom` 一用即崩（TypeError 在 try 之外，整 run 崩）的回归锚。

修前病灶：MetricExtractor._parse_builtin 用 getattr 分派内置解析器，把叠加层
`_parse_custom`（签名不吃 exit_code）也捞进来当基底调用 → `parser: custom`
配置一用，extract() 直接 TypeError，runner 裸调无兜底 → 整 run 崩。
修后语义：custom 是叠加层不是内置基底——`parser: custom` = 指标全部来自
自定义正则；其他 parser = 内置基底 + 自定义正则叠加（extract 的 update 链）。
"""

import pytest

from zigtester.config import MetricDef
from zigtester.metrics import MetricExtractor


PATTERNS = [MetricDef(name="widget_per_sec", pattern=r"WIDGET=([0-9.]+)")]

STDOUT = "WIDGET=42.5\nsome noise line"


def test_custom_parser_does_not_crash():
    """zt-12 主判据：parser=custom 的 extract 不抛 TypeError。"""
    ex = MetricExtractor("custom", PATTERNS)
    metrics = ex.extract(STDOUT, 0)
    assert metrics["widget_per_sec"] == 42.5


def test_custom_parser_has_no_builtin_base():
    """custom 模式无内置基底：不得混入 line_count 系键。"""
    ex = MetricExtractor("custom", PATTERNS)
    metrics = ex.extract(STDOUT, 0)
    assert "line_count" not in metrics
    assert "exit_code" not in metrics
    assert set(metrics) == {"widget_per_sec"}


def test_custom_patterns_still_overlay_builtin():
    """叠加语义回归：line_count 基底 + 自定义正则两组键都在。"""
    ex = MetricExtractor("line_count", PATTERNS)
    metrics = ex.extract(STDOUT, 0)
    assert metrics["line_count"] == 2.0
    assert metrics["exit_code"] == 0.0
    assert metrics["widget_per_sec"] == 42.5


def test_custom_parser_without_patterns_yields_empty():
    """custom 模式且未配 patterns：空指标字典，不崩（诚实于配置）。"""
    ex = MetricExtractor("custom", [])
    assert ex.extract(STDOUT, 0) == {}


def test_unknown_parser_still_falls_back_to_line_count():
    """未知 parser → line_count 兜底语义不变（config 校验外的防御层）。"""
    ex = MetricExtractor("no_such_parser", PATTERNS)
    metrics = ex.extract(STDOUT, 0)
    assert metrics["line_count"] == 2.0
    assert metrics["widget_per_sec"] == 42.5


def test_custom_pattern_bad_capture_is_skipped():
    """捕获组非数字（float 失败）→ 跳过该指标不崩（原有容错回归锚）。"""
    bad = MetricDef(name="broken", pattern=r"WIDGET=([a-z]+)")
    ex = MetricExtractor("custom", PATTERNS + [bad])
    metrics = ex.extract(STDOUT, 0)
    assert metrics == {"widget_per_sec": 42.5}


def test_runner_call_site_contract():
    """runner.py:211 裸调契约（无 try 包裹）：extract 不得抛。"""
    ex = MetricExtractor("custom", PATTERNS)
    # 与 runner 完全同形的双参调用——签名错配类缺陷在此永红。
    assert ex.extract(STDOUT, exit_code=0)["widget_per_sec"] == pytest.approx(42.5)
