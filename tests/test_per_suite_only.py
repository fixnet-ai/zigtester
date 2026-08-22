#!/usr/bin/env python3
"""per_suite_only 字段单元测试。

覆盖 config.py 解析 + runner.py 的 level 全量跳过语义：
- 全量执行（suite_filter=None）→ per_suite_only 套件 SKIP，普通套件正常跑
- --suite 显式指定 → per_suite_only 套件正常执行

可独立运行（仅标准库 + 项目源码 + `true` 命令），临时目录自动清理：
    python3 tests/test_per_suite_only.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from zigtester.config import parse_config  # noqa: E402
from zigtester.runner import run_project  # noqa: E402
from zigtester.scanner import DiscoveredProject  # noqa: E402

_RESULTS: list[tuple[str, bool, str]] = []

_YAML = """\
project: per-suite-test
settings:
  work_dir: "."
levels:
  performance:
    - name: only-bench
      command: "true"
      per_suite_only: true
    - name: normal-bench
      command: "true"
"""


def check(name: str, fn) -> None:
    try:
        fn()
        _RESULTS.append((name, True, ""))
    except AssertionError as e:
        _RESULTS.append((name, False, str(e)))
    except Exception as e:  # noqa: BLE001
        _RESULTS.append((name, False, f"异常: {type(e).__name__}: {e}"))


def _load_project(tmp: str) -> DiscoveredProject:
    cfg_path = os.path.join(tmp, "zigtester.yaml")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(_YAML)
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


def _message_of(pr, name: str) -> str:
    for s in pr.suites:
        if s.suite_name == name:
            return s.message
    raise AssertionError(f"结果中未找到套件 {name}")


# ── config 解析 ────────────────────────────────────────────


def test_config_parses_field():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _load_project(tmp)
        suites = {s.name: s for s in proj.config.levels["performance"].suites}
        assert suites["only-bench"].per_suite_only is True
        assert suites["normal-bench"].per_suite_only is False


def test_config_default_false():
    # 未声明 per_suite_only 的旧配置保持默认 False（向后兼容）
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = os.path.join(tmp, "zigtester.yaml")
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write('project: old\nlevels:\n  unit:\n    - name: t\n      command: "true"\n')
        cfg = parse_config(cfg_path)
        assert cfg.levels["unit"].suites[0].per_suite_only is False


# ── runner 全量执行（--level，无 --suite）────────────────────


def test_level_full_skips_per_suite_only():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _load_project(tmp)
        pr = run_project(proj, levels=["performance"], no_build=True)
        assert _status_of(pr, "only-bench") == "SKIP", pr
        assert "per_suite_only" in _message_of(pr, "only-bench"), pr
        # 普通套件不受影响，正常执行
        assert _status_of(pr, "normal-bench") == "PASS", pr


# ── runner --suite 显式运行 ────────────────────────────────


def test_suite_filter_runs_per_suite_only():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _load_project(tmp)
        pr = run_project(proj, levels=["performance"], no_build=True,
                         suite_filter="only-bench")
        # --suite 显式指定 → per_suite_only 套件真实执行（exit 0 → PASS）
        assert _status_of(pr, "only-bench") == "PASS", pr


def main() -> int:
    checks = [
        ("config — 解析 per_suite_only 字段", test_config_parses_field),
        ("config — 未声明默认 False", test_config_default_false),
        ("runner — level 全量跳过 per_suite_only", test_level_full_skips_per_suite_only),
        ("runner — --suite 正常运行 per_suite_only", test_suite_filter_runs_per_suite_only),
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
