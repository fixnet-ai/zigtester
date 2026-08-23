"""资源监控 — 后台线程采样目标进程 RSS/fd/CPU%。

采集对象:suite 声明 target(进程名)时只采进程树中匹配该名的进程;
未声明时只采命令进程本身(pid)。不累加整个进程树(排除 python 包装/
插件等非目标进程)。target 未匹配(目标进程启动期 / 配置错误)时跳过
本次采样,不采命令进程——避免包装进程资源污染目标指标。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

from .config import ResourceLimits, ResourceSnapshot

# psutil 为可选依赖
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


class ResourceMonitor:
    """后台线程监控目标进程资源使用。

    启动后按配置间隔(默认 0.2s)采样目标进程(suite 声明 target 时)或命令进程
    本身(未声明时),stop() 时返回 min/avg/peak 快照。analyze_leak() 基于时间
    序列做前后窗口均值对比,判定 RSS/fd 泄漏趋势。无 psutil 时优雅降级返回
    空快照。
    """

    def __init__(self):
        self._pid: int | None = None
        self._target: str | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._interval_s = 0.2
        self._start_wall = 0.0
        # 首样本跳过计数: Popen 后立即采样时进程树仅 python 包装、rss 未加载
        # (实测首样本 0.78MB), 混入 min/series 会失真。只跳首样本而非固定预热
        # 时长 — 短时套件(<1s)会被固定预热整体跳过而丢失资源展示。
        self._n_skipped = 0

        # per-pid 持久 Process 对象缓存 + CPU 差分基线
        # (每次新建 Process 对象会导致 cpu_percent()/cpu_times() 差分基线丢失)
        self._procs: dict[int, Any] = {}
        self._cpu_state: dict[int, tuple[float, float]] = {}

        # 累积样本
        self._peak_memory_mb = 0.0
        self._peak_fd = 0
        self._peak_cpu = 0.0
        self._min_memory_mb = float("inf")
        self._min_fd = 0
        self._min_cpu = 0.0
        self._samples_memory: list[float] = []
        self._samples_fd: list[int] = []
        self._samples_cpu: list[float] = []
        # 时间序列 (elapsed_s, rss_mb, fd_count, cpu_pct) — analyze_leak 输入,仅内存
        self._series: list[tuple[float, float, int, float]] = []

    # ── 公开 API ───────────────────────────────────────────

    def start(self, pid: int, interval_s: float = 0.2, target: str | None = None) -> None:
        """启动后台采样线程。

        Args:
            pid: 被测命令进程 PID
            interval_s: 采样间隔(秒),来自 settings.resource_sampling.interval_s
            target: 资源采集目标进程名(如 "test-engine")。声明时只采进程树中
                匹配该名的进程(全部匹配之和);未声明时只采命令进程本身(pid),
                不累加整个进程树。
        """
        if not _HAS_PSUTIL:
            return
        self._pid = pid
        self._target = target
        self._interval_s = max(interval_s, 0.05)
        self._start_wall = time.perf_counter()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def stop(self) -> ResourceSnapshot:
        """停止采样并返回 min/avg/peak 快照。"""
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=3.0)

        with self._lock:
            n = len(self._samples_memory)
            if self._target and n == 0:
                # 声明了 target 但整个套件期间从未匹配到目标进程:
                # 配置错误(进程名不符)或目标从未启动。空快照 + warning 暴露,
                # 避免资源全 0 被误读为「目标进程资源很小」。
                logger.warning(
                    "resource target %r never matched for command pid=%s; "
                    "snapshot is empty (all samples skipped)",
                    self._target,
                    self._pid,
                )
            return ResourceSnapshot(
                pid=self._pid or 0,
                peak_memory_mb=round(self._peak_memory_mb, 1),
                avg_memory_mb=round(sum(self._samples_memory) / max(n, 1), 1),
                min_memory_mb=round(self._min_memory_mb, 1)
                if self._min_memory_mb != float("inf") else 0.0,
                peak_fd_count=self._peak_fd,
                avg_fd_count=round(sum(self._samples_fd) / max(n, 1), 1),
                min_fd_count=self._min_fd,
                peak_cpu_pct=round(self._peak_cpu, 1),
                avg_cpu_pct=round(sum(self._samples_cpu) / max(n, 1), 1),
                min_cpu_pct=round(self._min_cpu, 1),
                sample_count=n,
            )

    def analyze_leak(self, window_s: float = 30.0) -> dict | None:
        """基于时间序列做前/后窗口均值对比,判定泄漏趋势。

        Args:
            window_s: 前/后窗口宽度(秒)。序列总时长不足 2×window_s 时对半切。

        Returns:
            {rss_growth_mb, fd_growth, cpu_head_pct, cpu_tail_pct};
            序列不足两个采样点时返回 None。
        """
        series = list(self._series)
        if len(series) < 2:
            return None

        total = series[-1][0] - series[0][0]
        if total <= window_s * 2:
            # 序列不足两窗口宽 → 对半切
            mid = len(series) // 2
            head, tail = series[:mid], series[mid:]
        else:
            end_t = series[-1][0]
            head = [s for s in series if s[0] <= window_s]
            tail = [s for s in series if s[0] >= end_t - window_s]
            if not head or not tail:
                return None

        def mean(col: int, rows: list) -> float:
            return 0.0 if not rows else sum(r[col] for r in rows) / len(rows)

        return {
            "rss_growth_mb": round(mean(1, tail) - mean(1, head), 1),
            "fd_growth": round(mean(2, tail) - mean(2, head)),
            "cpu_head_pct": round(mean(3, head), 1),
            "cpu_tail_pct": round(mean(3, tail), 1),
        }

    # ── 采样循环 ───────────────────────────────────────────

    def _sample_loop(self) -> None:
        """后台线程 — 按配置间隔采样。"""
        while not self._stop_event.is_set():
            try:
                self._collect()
            except Exception:
                pass
            self._stop_event.wait(self._interval_s)

    def _proc_for(self, pid: int) -> Any | None:
        """获取持久 Process 对象(缓存命中复用,新 pid 新建)。"""
        p = self._procs.get(pid)
        if p is None:
            try:
                p = psutil.Process(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return None
            self._procs[pid] = p
        return p

    @staticmethod
    def _proc_matches(proc: Any, target: str) -> bool:
        """按进程名或可执行文件基名匹配目标程序。"""
        try:
            if proc.name() == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        try:
            exe = proc.exe()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        return bool(exe) and os.path.basename(exe) == target

    def _collect(self) -> None:
        if self._pid is None or not _HAS_PSUTIL:
            return

        root = self._proc_for(self._pid)
        if root is None:
            return

        # 进程树(含子进程)
        try:
            children = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            children = []
        tree = [root] + children

        # 目标进程筛选:target 声明 → 进程名匹配;未声明 → 只采命令进程本身(root)。
        # 不累加整棵进程树(排除 python 包装 / 插件等非目标进程)。
        if self._target:
            targets = [p for p in tree if self._proc_matches(p, self._target)]
            if not targets:
                # 目标进程尚未出现(启动期,如 test-engine 由 python 包装延迟 Popen)
                # 或配置错误:跳过本次采样,不采命令进程——否则 python 包装进程的
                # RSS/fd 会混入目标资源(启动期 fallback 实测污染 peak)。
                # 不推进首样本跳过标志:目标首次出现时仍走首样本跳过,避免进程
                # 启动瞬间 rss≈0 的样本污染 min 基线(实测 harness rss 0→8MB 爬升)。
                return
        else:
            targets = [root]

        # 死进程从缓存清除(只保留当前目标进程)
        live_pids = {p.pid for p in targets}
        for pid in [k for k in self._procs if k not in live_pids]:
            self._procs.pop(pid, None)
            self._cpu_state.pop(pid, None)

        now = time.perf_counter()
        total_rss = 0.0
        total_fd = 0
        total_cpu = 0.0
        ok = 0  # 成功读取的进程数

        for p in targets:
            try:
                with p.oneshot():
                    mem = p.memory_info()
                    total_rss += mem.rss
                    total_fd += p.num_fds() if hasattr(p, "num_fds") else 0
                    cpu_t = p.cpu_times()
                cpu_now = cpu_t.user + cpu_t.system
                prev = self._cpu_state.get(p.pid)
                if prev is not None:
                    # 差分: 相邻两次采样的 CPU 秒数差 / 墙钟差 → 该进程平均占用率
                    d_cpu = max(cpu_now - prev[0], 0.0)
                    d_wall = max(now - prev[1], 1e-6)
                    total_cpu += d_cpu / d_wall * 100.0
                self._cpu_state[p.pid] = (cpu_now, now)
                ok += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # 目标进程全部读取失败(已退出)时跳过本次采样:
        # 死进程读取为 0 会污染 min/avg/peak 基线(mem min=0 失真根因)
        if ok == 0:
            return

        # 跳过首样本(进程启动瞬间): 进程树仅 python 包装、rss 未加载(实测 0.78MB),
        # 混入 min/avg/series 会失真。首样本恰为 CPU 差分基线(cpu_state 已更新)。
        # 只跳首样本, 短时套件(<1s)仍有后续样本可展示资源。
        if self._n_skipped == 0:
            self._n_skipped = 1
            return

        mem_mb = total_rss / (1024 * 1024)

        with self._lock:
            first = len(self._samples_memory) == 0
            if mem_mb > self._peak_memory_mb:
                self._peak_memory_mb = mem_mb
            if total_fd > self._peak_fd:
                self._peak_fd = total_fd
            if total_cpu > self._peak_cpu:
                self._peak_cpu = total_cpu

            if first:
                # 预热后首样本: 直接作为 min 基线(进程树已稳定, rss 已加载)
                self._min_memory_mb = mem_mb
                self._min_fd = total_fd
                self._min_cpu = total_cpu
            else:
                if mem_mb < self._min_memory_mb:
                    self._min_memory_mb = mem_mb
                # fd/cpu 非 0 时更新(防 min=0 失真)
                if total_fd and (self._min_fd == 0 or total_fd < self._min_fd):
                    self._min_fd = total_fd
                if total_cpu and (self._min_cpu == 0.0 or total_cpu < self._min_cpu):
                    self._min_cpu = total_cpu

            self._samples_memory.append(mem_mb)
            self._samples_fd.append(total_fd)
            self._samples_cpu.append(total_cpu)
            self._series.append(
                (round(now - self._start_wall, 3), round(mem_mb, 3), total_fd, round(total_cpu, 3))
            )

    # ── 阈值检查 ───────────────────────────────────────────

    @staticmethod
    def check_limits(
        snapshot: ResourceSnapshot, limits: ResourceLimits
    ) -> list[str]:
        """检查资源快照是否超限,返回违规描述列表。"""
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
