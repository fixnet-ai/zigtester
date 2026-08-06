"""测试执行引擎 — 子进程管理、依赖排序、层级编排。"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from collections import deque
from typing import Any

from .config import (
    LevelConfig,
    ProjectConfig,
    ProjectResult,
    SuiteConfig,
    SuiteResult,
)
from .metrics import MetricExtractor
from .monitor import ResourceMonitor
from .scanner import DiscoveredProject


class DependencyResolver:
    """依赖解析 — 拓扑排序，检测循环依赖。"""

    def resolve(self, suites: list[SuiteConfig]) -> list[SuiteConfig]:
        """对套件列表进行拓扑排序。

        depends_on 使用 "level.name" 格式。未在列表中的依赖视为已满足。
        """
        if not suites:
            return []

        # 建立索引
        by_name: dict[str, SuiteConfig] = {}
        for s in suites:
            key = s.name
            by_name[key] = s

        # 入度计算
        in_degree: dict[str, int] = {s.name: 0 for s in suites}
        adj: dict[str, list[str]] = {s.name: [] for s in suites}

        for s in suites:
            for dep in s.depends_on:
                # 依赖可能是 "level.name" 或仅 "name"
                dep_name = dep
                if "." in dep:
                    # 暂时不支持跨层级依赖排序（仅在单层级内解析）
                    dep_name = dep.split(".", 1)[1] if "." in dep else dep

                if dep_name in by_name:
                    adj[dep_name].append(s.name)
                    in_degree[s.name] += 1

        # Kahn 算法
        queue: deque[str] = deque()
        for name, deg in in_degree.items():
            if deg == 0:
                queue.append(name)

        ordered: list[SuiteConfig] = []
        while queue:
            name = queue.popleft()
            if name in by_name:
                ordered.append(by_name[name])
            for neighbor in adj.get(name, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # 未排序的追加到末尾
        for s in suites:
            if s not in ordered:
                ordered.append(s)

        return ordered

    def detect_cycle(self, suites: list[SuiteConfig]) -> list[str] | None:
        """检测循环依赖，返回循环中涉及的套件名或 None。"""
        resolved = self.resolve(suites)
        if len(resolved) == len(suites):
            return None
        # 找到未解析的节点
        resolved_names = {s.name for s in resolved}
        return [s.name for s in suites if s.name not in resolved_names]


class TestExecutor:
    """单套件执行器 — 子进程启动、超时控制、资源监控。"""

    def __init__(self):
        self._proc: subprocess.Popen | None = None

    def execute(self, suite: SuiteConfig, work_dir: str) -> SuiteResult:
        """执行单个测试套件。

        Args:
            suite: 套件配置
            work_dir: 工作目录（项目根目录）

        Returns:
            SuiteResult
        """
        started = time.time()
        result = SuiteResult(
            suite_name=suite.name,
            level="",  # 由调用方填充
        )

        # 构建命令
        cmd_parts = _build_cmd(suite)
        env = _build_env(suite)

        # 启动资源监控
        monitor = ResourceMonitor()

        try:
            self._proc = self._start_process(cmd_parts, env, suite.sudo, work_dir)
            monitor.start(self._proc.pid)

            stdout, stderr, exit_code = self._wait_with_timeout(suite.timeout)
        except Exception as e:
            result.status = "ERROR"
            result.message = f"进程启动失败: {e}"
            result.duration_ms = (time.time() - started) * 1000
            return result
        finally:
            snapshot = monitor.stop()

        elapsed = (time.time() - started) * 1000
        result.duration_ms = round(elapsed, 1)
        result.exit_code = exit_code
        result.stdout = stdout
        result.stderr = stderr
        result.resource_peak = snapshot

        # 解析输出 — zig build test 等工具输出到 stderr，合并两者
        combined = stdout + "\n" + stderr if stdout and stderr else (stdout or stderr)
        extractor = MetricExtractor(suite.parser, suite.metrics)
        result.metrics = extractor.extract(combined, exit_code)

        # 判断状态
        result.status = self._determine_status(exit_code, suite, result.metrics, snapshot)
        result.message = self._build_message(result)

        return result

    # ── 内部方法 ───────────────────────────────────────────

    def _start_process(
        self, cmd: list[str], env: dict[str, str], sudo: bool, work_dir: str
    ) -> subprocess.Popen:
        """启动子进程。"""
        if sudo:
            cmd = ["sudo"] + cmd

        merged_env = dict(os.environ)
        merged_env.update(env)

        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=merged_env,
            cwd=work_dir,
        )

    def _wait_with_timeout(self, timeout: int) -> tuple[str, str, int]:
        """等待子进程完成，超时则渐进杀死。

        Returns:
            (stdout, stderr, exit_code)
        """
        if self._proc is None:
            return ("", "", -1)

        try:
            stdout_b, stderr_b = self._proc.communicate(timeout=timeout)
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            return (stdout, stderr, self._proc.returncode or 0)
        except subprocess.TimeoutExpired:
            self._kill_progressive()
            # 收集残留输出
            try:
                stdout_b, stderr_b = self._proc.communicate(timeout=2)
                stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
                stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
                return (stdout, stderr, -1)
            except Exception:
                return ("", "进程超时被强制终止", -1)

    def _kill_progressive(self) -> None:
        """渐进式杀死进程：SIGTERM → 2s → SIGKILL。"""
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
        except Exception:
            pass

    def _determine_status(
        self,
        exit_code: int,
        suite: SuiteConfig,
        metrics: dict[str, float],
        snapshot: Any,
    ) -> str:
        """根据 exit_code、阈值、资源限制判断状态。"""
        # 超时
        if exit_code == -1:
            return "FAIL"

        # 命令失败
        if exit_code != 0:
            return "FAIL"

        # 阈值检查
        violations = MetricExtractor.check_thresholds(metrics, suite.thresholds)
        if violations:
            return "FAIL"

        # 资源限制检查
        if suite.resource_limits:
            from .monitor import ResourceMonitor as RM
            res_violations = RM.check_limits(snapshot, suite.resource_limits)
            if res_violations:
                return "FAIL"

        return "PASS"

    def _build_message(self, result: SuiteResult) -> str:
        """构建人类可读结果消息。"""
        parts: list[str] = []
        m = result.metrics

        if result.exit_code == -1:
            return "超时"
        if result.exit_code is not None and result.exit_code != 0:
            parts.append(f"exit={result.exit_code}")

        # 内置指标摘要
        if m.get("tests_total", 0) > 0:
            parts.append(
                f"{int(m['tests_passed'])}/{int(m['tests_total'])} passed"
            )
            if m.get("tests_skipped", 0) > 0:
                parts.append(f"{int(m['tests_skipped'])} skipped")
            if m.get("tests_failed", 0) > 0:
                parts.append(f"{int(m['tests_failed'])} failed")

        if result.status == "PASS" and not parts:
            parts.append("exit=0")

        return "; ".join(parts) if parts else "PASS"


# ── 辅助函数 ──────────────────────────────────────────────

def _build_cmd(suite: SuiteConfig) -> list[str]:
    """将 command 字符串解析为命令列表。"""
    # 尝试 shlex 解析，失败则按空格分割
    try:
        return shlex.split(suite.command)
    except ValueError:
        return suite.command.split()


def _build_env(suite: SuiteConfig) -> dict[str, str]:
    """构建命令执行环境变量。"""
    env: dict[str, str] = {}
    env.update(suite.env)
    return env


# ── 顶层编排函数 ──────────────────────────────────────────

def run_project(
    project: DiscoveredProject,
    levels: list[str],
    fail_fast: bool = False,
    no_build: bool = False,
) -> ProjectResult:
    """执行单个项目的指定层级测试。

    Args:
        project: 扫描发现的项目
        levels: 要执行的层级名列表（空 = 全部）
        fail_fast: 首个失败即停止
        no_build: 跳过构建步骤

    Returns:
        ProjectResult
    """
    result = ProjectResult(
        project=project.config.project,
        path=project.path,
        started_at=time.time(),
    )

    cfg = project.config
    work_dir = os.path.join(project.path, cfg.settings.work_dir or ".")

    # 可选构建
    if not no_build and cfg.settings.build_command:
        _run_build(cfg.settings.build_command, work_dir)

    # 确定要执行的层级
    target_levels = [l for l in levels if l in cfg.levels] if levels else [
        name for name, lc in cfg.levels.items() if lc.suites
    ]

    resolver = DependencyResolver()
    executor = TestExecutor()

    for level_name in target_levels:
        level_cfg = cfg.levels.get(level_name)
        if level_cfg is None or not level_cfg.suites:
            continue

        ordered = resolver.resolve(level_cfg.suites)
        cycles = resolver.detect_cycle(level_cfg.suites)
        if cycles:
            # 有循环依赖 → 按原顺序执行，但标记警告
            ordered = level_cfg.suites

        for suite in ordered:
            suite_result = executor.execute(suite, work_dir)
            suite_result.level = level_name
            result.suites.append(suite_result)

            if fail_fast and suite_result.status == "FAIL":
                # 跳过剩余套件
                for remaining in ordered[ordered.index(suite) + 1:]:
                    skipped = SuiteResult(
                        suite_name=remaining.name,
                        level=level_name,
                        status="SKIP",
                        message="fail-fast: 前序套件失败",
                    )
                    result.suites.append(skipped)

                result.finished_at = time.time()
                return result

    result.finished_at = time.time()
    return result


def run_workspace(
    projects: list[DiscoveredProject],
    levels: list[str],
    parallel: bool = False,
    fail_fast: bool = False,
    no_build: bool = False,
) -> "WorkspaceResult":
    """跨项目执行指定层级测试。

    Args:
        projects: 扫描发现的项目列表
        levels: 要执行的层级
        parallel: 并行执行项目（目前串行，后续扩展）
        fail_fast: 首个项目失败即停止
        no_build: 跳过构建

    Returns:
        WorkspaceResult
    """
    from .config import WorkspaceResult

    ws = WorkspaceResult()
    for proj in projects:
        pr = run_project(proj, levels, fail_fast=fail_fast, no_build=no_build)
        ws.projects.append(pr)
        if fail_fast and any(s.status == "FAIL" for s in pr.suites):
            break
    return ws


def _run_build(build_command: str, work_dir: str) -> None:
    """执行构建命令，失败不影响测试（仅警告）。"""
    try:
        cmd = shlex.split(build_command)
        subprocess.run(
            cmd, cwd=work_dir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=300,
        )
    except Exception:
        pass  # 构建失败不阻止测试
