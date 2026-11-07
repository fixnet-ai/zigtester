"""全局 run 互斥（摩擦 #10 并发互杀收口，2026-11-08）。

插件端口全局唯一 + 清理原语按进程名 pkill（plugin._pkill_names / kiss-core
tests/lib/kisscore.py _cleanup_leftover）⇒ 两个并发 run 必然互为对方的
「可识别残留」互杀（runner.py 模块注释早已知情，但防护只做了单次
run_workspace 内部——有插件项目强制串行）。跨调用形态——MCP server
（asyncio.to_thread 并发 + stateless）与 CLI（独立进程）——此前无任何互斥，
历史实锤：2026-10-19 18:07:16 同一秒落库两条 run 记录。

本模块用 flock 文件锁把「同一时刻至多一个 run 在执行」变成全入口不变量：
flock 语义属于 open file description，同进程内跨 fd 也互斥 ⇒ 一把锁同时
覆盖 server 内并发线程与 CLI 跨进程并发。

等待带超时：拿不到锁 = 另一个 run 在飞 ⇒ RunLockBusy，调用方应向用户返回
显式 busy（稍后重试），而不是让两个 run 互杀出「伪装成崩溃」的假象。
Windows 无 fcntl——本框架的插件管理（lsof/netstat/ps）本就 POSIX 专属，
平台面一致，非新缺口。
"""

from __future__ import annotations

import errno
import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_TIMEOUT_S = 120.0
_POLL_INTERVAL_S = 0.5


class RunLockBusy(RuntimeError):
    """另一个 zigtester run 正在执行（#10）：返回显式 busy，别去动任何进程。"""


def _lock_path() -> Path:
    override = os.environ.get("ZIGTESTER_RUN_LOCK")
    if override:
        return Path(override)
    return Path.home() / ".zigtester" / "run.lock"


@contextmanager
def run_lock(timeout_s: float | None = None) -> Iterator[None]:
    """跨入口全局互斥；timeout_s=None 时读环境变量（默认 120s，0 = 不等待）。

    ⚠️ 必须在 MCP 的 to_thread 工作线程内进入（本 contextmanager 的 acquire
    是阻塞轮询）——在事件循环线程里用会冻结心跳，所有客户端弃单。
    """
    if timeout_s is None:
        try:
            timeout_s = float(os.environ.get("ZIGTESTER_RUN_LOCK_TIMEOUT_S", DEFAULT_TIMEOUT_S))
        except ValueError:
            timeout_s = DEFAULT_TIMEOUT_S
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise RunLockBusy(
                        "another zigtester run is active; waited "
                        f"{timeout_s:.0f}s for {path}"
                    )
                time.sleep(_POLL_INTERVAL_S)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
