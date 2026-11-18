"""测试执行引擎 — 子进程管理、依赖排序、层级编排。"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import threading
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

    def execute(
        self, suite: SuiteConfig, work_dir: str, suite_args: str | None = None
    ) -> SuiteResult:
        """执行单个测试套件，包含五阶段生命周期。

        Phase 1: setup（可选）→ Phase 2: test command →
        Phase 3: stop monitor → Phase 4: stop setup process →
        Phase 5: teardown（finally 保证）

        Args:
            suite: 套件配置
            work_dir: 工作目录（项目根目录）
            suite_args: 追加到命令尾部的非标准压测参数（如 "-n 10"），可选

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
            # ── Phase 0: 产物新鲜度守卫（zt-12）──
            # zt-10 假绿新形态（2026-11-19 zigtun 实案）：构建成功但 install 产物
            # 陈旧（build_command 未覆盖该步 / 未配置）——套件对三周前旧二进制 PASS。
            # 声明 artifact 的套件执行前校验：glob 最新匹配 mtime ≥ git HEAD 提交时间。
            if suite.artifact is not None and os.environ.get("ZIGTESTER_SKIP_ARTIFACT_CHECK") != "1":
                err = _check_artifact_fresh(suite.artifact, work_dir)
                if err is not None:
                    result.status = "ERROR"
                    result.setup_error = err
                    result.duration_ms = (time.time() - started) * 1000
                    result.message = err
                    return result

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
            # 非标准压测参数追加到命令尾部（test_bench.py 的 argparse 选项位置无关）
            if suite_args:
                try:
                    cmd_parts += shlex.split(suite_args)
                except ValueError:
                    cmd_parts += suite_args.split()   # 对齐 _build_cmd 兜底写法
            env = _build_env(suite)
            monitor = ResourceMonitor()

            test_proc = self._start_process(cmd_parts, env, suite.sudo, work_dir)
            monitor.start(test_proc.pid, interval_s=suite.sampling_interval_s, target=suite.target)

            no_output_s = _effective_no_output_timeout(suite)
            stdout, stderr, exit_code, kill_reason = self._wait_with_timeout_proc(
                test_proc, suite.timeout, no_output_timeout_s=no_output_s
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

            # ── Phase 6: 兜底清理 test command 进程树（zt-6）──
            # 测试脚本可能未清理其 spawn 的后代（如 long 套件残留 kisscore 占用
            # 12080 → 下一套件环境自愈假失败）。进程组清理覆盖脱离父进程的孤儿；
            # 放 teardown 之后，不干扰套件自带的清理钩子。
            if test_proc is not None:
                self._kill_test_proc_tree(test_proc)

        elapsed = (time.time() - started) * 1000
        result.duration_ms = round(elapsed, 1)
        result.exit_code = exit_code
        result.kill_reason = kill_reason
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
            # ⚠ sudo 默认 env_reset 清空调用者环境变量——套件依赖的 host 化变量
            # （KISSCORE_ECHO_HOST/ZIGTESTER_PLUGIN_HOST 经 merged_env 传入）会被
            # 清空 → ECHO_HOST 回退 127.0.0.1 探测本机端口失败（tun-scenarios
            # macvm 实证，1.6s exit=1）。加 -E 保留调用者环境；PATH 仍受
            # sudoers secure_path 约束（与裸 sudo 一致，无 PATH 回归）。
            cmd = ["sudo", "-E"] + cmd

        merged_env = dict(os.environ)
        merged_env.update(env)

        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=merged_env,
            cwd=work_dir,
            # zt-6：独立进程组（组 id = 命令进程 pid）。test command 与其全部
            # 后代同属一组 → 套件结束后 killpg 兜底清理残留（含脱离父进程的
            # 孤儿被测进程，如 long 套件残留的 kisscore listen 12080）。
            start_new_session=True,
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
        self,
        proc: subprocess.Popen,
        timeout: int,
        no_output_timeout_s: int = 0,
    ) -> tuple[str, str, int, str]:
        """等待子进程完成，超时/静默卡死则渐进杀死（zt-11 ①）。

        旧实现 proc.communicate(timeout=) 只在进程退出后拿输出，框架对被跑
        内容零可见性（09-26 诊断实锚）。现改为双读线程持续排水 + 主循环轮询：

        - 硬超时：started+timeout 到点 → kill_reason="timeout"（语义与旧版一致）
        - 静默 watchdog：no_output_timeout_s>0 且 < timeout 时武装；最后一次
          输出距今 ≥ 窗口 → 判卡死 → kill_reason="no_output(<窗口>)"。
          窗口默认非 zig_test 300s / zig_test 900s（编译期合法静默更长，
          见 _effective_no_output_timeout）。

        Returns:
            (stdout, stderr, exit_code, kill_reason)；正常结束 kill_reason=""
        """
        started = time.monotonic()
        last_output = started  # 读线程每次读到数据刷新（GIL 下 float 写原子，无需锁）
        chunks_out: list[bytes] = []
        chunks_err: list[bytes] = []

        def _drain(pipe, chunks) -> None:
            nonlocal last_output
            try:
                for data in pipe:
                    chunks.append(data)
                    last_output = time.monotonic()
            except (OSError, ValueError):
                pass  # 进程被杀后管道关闭属正常路径

        readers = [
            threading.Thread(target=_drain, args=(proc.stdout, chunks_out), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, chunks_err), daemon=True),
        ]
        for t in readers:
            t.start()

        kill_reason = ""
        watchdog_armed = 0 < no_output_timeout_s < timeout
        hard_deadline = started + timeout

        while True:
            try:
                proc.wait(timeout=0.5)
                break  # 正常退出（含测试脚本自退/自杀）
            except subprocess.TimeoutExpired:
                pass
            now = time.monotonic()
            if now >= hard_deadline:
                kill_reason = "timeout"
                self._kill_progressive_proc(proc, timeout)
                break
            if watchdog_armed and (now - last_output) >= no_output_timeout_s:
                kill_reason = f"no_output({no_output_timeout_s})"
                self._kill_progressive_proc(proc, timeout)
                break

        # 杀进程后管道关闭，读线程自然退出；限时 join 防止极端阻塞丢已读数据
        for t in readers:
            t.join(timeout=2.0)

        stdout = b"".join(chunks_out).decode("utf-8", errors="replace")
        stderr = b"".join(chunks_err).decode("utf-8", errors="replace")
        exit_code = proc.returncode if proc.returncode is not None else -1
        if kill_reason:
            exit_code = -1  # 被框架杀死统一记 -1（对齐旧超时语义 → FAIL）
        return (stdout, stderr, exit_code, kill_reason)

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

    def _kill_test_proc_tree(self, proc: subprocess.Popen) -> None:
        """清理 test command 进程组（含脱离父进程的孤儿后代）。

        test command 启动时 start_new_session=True → 独立进程组，组 id = 命令
        进程 pid。命令进程退出后其后代若未自行清理会残留为孤儿（ppid=1），
        killpg 按组 id 覆盖整组，无需再追踪 ppid。仅清理本套件产生的进程组，
        不影响插件进程（独立进程组）。SIGTERM → 2s → SIGKILL。
        Windows 无进程组/killpg：改用 taskkill /T 递归终止进程树（zt-7）。
        """
        if os.name == "nt":
            # Windows 无 killpg：taskkill /T /F 递归终止整棵进程树。
            # os.kill(pid, 0) 在 Windows 是 TerminateProcess 直接杀进程（非
            # 存活探测），故存活判断用 proc.wait()/proc.poll()，绝不用 os.kill。
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()  # TerminateProcess 兜底
            except Exception:
                pass
            return
        pgid = proc.pid
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                os.killpg(pgid, 0)  # 探测进程组是否仍有成员
            except ProcessLookupError:
                return
            except PermissionError:
                # 进程组含更高权限成员（测试脚本 sudo 拉起的 root 子进程）：
                # 无权探测但组仍存活——继续等循环，让末尾 SIGKILL 的已捕获
                # 分支兜底（09-13 macvm sysproxy/tun-scenarios 收尾崩溃实锚：
                # 此处未捕获使 zigtester 整体 crash，套件被误判 FAIL——
                # 场景结果 11/11 全 PASS 却报 FAIL）
                pass
            time.sleep(0.1)
        try:
            os.killpg(pgid, getattr(signal, "SIGKILL", 9))
        except (ProcessLookupError, PermissionError):
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
            # zt-11 ①：区分硬超时与静默 watchdog 判卡死（两者都 FAIL，措辞区分根因）
            if result.kill_reason.startswith("no_output"):
                window = result.kill_reason.partition("(")[2].rstrip(")") or "?"
                return f"no-output watchdog: {window}s 无输出判卡死"
            return "超时"
        if result.exit_code is not None and result.exit_code != 0:
            parts.append(f"exit={result.exit_code}")

        # 构建失败（编译错误）：message 明确标注，不再沿用误导性的
        # "exit=1; N/N passed"（多 test step 下成功 step 的 pass 行会让
        # parser 误以为测试全过）。见 metrics.py _parse_zig_test build_failure。
        if result.exit_code is not None and result.exit_code != 0 and m.get("build_failure", 0) == 1:
            parts.append("build_failure(编译错误, 测试未运行)")

        # zig_test / test_protocols 类指标
        # build_failure 时计数不可靠（成功 step 的 pass 行残留），只报构建失败
        if m.get("build_failure", 0) == 0 and m.get("tests_total", 0) > 0:
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

