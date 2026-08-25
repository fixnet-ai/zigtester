#!/usr/bin/env python3
"""zigtester 报告与历史改进单元测试。

覆盖 reporter.py 的 extract_failure_lines、history.py 的
detect_flaky / check_regression 基线过滤。

可独立运行（仅标准库 + 项目源码），不留临时文件和进程：
    python3 tests/test_report_history.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from zigtester.config import (  # noqa: E402
    ProjectResult,
    Regression,
    ResourceSnapshot,
    SuiteResult,
)
from zigtester.history import check_regression, detect_flaky  # noqa: E402
from zigtester.reporter import Reporter, extract_failure_lines  # noqa: E402

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
        _RESULTS.append((name, True, ""))
    except AssertionError as e:
        _RESULTS.append((name, False, str(e)))
    except Exception as e:  # noqa: BLE001
        _RESULTS.append((name, False, f"异常: {type(e).__name__}: {e}"))


# ── extract_failure_lines ───────────────────────────────────


def test_extract_basic():
    text = "ok line\n✗ 用例A 失败\nall good\nFAIL 用例B\nERROR: boom\n"
    lines = extract_failure_lines(text)
    assert lines == ["✗ 用例A 失败", "FAIL 用例B", "ERROR: boom"], lines


def test_extract_ansi_stripped():
    text = "\x1b[31m✗ 用例A 失败\x1b[0m\nplain\n"
    lines = extract_failure_lines(text)
    assert lines == ["✗ 用例A 失败"], lines
    assert "\x1b" not in lines[0]


def test_extract_limit():
    text = "\n".join(f"✗ 用例{i}" for i in range(20))
    lines = extract_failure_lines(text, limit=5)
    assert len(lines) == 5, lines
    assert lines[0] == "✗ 用例0"


def test_extract_truncate_160():
    text = "✗ " + "x" * 300
    lines = extract_failure_lines(text)
    assert len(lines) == 1
    assert len(lines[0]) == 160, len(lines[0])


def test_extract_empty():
    assert extract_failure_lines("") == []
    assert extract_failure_lines("no failure here\nall fine\n") == []


# ── detect_flaky ────────────────────────────────────────────


def _recs(statuses: list[str]) -> list[dict]:
    return [{"status": s} for s in statuses]


def test_flaky_two_flips():
    # PASS → FAIL → PASS → FAIL：翻转 3 次，flaky
    assert detect_flaky(_recs(["FAIL", "PASS", "FAIL", "PASS"])) is True


def test_flaky_stable_pass():
    assert detect_flaky(_recs(["PASS"] * 8)) is False


def test_flaky_stable_fail():
    assert detect_flaky(_recs(["FAIL"] * 8)) is False


def test_flaky_insufficient_records():
    assert detect_flaky(_recs(["PASS", "FAIL", "PASS"])) is False


def test_flaky_one_flip():
    # 只翻转 1 次（先失败后一直通过），不算 flaky
    assert detect_flaky(_recs(["PASS", "PASS", "PASS", "FAIL"])) is False


def test_flaky_skip_ignored():
    # SKIP 不参与翻转计数
    assert detect_flaky(_recs(["FAIL", "SKIP", "PASS", "SKIP", "FAIL"])) is True


# ── check_regression 基线过滤 ───────────────────────────────


def test_regression_filters_fail_records():
    # FAIL 记录指标异常（throughput=1），不应拉低基线
    history = [
        {"status": "PASS", "metrics": {"throughput": 100}},
        {"status": "FAIL", "metrics": {"throughput": 1}},
        {"status": "PASS", "metrics": {"throughput": 100}},
    ]
    regs = check_regression({"throughput": 100}, history)
    assert regs == [], [str(r) for r in regs]


def test_regression_without_status_field_compatible():
    # 无 status 字段的旧格式记录保持兼容（不过滤）
    history = [
        {"metrics": {"throughput": 100}},
        {"metrics": {"throughput": 100}},
    ]
    regs = check_regression({"throughput": 50}, history)
    # 吞吐下降 50% > 20% 阈值，应报退化（说明基线用了无 status 记录）
    assert len(regs) == 1, regs
    assert regs[0].metric == "throughput"


def test_regression_detects_drop():
    history = [{"status": "PASS", "metrics": {"throughput": 100}}] * 3
    regs = check_regression({"throughput": 50}, history)
    assert len(regs) == 1, regs


def test_regression_all_fail_no_baseline():
    history = [{"status": "FAIL", "metrics": {"throughput": 100}}] * 3
    assert check_regression({"throughput": 100}, history) == []


def test_regression_duration_normalized_no_false_positive():
    # 40s→10s：success_count 总量随时长减半，但速率一致 → 不应误报退化
    history = [
        {"status": "PASS", "metrics": {"success_count": 2400, "duration_s": 40}},
        {"status": "PASS", "metrics": {"success_count": 2400, "duration_s": 40}},
        {"status": "PASS", "metrics": {"success_count": 2400, "duration_s": 40}},
    ]
    regs = check_regression({"success_count": 600, "duration_s": 10}, history)
    assert regs == [], [str(r) for r in regs]


def test_regression_duration_normalized_real_drop():
    # 相同 duration，success_count 总量真降（=速率真降）→ 仍报退化
    history = [
        {"status": "PASS", "metrics": {"success_count": 1000, "duration_s": 10}},
        {"status": "PASS", "metrics": {"success_count": 1000, "duration_s": 10}},
        {"status": "PASS", "metrics": {"success_count": 1000, "duration_s": 10}},
    ]
    regs = check_regression({"success_count": 500, "duration_s": 10}, history)
    assert len(regs) == 1, regs
    assert regs[0].metric == "success_count"


def test_regression_duration_missing_backward_compatible():
    # 旧记录无 duration_s → 不归一化，按原值对比（保持既有行为）
    history = [
        {"status": "PASS", "metrics": {"success_count": 1000}},
        {"status": "PASS", "metrics": {"success_count": 1000}},
    ]
    regs = check_regression({"success_count": 500}, history)
    assert len(regs) == 1, regs


def test_regression_latency_sub_ms_noise_ignored():
    # 亚毫秒级延迟差异（0.001→0.005ms = +400%）是测量噪声，不应报回归
    history = [
        {"status": "PASS", "metrics": {"latency_p99_ms": 0.001}},
        {"status": "PASS", "metrics": {"latency_p99_ms": 0.001}},
        {"status": "PASS", "metrics": {"latency_p99_ms": 0.001}},
    ]
    regs = check_regression({"latency_p99_ms": 0.005}, history)
    assert regs == [], [str(r) for r in regs]


def test_regression_latency_real_drop_still_detected():
    # 毫秒级延迟真实退化（1.0→2.0ms = +100%）仍应报回归
    history = [
        {"status": "PASS", "metrics": {"latency_p99_ms": 1.0}},
        {"status": "PASS", "metrics": {"latency_p99_ms": 1.0}},
        {"status": "PASS", "metrics": {"latency_p99_ms": 1.0}},
    ]
    regs = check_regression({"latency_p99_ms": 2.0}, history)
    assert len(regs) == 1, regs
    assert regs[0].metric == "latency_p99_ms"


def test_regression_short_duration_excluded_from_baseline():
    # 短测量（duration_ms < 2s）吞吐虚高，应排除出基线：
    # 854 万（1035ms 短测量）剔除后，基线 48.1 万，当前 55.7 万 = +15.8% 不报退化
    history = [
        {"status": "PASS", "duration_ms": 18000, "metrics": {"throughput_reqs_per_sec": 480000}},
        {"status": "PASS", "duration_ms": 17500, "metrics": {"throughput_reqs_per_sec": 482000}},
        {"status": "PASS", "duration_ms": 1035, "metrics": {"throughput_reqs_per_sec": 8540000}},
    ]
    regs = check_regression({"throughput_reqs_per_sec": 557000}, history)
    assert regs == [], [str(r) for r in regs]


def test_regression_duration_missing_kept_in_baseline():
    # duration_ms 缺失（None）视为「无时长信息」→ 保留，不误剔除（向后兼容）
    history = [
        {"status": "PASS", "metrics": {"throughput": 100}},
        {"status": "PASS", "metrics": {"throughput": 100}},
    ]
    regs = check_regression({"throughput": 50}, history)
    assert len(regs) == 1, regs


def test_regression_stale_record_excluded_from_baseline():
    # 超过 7 天的旧记录（可能经历代码/参数/测量方式变更）排除出基线：
    # 08-14 单轮测量 338 万 req/s 混入会误判 79 万 ↓66%，窗口过滤后基线纯近期数据不报
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(days=1)).isoformat()
    stale_ts = (now - timedelta(days=10)).isoformat()
    history = [
        {"status": "PASS", "timestamp": recent_ts, "duration_ms": 83000,
         "metrics": {"throughput_reqs_per_sec": 775000}},
        {"status": "PASS", "timestamp": recent_ts, "duration_ms": 85000,
         "metrics": {"throughput_reqs_per_sec": 745000}},
        {"status": "PASS", "timestamp": stale_ts, "duration_ms": 19559,
         "metrics": {"throughput_reqs_per_sec": 3380000}},
    ]
    regs = check_regression({"throughput_reqs_per_sec": 797000}, history)
    # 基线 = (775000 + 745000) / 2 = 760000，当前 797000 = +4.9% → 不报退化
    assert regs == [], [str(r) for r in regs]


# ── compact_markdown（run 回归节 / 非标准参数标注）────────────


def _make_project_result(suite_args=None) -> ProjectResult:
    """构造一个含 PASS + 非空 metrics 的 performance 套件结果。"""
    suite = SuiteResult(
        suite_name="bench-socks5",
        level="performance",
        status="PASS",
        duration_ms=120.0,
        exit_code=0,
        metrics={"p99_ms": 200.0},
        message="p99=200.0ms",
        resource_peak=ResourceSnapshot(sample_count=0),
    )
    return ProjectResult(
        project="test-proj",
        path="/tmp",
        suites=[suite],
        started_at=0.0,
        finished_at=1.0,
        suite_args=suite_args,
    )


def test_compact_markdown_regression_section():
    result = _make_project_result()
    regs = {"bench-socks5": [Regression("p99_ms", 200.0, 90.0, 122.2, True)]}
    md = Reporter.compact_markdown(result, regressions=regs)
    assert "回归检测" in md, md
    assert "p99_ms" in md, md
    assert "基线" in md, md
    assert "bench-socks5" in md, md
    assert "122.2%" in md, md


def test_compact_markdown_regression_empty_dict():
    # 空 dict = 已分析且无退化 → 渲染 ✓
    result = _make_project_result()
    md = Reporter.compact_markdown(result, regressions={})
    assert "回归检测" in md, md
    assert "✓ 未检测到性能退化" in md, md


def test_compact_markdown_regression_none_not_rendered():
    # None = 未启用回归对比 → 不渲染该节
    result = _make_project_result()
    md = Reporter.compact_markdown(result, regressions=None)
    assert "回归检测" not in md, md


def test_compact_markdown_suite_args_warning():
    md = Reporter.compact_markdown(_make_project_result(suite_args="-n 10"))
    assert "非标准参数" in md, md
    assert "-n 10" in md, md

    md_plain = Reporter.compact_markdown(_make_project_result())
    assert "非标准参数" not in md_plain, md_plain


# ── compact_history（固定历史报表）──────────────────────────


def test_compact_history_empty():
    # records 空 → 「无历史记录」，不访问 records[0]（防 IndexError）
    md = Reporter.compact_history("proj", "bench-socks5", [], [], False)
    assert "无历史记录" in md, md


def test_compact_history_single():
    records = [{
        "timestamp": "2026-08-25T10:00:00+08:00",
        "status": "PASS",
        "duration_ms": 100.0,
        "metrics": {"p99_ms": 90.0},
    }]
    md = Reporter.compact_history("proj", "bench-socks5", records, [], False)
    assert "| 时间 | 状态 | 耗时 | 指标 |" in md, md
    assert "2026-08-25" in md, md
    assert "p99_ms=90.0" in md, md
    assert "✓ 未检测到性能退化" in md, md
    # 指标排除 exit_code
    rec2 = [dict(records[0], metrics={"p99_ms": 90.0, "exit_code": 0})]
    md2 = Reporter.compact_history("proj", "s", rec2, [], False)
    assert "exit_code=0" not in md2, md2


def test_compact_history_single_regression_red_line():
    records = [{
        "timestamp": "2026-08-25T10:00:00+08:00",
        "status": "PASS",
        "duration_ms": 100.0,
        "metrics": {"p99_ms": 90.0},
    }]
    regs = [Regression("p99_ms", 200.0, 90.0, 122.2, True)]
    md = Reporter.compact_history("proj", "bench-socks5", records, regs, False)
    assert "回归检测" in md, md
    assert "🔴" in md, md
    assert "基线" in md, md


def test_compact_history_group():
    group_records = {"bench-tcp-direct": [{
        "timestamp": "2026-08-25T10:00:00+08:00",
        "status": "PASS",
        "duration_ms": 100.0,
        "metrics": {"p99_ms": 90.0},
    }]}
    md = Reporter.compact_history(
        "proj", "direct", [], {}, {}, group_records=group_records
    )
    # 成员表头 + 模式前缀消歧 + 无退化
    assert "| 套件 | 时间 | 状态 | 耗时 | 指标 |" in md, md
    assert "tcp:p99_ms=90.0" in md, md
    assert "✓ 未检测到性能退化" in md, md


def test_compact_history_group_flaky_member():
    group_records = {"bench-tcp-direct": [{
        "timestamp": "2026-08-25T10:00:00+08:00",
        "status": "PASS",
        "duration_ms": 100.0,
        "metrics": {"p99_ms": 90.0},
    }]}
    md = Reporter.compact_history(
        "proj", "direct", [], {}, {"bench-tcp-direct": True},
        group_records=group_records,
    )
    assert "flaky" in md, md


def main() -> int:
    checks = [
        ("extract — 基本提取", test_extract_basic),
        ("extract — 剥离 ANSI 色码", test_extract_ansi_stripped),
        ("extract — 行数上限", test_extract_limit),
        ("extract — 160 字符截断", test_extract_truncate_160),
        ("extract — 空输入/无匹配", test_extract_empty),
        ("flaky — 翻转 2 次以上为 True", test_flaky_two_flips),
        ("flaky — 稳定 PASS 为 False", test_flaky_stable_pass),
        ("flaky — 稳定 FAIL 为 False", test_flaky_stable_fail),
        ("flaky — 记录不足 4 条为 False", test_flaky_insufficient_records),
        ("flaky — 仅 1 次翻转为 False", test_flaky_one_flip),
        ("flaky — SKIP 不参与翻转", test_flaky_skip_ignored),
        ("regression — 基线过滤 FAIL 记录", test_regression_filters_fail_records),
        ("regression — 无 status 字段兼容", test_regression_without_status_field_compatible),
        ("regression — 检测吞吐下降", test_regression_detects_drop),
        ("regression — 全 FAIL 无基线", test_regression_all_fail_no_baseline),
        ("regression — 总量指标 duration 归一化不误报", test_regression_duration_normalized_no_false_positive),
        ("regression — 总量指标真降仍检测", test_regression_duration_normalized_real_drop),
        ("regression — 无 duration 旧记录兼容", test_regression_duration_missing_backward_compatible),
        ("regression — 亚毫秒延迟噪声忽略", test_regression_latency_sub_ms_noise_ignored),
        ("regression — 毫秒级延迟真退化仍检测", test_regression_latency_real_drop_still_detected),
        ("regression — 短 duration 排除出基线", test_regression_short_duration_excluded_from_baseline),
        ("regression — duration 缺失保留基线", test_regression_duration_missing_kept_in_baseline),
        ("regression — 陈旧记录排除出基线", test_regression_stale_record_excluded_from_baseline),
        ("compact_markdown — 回归节渲染", test_compact_markdown_regression_section),
        ("compact_markdown — 空 dict 渲染 ✓", test_compact_markdown_regression_empty_dict),
        ("compact_markdown — None 不渲染", test_compact_markdown_regression_none_not_rendered),
        ("compact_markdown — 非标准参数标注", test_compact_markdown_suite_args_warning),
        ("compact_history — 空历史无 IndexError", test_compact_history_empty),
        ("compact_history — 单套件表", test_compact_history_single),
        ("compact_history — 单套件回归红字", test_compact_history_single_regression_red_line),
        ("compact_history — 组视图分表 + 模式前缀", test_compact_history_group),
        ("compact_history — 组视图 flaky 成员", test_compact_history_group_flaky_member),
    ]
    for name, fn in checks:
        check(name, fn)

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    failed = len(_RESULTS) - passed
    print(f"\n总计 {len(_RESULTS)} | 通过 {passed} | 失败 {failed}")
    for name, ok, msg in _RESULTS:
        if not ok:
            print(f"  FAIL {name}: {msg}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
