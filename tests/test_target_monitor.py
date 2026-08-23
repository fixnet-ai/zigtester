#!/usr/bin/env python3
"""target 字段（资源采集目标进程）单元测试。

覆盖 config.py 解析 + monitor.py 目标筛选：
- yaml 声明 target → SuiteConfig.target 正确解析
- 未声明 target → 只采命令进程本身（root），不累加子进程
- 声明 target 且匹配 → 只采匹配进程（排除命令进程包装）
- 声明 target 但树中无匹配 → 跳过采样（空快照），不采命令进程包装
- 目标进程延迟出现 → 启动期跳过，出现后只采目标

可独立运行（仅标准库 + 项目源码 + psutil）：
    python3 tests/test_target_monitor.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from zigtester.config import parse_config  # noqa: E402
from zigtester.monitor import ResourceMonitor  # noqa: E402

_RESULTS: list[tuple[str, bool, str]] = []

_YAML_WITH_TARGET = """\
project: target-test
settings:
  work_dir: "."
levels:
  performance:
    - name: bench-long-fake
      command: "python3 tests/test_engine/test_engine.py --proto direct"
      target: test-engine
    - name: plain
      command: "zig build test"
"""


def check(name: str, fn) -> None:
    try:
        fn()
        _RESULTS.append((name, True, ""))
    except AssertionError as e:
        _RESULTS.append((name, False, str(e)))
    except Exception as e:  # noqa: BLE001
        _RESULTS.append((name, False, f"异常: {type(e).__name__}: {e}"))


def _sample(mon: ResourceMonitor) -> None:
    """采样足够时长（≥2 个有效样本，跳过首样本后）。"""
    time.sleep(0.35)


# ── config 解析 ────────────────────────────────────────────

def test_config_target_parsed() -> None:
    tmp = tempfile.mkdtemp()
    try:
        cfg_path = os.path.join(tmp, "zigtester.yaml")
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(_YAML_WITH_TARGET)
        cfg = parse_config(cfg_path)
        suites = cfg.levels["performance"].suites
        by_name = {s.name: s for s in suites}
        assert by_name["bench-long-fake"].target == "test-engine", \
            f"target 应解析为 test-engine, 实际 {by_name['bench-long-fake'].target!r}"
        assert by_name["plain"].target is None, \
            "未声明 target 的套件应为 None"
    finally:
        shutil.rmtree(tmp)


# ── monitor 目标筛选（真实进程树）───────────────────────────

def test_monitor_no_target_root_only() -> None:
    """未声明 target → 只采命令进程（root），排除子进程 sleep。"""
    if psutil is None:
        return
    child = subprocess.Popen(["/bin/sleep", "3"])
    try:
        mon = ResourceMonitor()
        mon.start(os.getpid(), interval_s=0.05, target=None)
        _sample(mon)
        snap = mon.stop()
        # root 是当前 python 测试进程（import 了 psutil 等），rss 应明显大于 sleep
        assert snap.peak_memory_mb > 5, \
            f"未声明 target 应采命令进程本身(rss>5MB), 实际 {snap.peak_memory_mb}MB"
    finally:
        child.terminate()
        child.wait()


def test_monitor_target_match_only_target() -> None:
    """声明 target=sleep → 只采 sleep 进程，排除 python 命令进程包装。"""
    if psutil is None:
        return
    child = subprocess.Popen(["/bin/sleep", "3"])
    try:
        # target=sleep: 只采子进程 sleep（rss 极小）
        mon = ResourceMonitor()
        mon.start(os.getpid(), interval_s=0.05, target="sleep")
        _sample(mon)
        snap_target = mon.stop()
        # 未声明: 采命令进程本身（python 测试进程，rss 明显更大）
        mon2 = ResourceMonitor()
        mon2.start(os.getpid(), interval_s=0.05, target=None)
        _sample(mon2)
        snap_root = mon2.stop()

        assert snap_target.peak_memory_mb < 10, \
            f"target=sleep 应只采 sleep(rss<10MB), 实际 {snap_target.peak_memory_mb}MB"
        assert snap_target.peak_memory_mb < snap_root.peak_memory_mb, \
            f"target 版本({snap_target.peak_memory_mb}MB)应小于 root 版本({snap_root.peak_memory_mb}MB)"
    finally:
        child.terminate()
        child.wait()


def test_monitor_target_missing_skip_samples() -> None:
    """声明 target 但树中无匹配 → 跳过采样（空快照），不采命令进程包装。"""
    if psutil is None:
        return
    mon = ResourceMonitor()
    mon.start(os.getpid(), interval_s=0.05, target="no-such-process-xyz")
    _sample(mon)
    snap = mon.stop()
    assert snap.sample_count == 0, \
        f"target 无匹配应跳过采样(空快照), 实际采了 {snap.sample_count} 个样本"
    assert snap.peak_memory_mb == 0, "target 无匹配应空快照(peak=0)"


def test_monitor_target_late_start_skips_wrapper() -> None:
    """目标进程在 monitor 启动后才出现：启动期不采命令进程包装，出现后只采目标。"""
    if psutil is None:
        return
    mon = ResourceMonitor()
    mon.start(os.getpid(), interval_s=0.05, target="sleep")
    # monitor 启动后 0.2s 才拉起 sleep 子进程（模拟目标进程延迟出现）
    time.sleep(0.2)
    child = subprocess.Popen(["/bin/sleep", "3"])
    try:
        _sample(mon)
        snap = mon.stop()
        assert snap.sample_count > 0, "目标进程出现后应采到样本"
        assert snap.peak_memory_mb < 10, \
            f"应只采 sleep(rss<10MB), 实际 {snap.peak_memory_mb}MB（混入命令进程包装?）"
    finally:
        child.terminate()
        child.wait()


if __name__ == "__main__":
    check("config: target 字段解析", test_config_target_parsed)
    check("monitor: 未声明 target 只采 root", test_monitor_no_target_root_only)
    check("monitor: target 匹配只采目标进程", test_monitor_target_match_only_target)
    check("monitor: target 无匹配跳过采样", test_monitor_target_missing_skip_samples)
    check("monitor: 目标延迟出现只采目标", test_monitor_target_late_start_skips_wrapper)

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    failed = len(_RESULTS) - passed
    print(f"\n总计 {len(_RESULTS)} | 通过 {passed} | 失败 {failed}")
    for name, ok, err in _RESULTS:
        status = "✅" if ok else "❌"
        print(f"  {status} {name}" + (f" — {err}" if not ok else ""))
    sys.exit(1 if failed else 0)
