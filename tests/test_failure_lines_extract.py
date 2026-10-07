#!/usr/bin/env python3
"""failure_lines 提取修复单测（排期视图 #15：all-tests 失败可命名）。

三层断点（2026-10-07 修复）：
  ① 提取器只喂 stdout —— zig 0.16 exit=1 时 stdout 全空、失败命名行在 stderr
  ② 记号大小写不符 —— 旧标记 "ERROR:"/“FAIL”，zig 实际打小写 error: '<名>' failed
  ③ stderr_tail 500 字符截尾

样本取自本机 zig 0.16.0 真实失败输出（zig build test --summary all，exit=1）。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zigtester.reporter import combined_text, extract_failure_lines  # noqa: E402

# 真实 stderr（节选，保留原始行形）：stdout 为空
ZIG_REAL_STDERR = """\
test
+- run test 2 pass, 1 fail (3 total)
error: 'main.test.will fail' failed:
       expected 1, found 2
       /opt/homebrew/Cellar/zig/0.16.0_1/lib/zig/std/testing.zig:118:17: 0x1029118a7 in expectEqualInner__anon_36279 (test)
       /Users/x/tmp/zigfail2/src/main.zig:73:20: 0x102911977 in test.will fail (test)
       test "will fail" { try std.testing.expectEqual(@as(u8, 1), 2); }
                          ^
failed command: ./.zig-cache/o/0d24e99e83142262873512f9caf1cde4/test --cache-dir=./.zig-cache --seed=0x3883b196 --listen=-

Build Summary: 3/5 steps succeeded (1 failed); 3/4 tests passed (1 failed)
test transitive failure

error: the following build command failed with exit code 1:
.zig-cache/o/1e8d613f221727a8df7c4fd13bfbe4d2/build /opt/homebrew/bin/zig test --summary all
"""


def test_zig_failure_named_from_stderr_only():
    """断点①+②：stdout 全空、失败行在 stderr 且为小写 error: —— 必须命名出用例。"""
    lines = extract_failure_lines(combined_text("", ZIG_REAL_STDERR))
    assert any("'main.test.will fail' failed" in ln for ln in lines), lines
    # 紧规则负例：构建命令失败行 / failed command 行不进 failure_lines
    assert not any("build command failed" in ln for ln in lines), lines
    assert not any(ln.startswith("failed command") for ln in lines), lines


def test_leaked_variant_matches():
    lines = extract_failure_lines("error: 'src/subs.zig:90:12: mock leak test' leaked\n")
    assert len(lines) == 1 and "leaked" in lines[0]


def test_old_markers_still_work():
    """既有标记（python/自研脚本）不回退。"""
    lines = extract_failure_lines("some py output\nFAILED tests/test_x.py::test_y - assert 1 == 2\n")
    assert len(lines) == 1 and "test_y" in lines[0]
    lines = extract_failure_lines("xxx\nERROR: boom\n")
    assert lines == ["ERROR: boom"]


def test_combined_text_order_and_empty():
    assert combined_text("out", "err") == "out\nerr"
    assert combined_text("", "err") == "err"
    assert combined_text("out", "") == "out"
    assert combined_text(None, None) == ""
