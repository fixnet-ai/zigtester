"""测试执行引擎 — 子进程管理、依赖排序、层级编排。"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .config import (
    LevelConfig,
    ProjectConfig,
    ProjectResult,
    ResourceSnapshot,
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
        """执行单个测试套件，包含五阶段生命周期。

        Phase 1: setup（可选）→ Phase 2: test command →
        Phase 3: stop monitor → Phase 4: stop setup process →
        Phase 5: teardown（finally 保证）

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

        test_proc: subprocess.Popen | None = None
        setup_proc: subprocess.Popen | None = None
        monitor: ResourceMonitor | None = None

        try:
            # ── Phase 1: setup ──
            if suite.setup is not None and suite.setup.command is not None:
                try:
                    setup_proc = self._start_hook_process(suite.setup, work_dir)
                    if not self._wait_hook_ready(setup_proc, suite.setup):
                        ro = suite.setup.ready_on
                        if ro is not None:
                            result.setup_error = (
                                f"setup 就绪检测超时: {ro.type}"
                                + (f" {ro.host}:{ro.port}" if ro.type == "tcp" else "")
                                + f" 在 {ro.timeout}s 内未就绪"
                            )
                        else:
                            exit_code = setup_proc.poll()
                            result.setup_error = (
                                f"setup 命令失败: exit_code={exit_code}"
                            )
                        result.status = "ERROR"
                        result.duration_ms = (time.time() - started) * 1000
                        result.message = result.setup_error
                        return result
                except Exception as e:
                    result.status = "ERROR"
                    result.setup_error = f"setup 失败: {e}"
                    result.duration_ms = (time.time() - started) * 1000
                    result.message = result.setup_error
                    return result

            # ── Phase 2: test command ──
            cmd_parts = _build_cmd(suite)
            env = _build_env(suite)
            monitor = ResourceMonitor()

            test_proc = self._start_process(cmd_parts, env, suite.sudo, work_dir)
            monitor.start(test_proc.pid)

            stdout, stderr, exit_code = self._wait_with_timeout_proc(
                test_proc, suite.timeout
            )

        except Exception as e:
            result.status = "ERROR"
            result.message = f"进程启动失败: {e}"
            result.duration_ms = (time.time() - started) * 1000
            return result
        finally:
            # ── Phase 3: stop resource monitor ──
            snapshot = ResourceSnapshot()
            if monitor is not None:
                snapshot = monitor.stop()
            result.resource_peak = snapshot

            # ── Phase 4: stop setup process ──
            if setup_proc is not None:
                self._kill_progressive_proc(setup_proc, suite.setup.timeout if suite.setup else 30)

            # ── Phase 5: teardown（guaranteed）──
            if suite.teardown is not None:
                self._execute_teardown(suite.teardown, work_dir, result)

        elapsed = (time.time() - started) * 1000
        result.duration_ms = round(elapsed, 1)
        result.exit_code = exit_code
        result.stdout = stdout
        result.stderr = stderr

        # 解析输出
        combined = stdout + "\n" + stderr if stdout and stderr else (stdout or stderr)
        extractor = MetricExtractor(suite.parser, suite.metrics)
        result.metrics = extractor.extract(combined, exit_code)

        # 判断状态 — sudo 不可用时标记 SKIP 而非 FAIL
        if exit_code != 0 and _is_sudo_failure(stderr):
            result.status = "SKIP"
            result.message = "sudo 不可用（无 TTY 或密码未配置）"
        else:
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

    def _start_hook_process(
        self, hook: "LifecycleHook", work_dir: str
    ) -> subprocess.Popen:
        """启动生命周期钩子子进程（shell 模式，支持管道/重定向/shell 内建命令）。

        与 test command 不同，lifecycle hooks 使用 shell=True —
        setup/teardown 脚本常用 shell 特性。
        """
        assert hook.command is not None
        merged_env = dict(os.environ)
        return subprocess.Popen(
            hook.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=merged_env,
            cwd=work_dir,
            shell=True,
        )

    def _wait_with_timeout_proc(
        self, proc: subprocess.Popen, timeout: int
    ) -> tuple[str, str, int]:
        """等待子进程完成，超时则渐进杀死。

        Returns:
            (stdout, stderr, exit_code)
        """
        try:
            stdout_b, stderr_b = proc.communicate(timeout=timeout)
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            return (stdout, stderr, proc.returncode or 0)
        except subprocess.TimeoutExpired:
            self._kill_progressive_proc(proc, timeout)
            try:
                stdout_b, stderr_b = proc.communicate(timeout=2)
                stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
                stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
                return (stdout, stderr, -1)
            except Exception:
                return ("", "进程超时被强制终止", -1)

    def _kill_progressive_proc(
        self, proc: subprocess.Popen, timeout: int
    ) -> None:
        """渐进式杀死进程：SIGTERM → 2s → SIGKILL。"""
        try:
            proc.terminate()
            try:
                proc.wait(timeout=min(2, timeout))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except Exception:
            pass

    def _wait_hook_ready(
        self, proc: subprocess.Popen, hook: "LifecycleHook"
    ) -> bool:
        """等待 hook 就绪。分两种情况：

        - ready_on 存在：启动的是长期服务，轮询 readiness probe。
        - ready_on 不存在：一次性命令，等待完成并检查 exit_code。
        """
        ro = hook.ready_on

        if ro is None:
            # 一次性命令：等待完成，检查退出码
            try:
                exit_code = proc.wait(timeout=hook.timeout)
            except subprocess.TimeoutExpired:
                self._kill_progressive_proc(proc, hook.timeout)
                return False
            return exit_code == 0

        # 长期服务 + readiness probe
        if ro.type == "tcp":
            return _wait_tcp_ready(ro.host, ro.port, ro.timeout, ro.interval)
        elif ro.type == "process":
            target = ""
            if hook.command:
                target = hook.command.split()[0]
            if ro.host and ro.host != "127.0.0.1":
                target = ro.host
            if not target:
                return True
            return _wait_process_ready(target, ro.timeout, ro.interval)
        else:
            return True

    def _execute_teardown(
        self,
        hook: "LifecycleHook",
        work_dir: str,
        result: "SuiteResult",
    ) -> None:
        """执行 teardown 钩子——命令 + 进程清理。失败仅记录 warning。"""
        teardown_proc: subprocess.Popen | None = None

        # 1) 执行 teardown 命令
        if hook.command is not None:
            try:
                teardown_proc = self._start_hook_process(hook, work_dir)
                # 等待 teardown 命令完成（不等 ready_on）
                try:
                    teardown_proc.wait(timeout=hook.timeout)
                except subprocess.TimeoutExpired:
                    self._kill_progressive_proc(teardown_proc, hook.timeout)
            except Exception as e:
                result.teardown_error = f"teardown 命令失败: {e}"

        # 2) 进程名清理
        if hook.kill is not None:
            try:
                self._cleanup_by_name(hook.kill)
            except Exception as e:
                prefix = result.teardown_error + "; " if result.teardown_error else ""
                result.teardown_error = prefix + f"进程清理失败: {e}"

    def _cleanup_by_name(self, names: list[str]) -> None:
        """按进程名清理残留进程：SIGTERM → 0.5s → SIGKILL。

        使用 pkill 清理，失败静默忽略。
        """
        for name in names:
            try:
                # 先温和终止
                subprocess.run(
                    ["pkill", "-f", name],
                    capture_output=True,
                    timeout=5,
                )
                time.sleep(0.5)
                # 再强制终止残留
                subprocess.run(
                    ["pkill", "-9", "-f", name],
                    capture_output=True,
                    timeout=5,
                )
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

    # ── 插件生命周期（project 级别，跨 suites 复用）──
    plugin_procs: list[tuple[Any, Any]] = []
    if cfg.plugins:
        try:
            from .plugin import (
                discover_plugins,
                parse_plugin_config,
                build_plugin,
                start_plugin,
                stop_plugin,
                _find_zigtester_root,
            )
            zt_root = _find_zigtester_root()
            if zt_root is not None:
                available = discover_plugins(zt_root)
                for name in cfg.plugins:
                    if name not in available:
                        continue
                    pcfg = parse_plugin_config(available[name])
                    if pcfg is None:
                        continue
                    build_plugin(pcfg)
                    proc = start_plugin(pcfg, pcfg.path)
                    if proc is not None:
                        plugin_procs.append((pcfg, proc))
        except Exception:
            pass

    try:
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
                ordered = level_cfg.suites

            for suite in ordered:
                suite_result = executor.execute(suite, work_dir)
                suite_result.level = level_name
                result.suites.append(suite_result)

                if fail_fast and suite_result.status == "FAIL":
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
    finally:
        # ── 停止插件（reverse order，finally 保证）──
        for pcfg, proc in reversed(plugin_procs):
            try:
                from .plugin import stop_plugin
                stop_plugin(proc, pcfg)
            except Exception:
                pass


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
        parallel: 并行执行项目（各项目工作目录、子进程、历史存储互相独立）
        fail_fast: 首个项目失败即停止
        no_build: 跳过构建

    Returns:
        WorkspaceResult
    """
    from .config import WorkspaceResult

    ws = WorkspaceResult()

    if not parallel or len(projects) <= 1:
        # 串行路径（保持现有逻辑不变）
        for proj in projects:
            pr = run_project(proj, levels, fail_fast=fail_fast, no_build=no_build)
            ws.projects.append(pr)
            if fail_fast and any(s.status == "FAIL" for s in pr.suites):
                break
    else:
        # 并行路径 — 各项目工作目录、子进程、历史存储互相独立
        with ThreadPoolExecutor(max_workers=len(projects)) as ex:
            futures = {
                ex.submit(run_project, p, levels, fail_fast, no_build): p
                for p in projects
            }
            for f in as_completed(futures):
                try:
                    pr = f.result()
                except Exception:
                    continue
                ws.projects.append(pr)
                if fail_fast and any(s.status == "FAIL" for s in pr.suites):
                    for remaining in futures:
                        remaining.cancel()

    return ws


_SUDO_FAILURE_PATTERNS = [
    "sudo: a password is required",
    "sudo: no tty present",
    "sudo: unable to",
    "is not in the sudoers file",
    "sudo: sorry",
]


def _is_sudo_failure(stderr: str) -> bool:
    """检测 stderr 是否包含 sudo 权限失败信息。"""
    return any(pat in stderr for pat in _SUDO_FAILURE_PATTERNS)


# ── Readiness Probe ─────────────────────────────────────────


def _wait_tcp_ready(host: str, port: int, timeout: int, interval: float) -> bool:
    """轮询 TCP 端口直到可连接或超时。

    等价于 zigbox tests/lib/config.py 的 wait_for_port()。
    """
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=min(0.5, interval))
            s.close()
            return True
        except OSError:
            time.sleep(interval)
    return False


def _wait_process_ready(name: str, timeout: int, interval: float) -> bool:
    """轮询检测指定进程名是否在运行。优先 pgrep，备选 psutil。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = subprocess.run(
                ["pgrep", "-f", name], capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


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
