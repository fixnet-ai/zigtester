"""测试执行引擎 — 子进程管理、依赖排序、层级编排。"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
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
            monitor.start(test_proc.pid, interval_s=suite.sampling_interval_s, target=suite.target)

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

        # 泄漏判定由框架计算(脚本删除采样器后唯一来源) →
        # rss_growth_mb/fd_growth 进 metrics,由既有 check_thresholds 判 FAIL
        if suite.analyze_leak and monitor is not None and snapshot.sample_count >= 2:
            leak = monitor.analyze_leak(window_s=suite.leak_window_s)
            if leak:
                result.metrics.update(leak)

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

        # zig_test / test_protocols 类指标
        if m.get("tests_total", 0) > 0:
            parts.append(
                f"{int(m['tests_passed'])}/{int(m['tests_total'])} passed"
            )
            if m.get("tests_skipped", 0) > 0:
                parts.append(f"{int(m['tests_skipped'])} skipped")
            if m.get("tests_failed", 0) > 0:
                parts.append(f"{int(m['tests_failed'])} failed")

        # bench 类指标
        tp = m.get("throughput_reqs_per_sec", 0)
        if tp > 0:
            parts.append(f"{tp:.0f} req/s")
        p99 = m.get("latency_p99_ms", 0)
        # fallback: 无 ms 后缀的延迟（可能是微秒，>100 时自动转换）
        if p99 == 0:
            p99_raw = m.get("latency_p99_raw", 0)
            if p99_raw > 100:
                p99 = p99_raw / 1000  # 微秒 → 毫秒
            else:
                p99 = p99_raw
        if p99 > 0:
            parts.append(f"p99={p99:.1f}ms")

        # 吞吐: THROUGHPUT_MBPS= / 传输: X MB/s / 吞吐: X conn/s
        mbps = m.get("throughput_mbps", 0)
        if mbps > 0:
            parts.append(f"{mbps:.1f}MB/s")
        transfer = m.get("transfer_mb_per_sec", 0)
        if transfer > 0:
            parts.append(f"{transfer:.1f}MB/s")
        cps = m.get("conn_per_sec", 0)
        if cps > 0:
            parts.append(f"{cps:.0f}conn/s")

        # 泄漏判定指标（框架 analyze_leak 产出;脚本删除采样器后唯一来源）
        rg = m.get("rss_growth_mb")
        if rg is not None:
            parts.append(f"rss_growth={rg}MB")
        fg = m.get("fd_growth")
        if fg is not None:
            parts.append(f"fd_growth={fg}")

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
    suite_filter: str | None = None,
) -> ProjectResult:
    """执行单个项目的指定层级测试。

    Args:
        project: 扫描发现的项目
        levels: 要执行的层级名列表（空 = 全部）
        fail_fast: 首个失败即停止
        no_build: 跳过构建步骤
        suite_filter: 仅运行指定名称的套件（可选）

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

    # ── 插件生命周期（project 级，每 suite 前自检自愈）──
    # 环境被外部破坏（插件被误杀/端口被残留进程抢占）时：
    # 先尝试自动恢复；恢复失败 fast fail 并输出测试环境规范，
    # 避免在残缺环境上继续跑测试导致结果误判。
    plugin_mgr: Any = None
    if cfg.plugins:
        from .plugin import PluginManager

        plugin_mgr = PluginManager()
        fatal = plugin_mgr.prepare(cfg.plugins)
        if fatal is not None:
            print(fatal, file=sys.stderr)
            _abort_with_env_error(result, cfg, target_levels, fatal)
            return result

    try:
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

            # suite 过滤：仅运行指定套件及其依赖
            if suite_filter is not None:
                filtered = _filter_suites(ordered, suite_filter)
                if not filtered:
                    continue
                ordered = filtered

            for suite in ordered:
                # per_suite_only 套件：仅允许 --suite 显式运行。
                # --level 全量执行（suite_filter=None）时自动跳过，禁止一次跑全部压测。
                if suite_filter is None and suite.per_suite_only:
                    result.suites.append(SuiteResult(
                        suite_name=suite.name,
                        level=level_name,
                        status="SKIP",
                        message="per_suite_only: 仅允许 --suite 单独运行（禁止 --level 全量压测）",
                    ))
                    continue

                # pre-flight：每个套件执行前自检插件环境（无插件时零开销）
                if plugin_mgr is not None:
                    fatal = plugin_mgr.ensure_ready()
                    if fatal is not None:
                        print(fatal, file=sys.stderr)
                        err = SuiteResult(
                            suite_name=suite.name,
                            level=level_name,
                            status="ERROR",
                            message="环境自检失败（自动恢复未成功）— 见下方测试环境规范",
                            setup_error=fatal,
                        )
                        result.suites.append(err)
                        _skip_remaining_suites(
                            result, cfg, target_levels, level_name, suite.name
                        )
                        result.finished_at = time.time()
                        return result

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
        if plugin_mgr is not None:
            plugin_mgr.stop_all()


