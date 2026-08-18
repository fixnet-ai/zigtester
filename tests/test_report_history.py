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

from zigtester.history import check_regression, detect_flaky  # noqa: E402
from zigtester.reporter import extract_failure_lines  # noqa: E402

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
