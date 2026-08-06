"""资源监控 — 后台线程采样进程树 RSS/fd/CPU%。"""

from __future__ import annotations

import threading
import time
from typing import Any

from .config import ResourceLimits, ResourceSnapshot

# psutil 为可选依赖
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


class ResourceMonitor:
    """后台线程监控进程树资源使用。

    启动后每秒采样一次，stop() 时返回峰值快照。
    无 psutil 时优雅降级返回空快照。
    """

    def __init__(self):
        self._pid: int | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # 累积样本
        self._peak_memory_mb = 0.0
        self._peak_fd = 0
        self._peak_cpu = 0.0
        self._samples_memory: list[float] = []
        self._samples_fd: list[int] = []
        self._samples_cpu: list[float] = []

    # ── 公开 API ───────────────────────────────────────────

    def start(self, pid: int) -> None:
        """启动后台采样线程。"""
        if not _HAS_PSUTIL:
            return
        self._pid = pid
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def stop(self) -> ResourceSnapshot:
        """停止采样并返回峰值快照。"""
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=3.0)

        with self._lock:
            return ResourceSnapshot(
                pid=self._pid or 0,
                peak_memory_mb=round(self._peak_memory_mb, 1),
                avg_memory_mb=round(
                    sum(self._samples_memory) / max(len(self._samples_memory), 1), 1
                ),
                peak_fd_count=self._peak_fd,
                avg_fd_count=round(
                    sum(self._samples_fd) / max(len(self._samples_fd), 1), 1
                ),
                peak_cpu_pct=round(self._peak_cpu, 1),
                avg_cpu_pct=round(
                    sum(self._samples_cpu) / max(len(self._samples_cpu), 1), 1
                ),
                sample_count=len(self._samples_memory),
            )

    # ── 采样循环 ───────────────────────────────────────────

    def _sample_loop(self) -> None:
        """后台线程 — 每秒采样一次。"""
        while not self._stop_event.is_set():
            try:
                self._collect()
            except Exception:
                pass
            self._stop_event.wait(1.0)

    def _collect(self) -> None:
        if self._pid is None or not _HAS_PSUTIL:
            return

        try:
            proc = psutil.Process(self._pid)
        except psutil.NoSuchProcess:
            return

        # 进程树（含子进程）
        try:
            children = proc.children(recursive=True)
        except psutil.NoSuchProcess:
            children = []

        all_procs = [proc] + children
        total_rss = 0.0
        total_fd = 0
        total_cpu = 0.0

        for p in all_procs:
            try:
                with p.oneshot():
                    mem = p.memory_info()
                    total_rss += mem.rss
                    total_fd += p.num_fds() if hasattr(p, "num_fds") else 0
                    total_cpu += p.cpu_percent()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        mem_mb = total_rss / (1024 * 1024)

        with self._lock:
            if mem_mb > self._peak_memory_mb:
                self._peak_memory_mb = mem_mb
            if total_fd > self._peak_fd:
                self._peak_fd = total_fd
            if total_cpu > self._peak_cpu:
                self._peak_cpu = total_cpu

            self._samples_memory.append(mem_mb)
            self._samples_fd.append(total_fd)
            self._samples_cpu.append(total_cpu)

    # ── 阈值检查 ───────────────────────────────────────────

    @staticmethod
    def check_limits(
        snapshot: ResourceSnapshot, limits: ResourceLimits
    ) -> list[str]:
        """检查资源快照是否超限，返回违规描述列表。"""
        violations: list[str] = []

        if limits.memory_mb is not None and snapshot.peak_memory_mb > limits.memory_mb:
            violations.append(
                f"峰值内存 {snapshot.peak_memory_mb}MB > 限制 {limits.memory_mb}MB"
            )
        if limits.fd_count is not None and snapshot.peak_fd_count > limits.fd_count:
            violations.append(
                f"峰值 fd {snapshot.peak_fd_count} > 限制 {limits.fd_count}"
            )
        if limits.cpu_percent is not None and snapshot.peak_cpu_pct > limits.cpu_percent:
            violations.append(
                f"峰值 CPU {snapshot.peak_cpu_pct}% > 限制 {limits.cpu_percent}%"
            )

        return violations
