"""Zig 0.17 缓存命中态计数失真修复（2026-11-25 kisstun 实锚）。

0.17 cached 态连 run 行都不输出（stdout 仅 "+- compile test safe native
cached 45ms"）——此前 fallthrough ⑧ 记 1/1 passed（计数失真，判定走
exit code 未受影响）。修后：记 cached_run=1，计数保持 0，runner message
显式标注 cached；真计数形态不受影响。
"""

from zigtester.metrics import MetricExtractor

CACHED_STDOUT = """\
Build Summary: 4/4 steps succeeded
+- compile test safe native cached 45ms MaxRSS:31M
   +- options cached
"""


def test_cached_run_flagged_not_1_of_1():
    m = MetricExtractor("zig_test", []).extract(CACHED_STDOUT, 0)
    assert m["cached_run"] == 1.0
    assert m["tests_total"] == 0.0
    assert m["tests_passed"] == 0.0


def test_real_count_still_parses():
    m = MetricExtractor("zig_test", []).extract(
        "+- run test 107 pass, 6 skip (113 total) 7s", 0
    )
    assert m["tests_passed"] == 107.0
    assert m["tests_total"] == 113.0
    assert m["tests_skipped"] == 6.0


def test_uncached_no_count_falls_through_normally():
    # 无 cached 字样、无计数行、exit=0 → 原 ⑧ else 面（1/1 保守记账）
    m = MetricExtractor("zig_test", []).extract("some plain output", 0)
    assert m.get("cached_run", 0) == 0.0
    assert m["tests_passed"] == 1.0
