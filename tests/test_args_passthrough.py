#!/usr/bin/env python3
"""压测参数透传（工作项 A）单元测试。

覆盖：
- run_project(suite_args=...) 将参数追加到命令尾部、写入 result.suite_args
- 未配对引号触发 shlex ValueError → 回退空格分割，进程仍正常执行
- CLI cmd_run：--args 时跳过保存历史（--no-history 语义扩展）
- CLI cmd_run：--args 与 --all 互斥 / 多项目时报错退出

可独立运行（仅标准库 + 项目源码 + `true` 命令），临时目录自动清理：
    python3 tests/test_args_passthrough.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from zigtester.cli import cmd_run  # noqa: E402
from zigtester.config import parse_config  # noqa: E402
from zigtester.runner import run_project  # noqa: E402
from zigtester.scanner import DiscoveredProject  # noqa: E402

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
        _RESULTS.append((name, True, ""))
    except AssertionError as e:
        _RESULTS.append((name, False, str(e)))
    except Exception as e:  # noqa: BLE001
        _RESULTS.append((name, False, f"异常: {type(e).__name__}: {e}"))


def _write_yaml(tmp: str, project: str) -> str:
    """写一个最小项目配置（unit 层套件 t = true）。"""
    cfg_path = os.path.join(tmp, "zigtester.yaml")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(f'project: {project}\nlevels:\n  unit:\n'
                f'    - name: t\n      command: "true"\n')
    return cfg_path


def _write_echo_project(tmp: str) -> DiscoveredProject:
    """写 echo 参数项目：command = python echoargs.py，stdout 回显参数。"""
    with open(os.path.join(tmp, "echoargs.py"), "w", encoding="utf-8") as f:
        f.write('import sys\nprint("ARGS=" + " ".join(sys.argv[1:]))\n')
    cfg_path = os.path.join(tmp, "zigtester.yaml")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(
            f"project: echo-proj\nsettings:\n  work_dir: \".\"\nlevels:\n"
            f"  unit:\n    - name: echo-args\n"
            f"      command: \"'{sys.executable}' echoargs.py\"\n"
            f"      timeout: 30\n"
        )
    cfg = parse_config(cfg_path)
    return DiscoveredProject(
        name=cfg.project,
        path=tmp,
        config_path=cfg_path,
        config=cfg,
    )


def _status_of(pr, name: str) -> str:
    for s in pr.suites:
        if s.suite_name == name:
            return s.status
    raise AssertionError(f"结果中未找到套件 {name}")


def _stdout_of(pr, name: str) -> str:
    for s in pr.suites:
        if s.suite_name == name:
            return s.stdout
    raise AssertionError(f"结果中未找到套件 {name}")


def _run_namespace(tmp: str, **overrides) -> argparse.Namespace:
    base = dict(
        dir=tmp, no_recursive=True, all=False, project=None,
        args=None, level="unit", fail_fast=False, no_build=True,
        suite=None, report_format="terminal", verbose=False,
        json_output=None, no_history=False, parallel=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


# ── runner 参数透传 ────────────────────────────────────────


def test_suite_args_appended():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _write_echo_project(tmp)
        pr = run_project(proj, levels=["unit"], no_build=True,
                         suite_args="-n 10 --mode socks5")
        assert pr.suite_args == "-n 10 --mode socks5", pr.suite_args
        assert _status_of(pr, "echo-args") == "PASS", pr
        # 命令尾部应含完整参数段（追加到 command 之后）
        out = _stdout_of(pr, "echo-args").strip()
        assert out == "ARGS=-n 10 --mode socks5", out


def test_suite_args_default_none():
    # 不传 suite_args → 命令不含额外参数，result.suite_args 为 None
    with tempfile.TemporaryDirectory() as tmp:
        proj = _write_echo_project(tmp)
        pr = run_project(proj, levels=["unit"], no_build=True)
        assert pr.suite_args is None
        assert _stdout_of(pr, "echo-args").strip() == "ARGS="


def test_suite_args_unbalanced_quotes_fallback():
    # shlex ValueError（未配对引号）→ 回退空格分割，进程仍正常执行（exit 0 → PASS）
    with tempfile.TemporaryDirectory() as tmp:
        proj = _write_echo_project(tmp)
        pr = run_project(proj, levels=["unit"], no_build=True,
                         suite_args='-n "unterminated')
        assert _status_of(pr, "echo-args") == "PASS", pr
        out = _stdout_of(pr, "echo-args").strip()
        # 空格分割：['-n', '"unterminated']
        assert out == 'ARGS=-n "unterminated', out


# ── CLI --args 语义 ────────────────────────────────────────


def test_cli_args_skips_save():
    with tempfile.TemporaryDirectory() as tmp:
        _write_yaml(tmp, "cli-save")
        calls = {"n": 0}

        def fake_save_run(*a, **k):
            calls["n"] += 1

        # --args 非空 → 跳过保存历史
        ns = _run_namespace(tmp, project="cli-save", args="-n 10")
        with mock.patch("zigtester.history.save_run", side_effect=fake_save_run):
            rc = cmd_run(ns)
        assert rc == 0, rc
        assert calls["n"] == 0, calls

        # 对照组：无 --args → 保存 1 次
        ns2 = _run_namespace(tmp, project="cli-save", args=None)
        with mock.patch("zigtester.history.save_run", side_effect=fake_save_run):
            rc2 = cmd_run(ns2)
        assert rc2 == 0, rc2
        assert calls["n"] == 1, calls


def test_cli_all_and_args_mutually_exclusive():
    # --all + --args → 报错退出（决策 D1）
    with tempfile.TemporaryDirectory() as tmp:
        _write_yaml(tmp, "cli-all-args")
        ns = _run_namespace(tmp, all=True, args="-n 10")
        assert cmd_run(ns) == 1


def test_cli_args_requires_single_project():
    # --args + 多项目 → 报错退出（R2：防止多项目路径静默丢弃参数）
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "projA"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "projB"), exist_ok=True)
        _write_yaml(os.path.join(tmp, "projA"), "projA")
        _write_yaml(os.path.join(tmp, "projB"), "projB")
        ns = _run_namespace(tmp, no_recursive=False, args="-n 10")
        assert cmd_run(ns) == 1


def main() -> int:
    checks = [
        ("runner — 参数追加到命令尾部 + 写入 suite_args", test_suite_args_appended),
        ("runner — 默认 suite_args 为 None", test_suite_args_default_none),
        ("runner — 未配对引号回退空格分割", test_suite_args_unbalanced_quotes_fallback),
        ("cli — --args 跳过保存历史", test_cli_args_skips_save),
        ("cli — --args 与 --all 互斥", test_cli_all_and_args_mutually_exclusive),
        ("cli — --args 要求单项目", test_cli_args_requires_single_project),
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