# zt-11 ②：run 级总预算默认值（秒）。生态内最坏全量之和 ≈ 数小时，1h 止损线
# 覆盖所有常规门禁形态；项目可在 settings.total_budget_s 或 env 中覆盖。
_TOTAL_BUDGET_DEFAULT_S = 3600


def _env_int(name: str, default: int) -> int:
    """读整型环境变量；缺失/非法回退默认值。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# zt-11 ①：no-output watchdog 静默窗口默认值。
# - line_count/custom/bench/test_protocols 类脚本套件通常持续打印进度行，静默 >300s
#   几乎必为卡死；300s 足够保守（覆盖 python 启动 + 长静默 setup 的正常场景）。
# - zig_test 套件（zig build test）编译期合法静默可达数分钟（冷缓存），900s 才安全；
#   生态内 zig_test 套件 timeout 多为 120-420s，900s 窗口 < timeout 才武装 →
#   实际不触发，zig_test 仍由硬超时兜底（诚实：watchdog 主攻脚本类套件）。
_NO_OUTPUT_DEFAULT_S = 300
_NO_OUTPUT_DEFAULT_ZIG_TEST_S = 900


def _effective_no_output_timeout(suite: SuiteConfig) -> int:
    """解析套件的 no-output watchdog 窗口：套件字段 > env > 按 parser 默认。

    返回 0 = 禁用（显式 no_output_timeout_s: 0 或 env 置 0）。
    """
    if suite.no_output_timeout_s > 0:
        return suite.no_output_timeout_s
    env_raw = os.environ.get("ZIGTESTER_NO_OUTPUT_TIMEOUT_S")
    if env_raw is not None:
        try:
            return max(0, int(env_raw))
        except ValueError:
            pass
    return (
        _NO_OUTPUT_DEFAULT_ZIG_TEST_S
        if suite.parser == "zig_test"
        else _NO_OUTPUT_DEFAULT_S
    )


def _build_cmd(suite: SuiteConfig) -> list[str]:
    """将 command 字符串解析为命令列表（先替换 ${VAR} 插件 env 引用）。"""
    cmd = _subst_env_vars(suite.command, _build_env(suite))
    # 尝试 shlex 解析，失败则按空格分割
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _build_env(suite: SuiteConfig) -> dict[str, str]:
    """构建命令执行环境变量（suite.env + 插件 PLUGIN_* 注入）。

    插件端口/凭证经 plugins/*/plugin.yaml config 段派生为 PLUGIN_<KEY>，
    命令串以 ${PLUGIN_<KEY>} 引用 — 改端口/凭证只改 plugin.yaml 一处（zt-2 收敛）。
    suite.env / 调用方显式值优先（setdefault 不覆盖）。
    """
    env: dict[str, str] = {}
    env.update(suite.env)
    for key, value in _load_plugin_envs().items():
        env.setdefault(key, value)
    return env


def _subst_env_vars(command: str, env: dict[str, str]) -> str:
    """替换命令串中的 ${VAR}；env 未提供的 VAR 保留原样（避免误删）。"""
    return re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), m.group(0)), command)


def _load_plugin_envs() -> dict[str, str]:
    """从 plugins/*/plugin.yaml config 段派生命令串可用的 env（唯一真相源）。

    生成两套命名，供命令串 ${VAR} 引用：
      - PLUGIN_<KEY>        ：通用名（同名 key 多插件冲突时后者覆盖，仅限无歧义场景）
      - <PLUGIN>_<KEY>      ：插件限定名（SINGBOX_/XRAY_/LOCALECHO_），跨插件同名
                               key（如 vless_reality_port / reality_public_key）必须用它
    缺失/损坏的插件配置不阻断测试（返回空 env，命令串 ${VAR} 保留原样
    交由 shlex 拆分，与插件缺失导致的 ERROR 语义一致）。
    """
    env: dict[str, str] = {}
    try:
        plugins_dir = os.environ.get("ZIGTESTER_PLUGINS_DIR") or os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins")
        )
        if plugins_dir not in sys.path:
            sys.path.insert(0, plugins_dir)
        import plugin_ports as _pp  # noqa: PLC0415

        for name, plugin_prefix in (
            ("local-echo", "LOCALECHO"),
            ("sing-box", "SINGBOX"),
            ("xray-core", "XRAY"),
            ("masque-echo", "MASQUEE"),
        ):
            cfg = _pp.load_plugin(name).get("config", {})
            for key, value in cfg.items():
                env[f"PLUGIN_{key.upper()}"] = str(value)
                env[f"{plugin_prefix}_{key.upper()}"] = str(value)
    except Exception:
        pass
    return env


# ── 顶层编排函数 ──────────────────────────────────────────

def run_project(
    project: DiscoveredProject,
    levels: list[str],
    fail_fast: bool = False,
    no_build: bool = False,
    suite_filter: str | None = None,
    suite_args: str | None = None,
    total_budget_s: int | None = None,
) -> ProjectResult:
    """执行单个项目的指定层级测试。

    Args:
        project: 扫描发现的项目
        levels: 要执行的层级名列表（空 = 全部）
        fail_fast: 首个失败即停止
        no_build: 跳过构建步骤
        suite_filter: 仅运行指定名称的套件（可选）
        suite_args: 追加到命令尾部的非标准压测参数（如 "-n 10"），可选
        total_budget_s: run 级总预算秒数（zt-11 ②）。None = 配置/env 回退
            （settings.total_budget_s > ZIGTESTER_TOTAL_BUDGET_S > 默认 3600）；
            ≤0 禁用。预算耗尽后剩余套件 SKIP——止损线，不改单套件语义。

    Returns:
        ProjectResult
    """
    result = ProjectResult(
        project=project.config.project,
        path=project.path,
        started_at=time.time(),
        suite_args=suite_args,
    )

    cfg = project.config
    work_dir = os.path.join(project.path, cfg.settings.work_dir or ".")

    budget_s = total_budget_s
    if budget_s is None:
        if cfg.settings.total_budget_s > 0:
            budget_s = cfg.settings.total_budget_s
        else:
            budget_s = _env_int("ZIGTESTER_TOTAL_BUDGET_S", _TOTAL_BUDGET_DEFAULT_S)
    budget_deadline = (time.monotonic() + budget_s) if budget_s and budget_s > 0 else None

    # 确定要执行的层级
    target_levels = [l for l in levels if l in cfg.levels] if levels else [
        name for name, lc in cfg.levels.items() if lc.suites
    ]

    # 可选构建（zt-10：失败/超时即整体 ERROR——拒绝在陈旧二进制上收集证据；
    # 逃生门 no_build）
    if not no_build and cfg.settings.build_command:
        try:
            _run_build(cfg.settings.build_command, work_dir)
        except BuildFailure as e:
            print(f"[zigtester] {e}", file=sys.stderr)
            _abort_with_build_error(result, cfg, target_levels, str(e))
            return result

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
                # per_suite_only / zt-11 ⑤ 隐式单跑：仅允许 --suite 显式运行。
                # --level 全量执行（suite_filter=None）时自动跳过，禁止一次跑全部压测。
                if suite_filter is None and suite.solo_by_default:
                    solo_msg = (
                        "per_suite_only: 仅允许 --suite 单独运行（禁止 --level 全量压测）"
                        if suite.per_suite_only
                        else "timeout≥300s 默认单跑（zt-11 ⑤）：--suite 单跑，或 allow_full_run: true 解锁全量"
                    )
                    result.suites.append(SuiteResult(
                        suite_name=suite.name,
                        level=level_name,
                        status="SKIP",
                        message=solo_msg,
                    ))
                    continue

                # zt-11 ② 总预算止损：耗尽后本套件起全部 SKIP（含后续层级）
                if budget_deadline is not None and time.monotonic() >= budget_deadline:
                    print(
                        f"[run] 总预算 {budget_s}s 耗尽，剩余套件跳过（zt-11 ②）",
                        file=sys.stderr,
                    )
                    result.suites.append(SuiteResult(
                        suite_name=suite.name,
                        level=level_name,
                        status="SKIP",
                        message=f"总预算 {budget_s}s 耗尽（zt-11 ②）：剩余套件跳过",
                    ))
                    _skip_remaining_suites(
                        result, cfg, target_levels, level_name, suite.name,
                        skip_message="总预算耗尽: 跳过（zt-11 ②）",
                    )
                    result.finished_at = time.time()
                    return result

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

                suite_result = executor.execute(suite, work_dir, suite_args=suite_args)
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
    _abort_all_suites(
        result, cfg, target_levels, fatal,
        error_message="测试环境自检失败（自动恢复未成功）— 见测试环境规范",
        skip_message="环境自检失败: 跳过（环境未恢复）",
    )


def _abort_with_build_error(
    result: ProjectResult,
    cfg: ProjectConfig,
    target_levels: list[str],
    fatal: str,
) -> None:
    """构建失败（zt-10）— 首个套件 ERROR + 其余全部 SKIP，禁止在陈旧二进制上跑测试。"""
    _abort_all_suites(
        result, cfg, target_levels, fatal,
        error_message="构建失败 — 拒绝在陈旧二进制上跑测试（zt-10 假绿防护）",
        skip_message="构建失败: 跳过（二进制陈旧）",
    )


def _abort_all_suites(
    result: ProjectResult,
    cfg: ProjectConfig,
    target_levels: list[str],
    fatal: str,
    error_message: str,
    skip_message: str,
) -> None:
    """setup 级致命错误的统一收口：首个套件 ERROR（带原文），其余全部 SKIP。"""
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
                        message=error_message,
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
                        message=skip_message,
                    )
                )
    result.finished_at = time.time()


def _skip_remaining_suites(
    result: ProjectResult,
    cfg: ProjectConfig,
    target_levels: list[str],
    failed_level: str,
    failed_suite: str,
    skip_message: str = "环境自检失败: 跳过（环境未恢复）",
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
                        message=skip_message,
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

    等价于 kisscore tests/lib/config.py 的 wait_for_port()。
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


class BuildFailure(Exception):
    """构建步骤失败/超时（zt-10 假绿防护：拒绝在陈旧二进制上跑套件）。"""


def _check_artifact_fresh(pattern: str, work_dir: str) -> str | None:
    """zt-12 产物新鲜度校验。陈旧/缺失返回错误消息；通过返回 None。

    判据：产物 glob 最新匹配 mtime ≥ 构建输入（本仓 src/**/*.zig + build.zig +
    build.zig.zon 各 path 依赖仓的 src）最新 mtime（-5s 容差）。
    输入新于产物 = 有改动必然未编入产物（确定性陈旧）——「改完源码没重编就跑套件」
    正是要抓的形态。不用 git 提交时间做锚：「先构建后提交」工作流恒误报，且
    zig install 缓存命中不刷新产物 mtime；path 依赖仓改动同样使产物过期（本判据
    一并覆盖，zigtun×zigstack 实案）。找不到构建输入（非 zig 仓）只查存在。
    """
    import glob as _glob

    matches = [p for p in _glob.glob(os.path.join(work_dir, pattern), recursive=True) if os.path.isfile(p)]
    if not matches:
        return (
            f"zt-12: 测试产物不存在: {pattern} — 先构建（或 artifact 声明写错路径）"
        )
    newest_art = max(os.path.getmtime(p) for p in matches)
    inputs_newest = _build_inputs_newest_mtime(work_dir)
    if inputs_newest is None:
        return None  # 非 zig 仓形态：只查存在
    if newest_art < inputs_newest - 5:
        age_min = (inputs_newest - newest_art) / 60
        return (
            f"zt-12: 测试产物陈旧 — {pattern} 比构建输入（src/ 或 path 依赖仓）旧 "
            f"{age_min:.0f} 分钟，有改动未编入产物，其上的 PASS 是假绿"
            f"（zt-10 新形态：构建成功但 install 产物陈旧）。"
            f"先重建（build_command / zig build <install 步>）；故意跑旧件可置 "
            f"ZIGTESTER_SKIP_ARTIFACT_CHECK=1"
        )
    return None


def _build_inputs_newest_mtime(work_dir: str) -> float | None:
    """构建输入的最新 mtime（zt-12 判据锚）。

    范围 = 本仓 src/**/*.zig + build.zig + build.zig.zon 中各 `.path` 依赖仓的
    同款范围（zig 生态仓固定 src/ 形态）。零误报：输入变更必然应触发重编；「构建
    后又改输入」的 mtime 晚于产物 = 确定陈旧。找不到任何输入返回 None（跳判据）。
    """

    def _scan_repo(root: str) -> float:
        best = 0.0
        src_dir = os.path.join(root, "src")
        if os.path.isdir(src_dir):
            for dirpath, _dirnames, filenames in os.walk(src_dir):
                for fn in filenames:
                    if fn.endswith(".zig"):
                        try:
                            best = max(best, os.path.getmtime(os.path.join(dirpath, fn)))
                        except OSError:
                            pass
        bz = os.path.join(root, "build.zig")
        if os.path.isfile(bz):
            try:
                best = max(best, os.path.getmtime(bz))
            except OSError:
                pass
        return best

    newest = _scan_repo(work_dir)
    zon = os.path.join(work_dir, "build.zig.zon")
    if os.path.isfile(zon):
        try:
            text = open(zon, encoding="utf-8", errors="replace").read()
            for dep_dir in re.findall(r'\.path\s*=\s*"([^"]+)"', text):
                dep_root = os.path.normpath(os.path.join(work_dir, dep_dir))
                if os.path.isdir(os.path.join(dep_root, "src")):
                    newest = max(newest, _scan_repo(dep_root))
        except OSError:
            pass
    return newest if newest > 0 else None


def _run_build(build_command: str, work_dir: str, timeout: int = 300) -> None:
    """执行构建命令；失败或超时 raise BuildFailure（附 stderr 尾部）。

    zt-10（09-25 挖出，10-07 修复）：旧实现丢弃 returncode + except-pass，
    编译失败时旧二进制照跑、套件仍报 PASS —— 构建失败状态下的假绿，
    污染所有 functional 绿灯的证据力。构建是证据链第一环，失败必须挡在
    套件前（run_project 会把全部套件记 ERROR）。逃生门 = no_build
    （CLI --no-build / MCP no_build=true：明知二进制最新时跳过构建）。
    """
    cmd = shlex.split(build_command)
    try:
        proc = subprocess.run(
            cmd, cwd=work_dir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise BuildFailure(
            f"构建超时（>{timeout}s）：{build_command}"
        ) from e
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode(errors="replace").strip()
        tail_lines = "\n".join(tail.splitlines()[-8:])
        detail = f"\n{tail_lines}" if tail_lines else ""
        raise BuildFailure(
            f"构建失败 rc={proc.returncode}：{build_command}{detail}"
        )


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
