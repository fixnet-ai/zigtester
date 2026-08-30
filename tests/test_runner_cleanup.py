#!/usr/bin/env python3
"""zt-6：test command 进程树兜底清理单元测试。

背景：long 套件（bench-long-*，62-120s）结束后被测 zigbox 残留 listen 12080
→ 下一套件环境自愈假失败（SIGTERM local-echo 连锁）。修复 = test command
启动时独立进程组（start_new_session），套件结束后 killpg 兜底清理（覆盖
脱离父进程的孤儿后代）。

验证：
  1. test command spawn 的后台子进程（父进程退出变孤儿）在套件结束后被清理。
  2. 清理不影响套件自身判定（脚本正常退出仍 PASS）。

运行（zigtester venv）：
    .venv/bin/python -m pytest tests/test_runner_cleanup.py -q
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zigtester.config import SuiteConfig  # noqa: E402
import zigtester.runner as runner  # noqa: E402  (用模块访问 TestExecutor，避免 pytest 收集该类)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def test_executor_kills_spawned_orphan_after_suite():
    """test command 留下的孤儿子进程（zigbox 残留场景）在套件结束后被清理。

    test command 是 python -c 包装，spawn 一个 sleep 30 后台子进程写 pid 文件
    后立即退出（模拟 test_bench.py 退出后残留 zigbox 的孤儿场景）。套件结束后
    该子进程必须已被进程组 killpg 清理。
    """
    tmp = tempfile.mktemp(suffix=".zt6.pid")
    try:
        code = (
            "import subprocess; "
            "p=subprocess.Popen(['sleep','30'], "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
            f"open('{tmp}','w').write(str(p.pid))"
        )
        suite = SuiteConfig(
            name="spawns-orphan",
            command=f"python3 -c \"{code}\"",
            timeout=30,
        )
        ex = runner.TestExecutor()
        result = ex.execute(suite, work_dir=os.getcwd())
        assert result.status == "PASS", f"套件本身应 PASS: {result.message}"

        with open(tmp) as f:
            child_pid = int(f.read().strip())
        # 子进程父进程已退出（孤儿），同进程组 → killpg 应已清理
        deadline = time.time() + 3
        while _alive(child_pid) and time.time() < deadline:
            time.sleep(0.1)
        assert not _alive(child_pid), f"残留子进程存活: pid={child_pid}"
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def test_executor_cleanup_skips_when_no_child():
    """无 spawn 的普通 test command：清理路径无副作用，套件仍 PASS。"""
    suite = SuiteConfig(
        name="plain",
        command="python3 -c \"print('ok')\"",
        timeout=30,
    )
    ex = runner.TestExecutor()
    result = ex.execute(suite, work_dir=os.getcwd())
    assert result.status == "PASS", f"普通命令应 PASS: {result.message}"