def _abort_with_env_error(
    result: ProjectResult,
    cfg: ProjectConfig,
    target_levels: list[str],
    fatal: str,
) -> None:
    """环境致命错误 — 首个套件 ERROR + 其余全部 SKIP，禁止在残缺环境上跑测试。"""
    first = True
    for level_name in target_levels:
        level_cfg = cfg.levels.get(level_name)
        if level_cfg is None or not level_cfg.suites:
            continue
        for suite in level_cfg.suites:
            if first:
                result.suites.append(
                    SuiteResult(
                        suite_name=suite.name,
                        level=level_name,
                        status="ERROR",
                        message="测试环境自检失败（自动恢复未成功）— 见测试环境规范",
                        setup_error=fatal,
                    )
                )
                first = False
            else:
                result.suites.append(
                    SuiteResult(
                        suite_name=suite.name,
                        level=level_name,
                        status="SKIP",
                        message="环境自检失败: 跳过（环境未恢复）",
                    )
                )
    result.finished_at = time.time()


def _skip_remaining_suites(
    result: ProjectResult,
    cfg: ProjectConfig,
    target_levels: list[str],
    failed_level: str,
    failed_suite: str,
) -> None:
    """某套件环境自检失败后 — 同层级剩余套件 + 后续层级全部 SKIP。"""
    started = False
    for level_name in target_levels:
        level_cfg = cfg.levels.get(level_name)
        if level_cfg is None or not level_cfg.suites:
            continue
        for suite in level_cfg.suites:
            if level_name == failed_level and suite.name == failed_suite:
                started = True
                continue
            if started:
                result.suites.append(
                    SuiteResult(
                        suite_name=suite.name,
                        level=level_name,
                        status="SKIP",
                        message="环境自检失败: 跳过（环境未恢复）",
                    )
                )


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
        # 串行路径（单项目或未开并行；分组对单项目无意义）
        if parallel and len(projects) <= 1:
            print("[run] 仅 1 个项目，无需分组并行，直接串行执行", file=sys.stderr)
        _run_serial(projects, levels, ws, fail_fast=fail_fast, no_build=no_build)
        return ws

    # 分组并行：插件端口全局唯一（local-echo:13333 等），声明插件的项目
    # 并行启动会互相把对方的插件进程识别为"可识别残留"清理掉，导致环境
    # 互杀、结果误判。因此按是否声明插件分两组：
    #   1) 无插件组 — ThreadPoolExecutor 并行（各项目工作目录/子进程独立）
    #   2) 有插件组 — 顺序串行（各自 run_project 内部管理插件启停）
    # 执行顺序：先无插件并行组，再有插件串行组。
    no_plugin = [p for p in projects if not p.config.plugins]
    with_plugin = [p for p in projects if p.config.plugins]
    if with_plugin and not no_plugin:
        print(
            "[run] 全部项目声明测试插件 ("
            + ", ".join(p.name for p in with_plugin)
            + ") — 插件端口全局唯一，插件项目串行执行",
            file=sys.stderr,
        )

    aborted = False
    if no_plugin:
        # 并行组 — 结果按传入顺序输出（不受完成先后影响）
        with ThreadPoolExecutor(max_workers=len(no_plugin)) as ex:
            futures = {ex.submit(run_project, p, levels, fail_fast, no_build): p
                       for p in no_plugin}
            if fail_fast:
                # 任一 FAIL → cancel 其余
                for f in as_completed(futures):
                    try:
                        pr = f.result()
                    except Exception:
                        continue
                    if any(s.status == "FAIL" for s in pr.suites):
                        for remaining in futures:
                            remaining.cancel()
                        aborted = True
                        break
        for p in no_plugin:  # 按原顺序收集结果
            f = next(f for f in futures if futures[f] is p)
            try:
                ws.projects.append(f.result())
            except Exception:
                continue

    if not aborted and with_plugin:
        # 串行组 — FAIL 即停止（fail_fast）
        for proj in with_plugin:
            pr = run_project(proj, levels, fail_fast=fail_fast, no_build=no_build)
            ws.projects.append(pr)
            if fail_fast and any(s.status == "FAIL" for s in pr.suites):
                break

    return ws


def _run_serial(
    projects: list[DiscoveredProject],
    levels: list[str],
    ws: "WorkspaceResult",
    fail_fast: bool = False,
    no_build: bool = False,
) -> None:
    """全部项目串行执行，结果按传入顺序追加到 ws。"""
    for proj in projects:
        pr = run_project(proj, levels, fail_fast=fail_fast, no_build=no_build)
        ws.projects.append(pr)
        if fail_fast and any(s.status == "FAIL" for s in pr.suites):
            break


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


def _filter_suites(
    suites: list[SuiteConfig], target_name: str
) -> list[SuiteConfig]:
    """过滤套件列表，仅保留目标套件及其传递依赖。

    依赖使用 "level.name" 或 "name" 格式。过滤后的列表保持依赖顺序。
    如果目标不存在，返回空列表。
    """
    # 建立 name → SuiteConfig 索引
    by_name: dict[str, SuiteConfig] = {s.name: s for s in suites}

    # 查找目标套件
    if target_name not in by_name:
        return []

    # 递归收集依赖（传递闭包）
    needed: set[str] = set()

    def collect(name: str) -> None:
        if name in needed:
            return
        needed.add(name)
        suite = by_name.get(name)
        if suite is not None:
            for dep in suite.depends_on:
                dep_name = dep.split(".", 1)[1] if "." in dep else dep
                if dep_name in by_name:
                    collect(dep_name)

    collect(target_name)

    # 按原始顺序返回需要的套件
    return [s for s in suites if s.name in needed]
