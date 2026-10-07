#!/usr/bin/env python3
"""zt-10 构建门禁单元测试。

旧实现（09-25 zigbox H3 挖出，10-07 修复）：_run_build 丢弃 returncode +
except-pass ⇒ 编译失败时旧二进制照跑、套件仍报 PASS。修复后构建失败/超时
raise BuildFailure（附 stderr 尾部），run_project 把全部套件记 ERROR。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import pytest  # noqa: E402

from zigtester.config import ProjectConfig, ProjectSettings  # noqa: E402
from zigtester.runner import (  # noqa: E402
    BuildFailure,
    ProjectResult,
    _abort_with_build_error,
    _run_build,
)

FAIL_CMD = (
    "python3 -c \"import sys; print('compile-error-line-1', file=sys.stderr); "
    "print('compile-error-line-2', file=sys.stderr); sys.exit(2)\""
)


def test_run_build_failure_raises_with_stderr_tail(tmp_path):
    with pytest.raises(BuildFailure) as ei:
        _run_build(FAIL_CMD, str(tmp_path))
    msg = str(ei.value)
    assert "rc=2" in msg
    assert "compile-error-line-2" in msg  # stderr 尾部必须进消息（不再是黑洞）


def test_run_build_success_is_silent(tmp_path):
    _run_build("python3 -c \"print('ok')\"", str(tmp_path))  # 不抛即通过


def test_run_build_timeout_raises_not_swallows(tmp_path):
    # zt-11-⑥（与 zt-10 同段代码）：构建超时不再被 except-pass 静默吃掉
    with pytest.raises(BuildFailure, match="超时"):
        _run_build("python3 -c \"import time; time.sleep(5)\"", str(tmp_path), timeout=1)


def test_abort_with_build_error_marks_all_suites():
    cfg = ProjectConfig(
        project="demo",
        settings=ProjectSettings(),
        levels={
            "unit": _level(["u1", "u2"]),
            "functional": _level(["f1"]),
        },
    )
    result = ProjectResult(project="demo", path="/tmp/demo", started_at=0.0)
    _abort_with_build_error(result, cfg, ["unit", "functional"], "构建失败 rc=2：zig build")

    statuses = [(s.suite_name, s.status) for s in result.suites]
    assert statuses == [
        ("u1", "ERROR"), ("u2", "SKIP"), ("f1", "SKIP"),
    ]
    first = result.suites[0]
    assert "zt-10" in first.message
    assert "构建失败 rc=2" in (first.setup_error or "")
    assert all("陈旧" in s.message for s in result.suites[1:])


def _level(names):
    from zigtester.config import LevelConfig, SuiteConfig

    return LevelConfig(suites=[
        SuiteConfig(name=n, command="true", timeout=10) for n in names
    ])
