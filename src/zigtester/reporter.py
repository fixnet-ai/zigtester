"""输出格式化 — 终端 ANSI 彩色 / Markdown 表格 / JSON。

复用 zigbox tests/lib/report.py 的 TestResult/TestSuite 模式并增强。
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from .config import (
    ProjectResult,
    Regression,
    SuiteResult,
    WorkspaceResult,
)
from .scanner import DiscoveredProject

# 北京时间
_CST = timezone(timedelta(hours=8))

# ANSI 终端颜色
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"

_STATUS_COLORS = {
    "PASS": _GREEN,
    "FAIL": _RED,
    "SKIP": _DIM,
    "ERROR": _MAGENTA,
}
_STATUS_ICONS = {
    "PASS": "✓",
    "FAIL": "✗",
    "SKIP": "○",
    "ERROR": "⚠",
}


# ANSI 色码（提取失败行时剥离）
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def extract_failure_lines(text: str, limit: int = 10) -> list[str]:
    """从原始输出提取失败用例行。

    匹配含 "✗" / "FAIL" / "ERROR:" 的行，剥离 ANSI 色码，
    strip 后每行截断 160 字符，最多 limit 行。
    """
    if not text:
        return []
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = _ANSI_RE.sub("", raw).strip()
        if "✗" not in stripped and "FAIL" not in stripped and "ERROR:" not in stripped:
            continue
        lines.append(stripped[:160])
        if len(lines) >= limit:
            break
    return lines


# ── performance 套件分组（报表展示层合并，存储/回归仍按套件）──
# 压力并入性能后同协议有多个模式套件：bench-tcp-<proto> / bench-stream-<proto>
# / bench-long-<proto> / bench-tcp-sweep-<proto>。展示层按协议组重排并排对比，
# 同名指标（REQ_S/P99_MS）用模式前缀消歧（纯展示标签，不进 metrics 字典/历史）。

# 模式段匹配顺序关键：tcp-sweep 必须在 tcp 之前（bench-tcp-sweep-direct 才能
# 正确裁出 direct 而非 sweep-direct）。
_GROUP_RE = re.compile(r"^bench-(?:(?:tcp-sweep|tcp|stream|udp|long|sweep)-)?(.+)$")
_MODE_RE = re.compile(r"^bench-(tcp-sweep|tcp|stream|udp|long)-")


def performance_group(suite_name: str) -> str:
    """提取 performance 套件的协议组名。

    bench-tcp-direct / bench-stream-direct / bench-long-direct /
    bench-tcp-sweep-direct → "direct"（同协议多模式合并一组）；
    zigbox 短名 bench-socks5 / bench-long → 自身 token；非 bench 套件 → 自身名。
    """
    m = _GROUP_RE.match(suite_name)
    return m.group(1) if m else suite_name


def mode_of(suite_name: str) -> str:
    """提取 performance 套件的模式前缀（展示消歧）。

    bench-tcp-* → "tcp"、bench-stream-* → "stream"、bench-long-* → "long"、
    bench-tcp-sweep-* → "sweep"；其余（zigbox 短名/非 bench）→ ""。
    """
    m = _MODE_RE.match(suite_name)
    if not m:
        return ""
    return "sweep" if m.group(1) == "tcp-sweep" else m.group(1)


def group_performance_suites(
    suites: list["SuiteResult"],
) -> list[dict[str, Any]]:
    """将 performance 层套件按协议组重排，返回有序组列表。

    每项 {"name": 组名, "suites": [SuiteResult, ...]}，组间按组名字母序，
    组内保持 yaml 定义顺序。非 performance 套件不参与。
    """
    groups: dict[str, list[SuiteResult]] = {}
    for s in suites:
        if s.level != "performance":
            continue
        groups.setdefault(performance_group(s.suite_name), []).append(s)
    return [
        {"name": g, "suites": groups[g]}
        for g in sorted(groups)
    ]


def _fmt_resource(rp: "ResourceSnapshot") -> str:
    """格式化资源快照为紧凑单行字符串。

    sample_count=0 时返回 "n/a"（psutil 未安装或采样失败），
    与真零使用区分开。
    """
    if rp.sample_count == 0:
        return "n/a"
    parts = [f"mem:{rp.peak_memory_mb:.0f}MB"]
    if rp.peak_fd_count > 0:
        parts.append(f"fd:{rp.peak_fd_count}")
    if rp.peak_cpu_pct > 0:
        parts.append(f"cpu:{rp.peak_cpu_pct:.0f}%")
    return " ".join(parts)


def _project_of(ws: "WorkspaceResult", suite_result: "SuiteResult") -> str:
    """根据 SuiteResult 反向查找所属项目名。"""
    for pr in ws.projects:
        if suite_result in pr.suites:
            return pr.project
    return "?"


class Reporter:
    """测试报告输出器。"""

    def __init__(self, format: str = "terminal", verbose: bool = False):
        self.format = format
        self.verbose = verbose

    # ── 扫描结果 ───────────────────────────────────────────

    def print_scan_result(self, projects: list[DiscoveredProject]) -> None:
        """打印项目发现结果。"""
        if self.format == "json":
            print(json.dumps({
                "projects": [
                    {
                        "name": p.name,
                        "path": p.path,
                        "config_path": p.config_path,
                        "description": p.config.description,
                        "levels": p.levels,
                    }
                    for p in projects
                ]
            }, indent=2, ensure_ascii=False))
            return

        if not projects:
            print("未发现包含 zigtester.yaml 的项目。")
            return

        print(f"{_BOLD}发现 {len(projects)} 个项目{_RESET}\n")
        for p in projects:
            desc = f" — {p.config.description}" if p.config.description else ""
            levels_str = ", ".join(p.levels)
            print(f"  {_CYAN}{p.name}{_RESET}{desc}")
            print(f"    路径: {p.path}")
            print(f"    层级: {levels_str}")
            print(f"    配置: {p.config_path}")
            print()

    # ── 套件列表 ───────────────────────────────────────────

    def print_suite_list(self, project: DiscoveredProject) -> None:
        """打印项目套件列表。"""
        if self.format == "json":
            levels: dict[str, list[dict]] = {}
            for name, lc in project.config.levels.items():
                if lc.suites:
                    levels[name] = [
                        {
                            "name": s.name,
                            "command": s.command,
                            "timeout": s.timeout,
                            "sudo": s.sudo,
                            "parser": s.parser,
                        }
                        for s in lc.suites
                    ]
            print(json.dumps({
                "project": project.name,
                "levels": levels,
            }, indent=2, ensure_ascii=False))
            return

        print(f"{_BOLD}{project.name}{_RESET} — 测试套件\n")
        level_order = ["unit", "functional", "performance"]
        for level_name in level_order:
            lc = project.config.levels.get(level_name)
            if lc is None or not lc.suites:
                continue
            label = {"unit": "单元", "functional": "功能",
                     "performance": "性能"}.get(level_name, level_name)
            print(f"  {_CYAN}[{level_name}]{_RESET} {label}")
            for s in lc.suites:
                deps = f" ← {', '.join(s.depends_on)}" if s.depends_on else ""
                sudo_mark = f" {_YELLOW}[sudo]{_RESET}" if s.sudo else ""
                suite_only_mark = f" {_YELLOW}[suite-only]{_RESET}" if s.per_suite_only else ""
                print(f"    {s.name}: {s.command}{sudo_mark}{suite_only_mark}{deps}")
            print()

    # ── 执行结果 ───────────────────────────────────────────

    def print_results(self, result: ProjectResult) -> None:
        """打印项目执行结果。"""
        if self.format == "json":
            print(self.to_json(result))
            return

        if self.format == "markdown":
            print(self.to_markdown(result))
            return

        # 终端格式
        self._print_terminal(result)

    def print_workspace_results(self, ws: WorkspaceResult) -> None:
        """打印跨项目结果。"""
        if self.format == "json":
            output = {
                "projects": [
                    json.loads(Reporter("json").to_json(pr))
                    for pr in ws.projects
                ]
            }
            print(json.dumps(output, indent=2, ensure_ascii=False))
            return

        for pr in ws.projects:
            self.print_results(pr)
            if self.format == "terminal":
                print()

        # 汇总
        total_suites = sum(len(pr.suites) for pr in ws.projects)
        total_pass = sum(
            sum(1 for s in pr.suites if s.status == "PASS")
            for pr in ws.projects
        )
        total_fail = sum(
            sum(1 for s in pr.suites if s.status == "FAIL")
            for pr in ws.projects
        )
        total_error = sum(
            sum(1 for s in pr.suites if s.status == "ERROR")
            for pr in ws.projects
        )

        if self.format == "terminal":
            print(f"{_BOLD}{'='*60}{_RESET}")
            print(f"  工作区总计: {len(ws.projects)} 项目, {total_suites} 套件")
            print(f"  通过: {total_pass}{_GREEN} ✓{_RESET}  "
                  f"失败: {total_fail}{_RED} ✗{_RESET}  "
                  f"错误: {total_error}{_MAGENTA} ⚠{_RESET}")
            # 工作区资源汇总
            all_suites = [s for pr in ws.projects for s in pr.suites
                         if s.resource_peak.sample_count > 0]
            if all_suites:
                mem = max(all_suites, key=lambda s: s.resource_peak.peak_memory_mb)
                fd = max(all_suites, key=lambda s: s.resource_peak.peak_fd_count)
                print(
                    f"  {_DIM}资源峰值: mem={mem.resource_peak.peak_memory_mb:.0f}MB "
                    f"({mem.suite_name}@{_project_of(ws, mem)}) | "
                    f"fd={fd.resource_peak.peak_fd_count} "
                    f"({fd.suite_name}@{_project_of(ws, fd)}){_RESET}"
                )
            print(f"{_BOLD}{'='*60}{_RESET}")

    # ── 终端格式 ───────────────────────────────────────────

    def _print_terminal(self, result: ProjectResult) -> None:
        """终端 ANSI 彩色输出。"""
        elapsed = result.finished_at - result.started_at
        now = datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")

        pass_n = sum(1 for s in result.suites if s.status == "PASS")
        fail_n = sum(1 for s in result.suites if s.status == "FAIL")
        skip_n = sum(1 for s in result.suites if s.status == "SKIP")
        err_n = sum(1 for s in result.suites if s.status == "ERROR")

        all_ok = fail_n == 0 and err_n == 0
        header_icon = f"{_GREEN}✅{_RESET}" if all_ok else f"{_RED}❌{_RESET}"

        print()
        print(f"{_BOLD}{'='*60}{_RESET}")
        print(f"  {_BOLD}项目: {result.project}{_RESET}  {header_icon}")
        print(f"  路径: {result.path}")
        print(f"  时间: {now} CST")
        print(f"  耗时: {elapsed:.1f}s")
        print(f"{_BOLD}{'-'*60}{_RESET}")

        # 非 performance：按状态排序（原逻辑）
        other = [s for s in result.suites if s.level != "performance"]
        for status in ["PASS", "FAIL", "ERROR", "SKIP"]:
            for s in other:
                if s.status != status:
                    continue
                self._print_suite_terminal(s)

        # performance：按协议组重排，组间分隔头（同协议 tcp/stream/long/sweep 并排对比）
        perf_groups = group_performance_suites(result.suites)
        if perf_groups:
            print(f"\n  {_CYAN}── 性能测试（按协议组）{_RESET}")
            for group in perf_groups:
                print(f"  {_BOLD}[{group['name']}]{_RESET}")
                for status in ["PASS", "FAIL", "ERROR", "SKIP"]:
                    for s in group["suites"]:
                        if s.status != status:
                            continue
                        self._print_suite_terminal(s)

        # 项目级资源汇总
        self._print_resource_summary(result.suites)

        print(f"{_BOLD}{'-'*60}{_RESET}")
        summary = (
            f"  总计 {len(result.suites)} | "
            f"{_GREEN}通过 {pass_n}{_RESET} | "
            f"{_RED}失败 {fail_n}{_RESET} | "
            f"跳过 {skip_n}"
        )
        if err_n > 0:
            summary += f" | {_MAGENTA}错误 {err_n}{_RESET}"
        print(summary)

        if all_ok and pass_n > 0:
            print(f"\n  {_GREEN}✅ 全部通过！{_RESET}")
        elif fail_n > 0 or err_n > 0:
            print(f"\n  {_RED}❌ 存在失败测试。{_RESET}")

        print(f"{_BOLD}{'='*60}{_RESET}")

    def _print_suite_terminal(self, suite: SuiteResult) -> None:
        """终端格式 — 单个套件。"""
        icon = _STATUS_ICONS.get(suite.status, "?")
        color = _STATUS_COLORS.get(suite.status, _RESET)
        duration = f" ({suite.duration_ms:.0f}ms)" if suite.duration_ms > 0 else ""
        level_tag = f"[{suite.level}] " if suite.level else ""
        msg = f" — {suite.message}" if suite.message else ""

        # 非 verbose：套件行末尾追加资源摘要
        res_str = _fmt_resource(suite.resource_peak)
        if res_str != "n/a":
            line = f"  {color}{icon}{_RESET} {level_tag}{suite.suite_name}{duration}{msg}  {_DIM}{res_str}{_RESET}"
        else:
            line = f"  {color}{icon}{_RESET} {level_tag}{suite.suite_name}{duration}{msg}"
        print(line)

        # 详细模式显示指标（performance 套件同名键加模式前缀消歧）
        if self.verbose and suite.metrics:
            mode = mode_of(suite.suite_name)
            for k, v in suite.metrics.items():
                label = f"{mode}:{k}" if mode else k
                print(f"      {_DIM}{label}={v}{_RESET}")

        # 详细模式显示完整资源（峰值 + 均值）
        if self.verbose and suite.resource_peak.sample_count > 0:
            rp = suite.resource_peak
            print(f"      {_DIM}mem: peak={rp.peak_memory_mb}MB "
                  f"avg={rp.avg_memory_mb}MB | "
                  f"fd: peak={rp.peak_fd_count} avg={rp.avg_fd_count:.0f} | "
                  f"cpu: peak={rp.peak_cpu_pct}% avg={rp.avg_cpu_pct}%{_RESET}")
        elif self.verbose and suite.resource_peak.sample_count == 0:
            print(f"      {_DIM}res: n/a{_RESET}")

        # 失败/错误套件：打印失败用例行（始终显示，非 verbose 也展示关键信息）
        if suite.status in ("FAIL", "ERROR") and suite.stdout:
            for fl in extract_failure_lines(suite.stdout):
                print(f"      {_RED}{fl}{_RESET}")

        # 显示 setup/teardown 错误（始终显示，非 verbose 也展示关键信息）
        if suite.setup_error:
            print(f"      {_YELLOW}⚠ setup: {suite.setup_error}{_RESET}")
        if suite.teardown_error:
            print(f"      {_YELLOW}⚠ teardown: {suite.teardown_error}{_RESET}")

    @staticmethod
    def _print_resource_summary(suites: list["SuiteResult"]) -> None:
        """打印项目级资源汇总：peak memory + 对应套件，总 fd 峰值。

        仅当至少有一个套件包含有效采样数据时才输出。
        """
        sampled = [s for s in suites if s.resource_peak.sample_count > 0]
        if not sampled:
            return

        peak_mem = max(sampled, key=lambda s: s.resource_peak.peak_memory_mb, default=None)
        peak_fd = max(sampled, key=lambda s: s.resource_peak.peak_fd_count, default=None)
        peak_cpu = max(sampled, key=lambda s: s.resource_peak.peak_cpu_pct, default=None)
        total_fd = max(s.resource_peak.peak_fd_count for s in sampled)

        parts = []
        if peak_mem:
            parts.append(
                f"mem: {peak_mem.resource_peak.peak_memory_mb:.0f}MB ({peak_mem.suite_name})"
            )
        if total_fd > 0:
            parts.append(f"fd: {total_fd} peak")
        if peak_cpu and peak_cpu.resource_peak.peak_cpu_pct > 0:
            parts.append(
                f"cpu: {peak_cpu.resource_peak.peak_cpu_pct:.0f}% ({peak_cpu.suite_name})"
            )

        if parts:
            print(f"  {_DIM}资源: {' | '.join(parts)}{_RESET}")

    # ── Markdown 格式 ──────────────────────────────────────

    def to_markdown(self, result: ProjectResult) -> str:
        """生成 Markdown 格式报表。"""
        elapsed = result.finished_at - result.started_at
        now = datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")

        pass_n = sum(1 for s in result.suites if s.status == "PASS")
        fail_n = sum(1 for s in result.suites if s.status == "FAIL")
        skip_n = sum(1 for s in result.suites if s.status == "SKIP")
        err_n = sum(1 for s in result.suites if s.status == "ERROR")
        all_ok = fail_n == 0 and err_n == 0
        status_icon = "✅" if all_ok else "❌"

        lines = [
            f"## {result.project} — Test Report {status_icon}",
            f"> {now} CST | total={len(result.suites)} "
            f"passed={pass_n} failed={fail_n} skipped={skip_n} errors={err_n}",
            f"> duration={elapsed:.1f}s | path={result.path}",
            "",
            "### 结果汇总",
            "| 指标 | 值 |",
            "|------|----|",
            f"| 总套件数 | {len(result.suites)} |",
            f"| 通过 | {pass_n} ✅ |",
        ]
        if fail_n > 0:
            lines.append(f"| 失败 | {fail_n} ❌ |")
        if skip_n > 0:
            lines.append(f"| 跳过 | {skip_n} |")
        if err_n > 0:
            lines.append(f"| 错误 | {err_n} ⚠ |")
        lines.append(f"| 耗时 | {elapsed:.1f}s |")
        lines.append("")

        lines.append("### 套件明细")

        def _append_rows(ss: list[Any]) -> None:
            for s in ss:
                icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "○", "ERROR": "⚠"}.get(
                    s.status, "?"
                )
                duration = f"{s.duration_ms:.0f}ms" if s.duration_ms > 0 else "-"
                msg = s.message.replace("|", "\\|") if s.message else "-"
                mode = mode_of(s.suite_name)
                if mode:
                    msg = f"`{mode}` {msg}"
                res_col = _fmt_resource(s.resource_peak)
                lines.append(
                    f"| {icon} | {s.level} | {s.suite_name} | {duration} | {res_col} | {msg} |"
                )

        # 非 performance 原顺序；performance 按协议组重排（组间加小节标题）
        non_perf = [s for s in result.suites if s.level != "performance"]
        if non_perf:
            lines.append("| 状态 | 层级 | 套件 | 耗时 | 资源 | 说明 |")
            lines.append("|------|------|------|------|------|------|")
            _append_rows(non_perf)
        for group in group_performance_suites(result.suites):
            lines.append(f"\n**[{group['name']}]**")
            lines.append("| 状态 | 层级 | 套件 | 耗时 | 资源 | 说明 |")
            lines.append("|------|------|------|------|------|------|")
            _append_rows(group["suites"])
        lines.append("")

        if all_ok and pass_n > 0:
            lines.append("**🎉 全部通过！**")
        elif fail_n > 0 or err_n > 0:
            lines.append("**💥 存在失败测试。**")

        return "\n".join(lines)

    # ── JSON 格式 ──────────────────────────────────────────

    def to_json(self, result: ProjectResult) -> str:
        """导出 JSON 格式报告。"""
        def _build_suite_dict(s: SuiteResult) -> dict:
            d: dict = {
                "name": s.suite_name,
                "level": s.level,
                "status": s.status,
                "duration_ms": round(s.duration_ms, 1),
                "exit_code": s.exit_code,
                "message": s.message,
                "metrics": s.metrics,
                "resource": {
                    "peak_memory_mb": s.resource_peak.peak_memory_mb,
                    "avg_memory_mb": s.resource_peak.avg_memory_mb,
                    "peak_fd": s.resource_peak.peak_fd_count,
                    "peak_cpu_pct": s.resource_peak.peak_cpu_pct,
                    "sample_count": s.resource_peak.sample_count,
                },
            }
            # 失败/错误套件附带失败用例行（数据源 stdout）
            if s.status in ("FAIL", "ERROR"):
                failure_lines = extract_failure_lines(s.stdout)
                if failure_lines:
                    d["failure_lines"] = failure_lines
            if s.setup_error:
                d["setup_error"] = s.setup_error
            if s.teardown_error:
                d["teardown_error"] = s.teardown_error
            return d

        return json.dumps({
            "project": result.project,
            "path": result.path,
            "timestamp": datetime.now(_CST).isoformat(),
            "elapsed_s": round(result.finished_at - result.started_at, 1),
            "summary": {
                "total": len(result.suites),
                "passed": sum(1 for s in result.suites if s.status == "PASS"),
                "failed": sum(1 for s in result.suites if s.status == "FAIL"),
                "skipped": sum(1 for s in result.suites if s.status == "SKIP"),
                "errors": sum(1 for s in result.suites if s.status == "ERROR"),
            },
            "suites": [_build_suite_dict(s) for s in result.suites],
        }, indent=2, ensure_ascii=False)

    def save_json(self, result: ProjectResult, path: str) -> None:
        """保存 JSON 报告到文件。"""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json(result))
        print(f"  报告已保存: {path}")

    # ── 紧凑 Markdown 表格（MCP 专用）──────────────────────

    @staticmethod
    def compact_markdown(result: ProjectResult) -> str:
        """生成固定格式的紧凑 Markdown 表格 — 保证 MCP 人类可读性一致。

        每次 zigtester_run 返回此字段，Claude Code 直接渲染 markdown
        表格，格式完全由服务端控制，不依赖模型排版。
        """
        elapsed = result.finished_at - result.started_at
        pass_n = sum(1 for s in result.suites if s.status == "PASS")
        fail_n = sum(1 for s in result.suites if s.status == "FAIL")
        err_n = sum(1 for s in result.suites if s.status == "ERROR")
        skip_n = sum(1 for s in result.suites if s.status == "SKIP")
        all_ok = fail_n == 0 and err_n == 0
        icon = "✅" if all_ok else "❌"

        now = datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            f"## {result.project} — 测试报告 {icon}",
            "",
            f"> {now} CST | 总耗时 {elapsed:.1f}s | "
            f"通过 {pass_n} | 失败 {fail_n} | 错误 {err_n} | 跳过 {skip_n}",
            "",
            "| 层级 | 套件 | 状态 | 耗时 | 资源 | 关键指标 |",
            "|------|------|------|------|------|----------|",
        ]

        for s in result.suites:
            icon_s = _STATUS_ICONS.get(s.status, "?")
            emoji = {"PASS": "✅", "FAIL": "❌", "SKIP": "○", "ERROR": "⚠"}.get(
                s.status, "?"
            )
            # 耗时格式化
            if s.duration_ms >= 1000:
                dur_str = f"{s.duration_ms / 1000:.1f}s"
            elif s.duration_ms > 0:
                dur_str = f"{s.duration_ms:.0f}ms"
            else:
                dur_str = "-"

            # 关键指标：优先用 message，fallback 到退出码。
            # performance 套件加模式前缀（tcp/stream/long/sweep）消歧同名键。
            key = s.message.strip() if s.message else f"exit={s.exit_code}"
            mode = mode_of(s.suite_name)
            if mode:
                key = f"[{mode}] {key}"

            # 资源列
            res_col = _fmt_resource(s.resource_peak)

            # 拼接 setup/teardown 警告
            notes = ""
            if s.setup_error:
                notes += f" ⚠setup"
            if s.teardown_error:
                notes += f" ⚠teardown"

            lines.append(
                f"| {s.level} | {s.suite_name} | {emoji} {s.status} | {dur_str} | {res_col} | {key}{notes} |"
            )

        lines.append("")

        # 项目级资源汇总
        sampled = [s for s in result.suites if s.resource_peak.sample_count > 0]
        if sampled:
            peak_mem = max(sampled, key=lambda s: s.resource_peak.peak_memory_mb)
            peak_fd = max(sampled, key=lambda s: s.resource_peak.peak_fd_count)
            total_fd = max(s.resource_peak.peak_fd_count for s in sampled)
            lines.append(f"> 资源峰值: mem={peak_mem.resource_peak.peak_memory_mb:.0f}MB "
                        f"({peak_mem.suite_name}) "
                        f"fd={total_fd} ({peak_fd.suite_name})")
            lines.append("")

        # 汇总行
        total = len(result.suites)
        if all_ok and total > 0:
            lines.append(f"**结果**: {total}/{total} ✅ 全部通过")
        else:
            parts = [f"{pass_n} 通过"]
            if fail_n > 0:
                parts.append(f"{fail_n} 失败 ❌")
            if err_n > 0:
                parts.append(f"{err_n} 错误 ⚠")
            if skip_n > 0:
                parts.append(f"{skip_n} 跳过")
            lines.append(f"**结果**: {total} 套件 | " + " | ".join(parts))

        # 失败详情（仅失败/错误时展示）
        for s in result.suites:
            if s.status in ("FAIL", "ERROR"):
                detail = s.message.strip() if s.message else f"exit={s.exit_code}"
                lines.append(f"- 🔴 **{s.suite_name}** [{s.level}]: {detail}")
                if s.setup_error:
                    lines.append(f"  - setup 错误: {s.setup_error}")
                if s.teardown_error:
                    lines.append(f"  - teardown 错误: {s.teardown_error}")
                if s.stderr.strip():
                    # 仅包含 stderr 尾部的关键行（最多 5 行）
                    stderr_lines = s.stderr.strip().splitlines()
                    relevant = [l for l in stderr_lines if l.strip()][-5:]
                    if relevant:
                        lines.append(f"  - stderr 尾部:")
                        lines.append("    ```")
                        for rl in relevant:
                            lines.append(f"    {rl}")
                        lines.append("    ```")

        return "\n".join(lines)

    # ── 历史 ───────────────────────────────────────────────

    def print_history(
        self, project: str, suite: str,
        records: list[dict], regressions: list[Regression],
        group_records: dict[str, list[dict]] | None = None,
    ) -> None:
        """打印历史记录和回归检测结果。

        group_records: 协议组视图 — {套件名: records}。非 None 时渲染
        组内全部成员套件的合并表（每行 = 套件/时间/状态/指标，指标带模式
        前缀消歧），不破坏单套件精确匹配的原有逻辑。
        """
        if self.format == "json":
            from .history import detect_flaky
            out: dict[str, Any] = {
                "project": project,
                "suite": suite,
                "flaky": detect_flaky(records),
                "runs": [
                    {
                        "timestamp": r.get("timestamp"),
                        "status": r.get("status"),
                        "duration_ms": r.get("duration_ms"),
                        "metrics": r.get("metrics"),
                    }
                    for r in records
                ],
                "regressions": [
                    {
                        "metric": reg.metric,
                        "current": reg.current,
                        "baseline_avg": reg.baseline_avg,
                        "pct_change": reg.pct_change,
                        "is_regression": reg.is_regression,
                    }
                    for reg in regressions
                ],
            }
            if group_records is not None:
                out["groups"] = {
                    member: [
                        {
                            "timestamp": r.get("timestamp"),
                            "status": r.get("status"),
                            "duration_ms": r.get("duration_ms"),
                            "metrics": r.get("metrics"),
                        }
                        for r in recs
                    ]
                    for member, recs in group_records.items()
                }
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return

        # 协议组视图（suite 为组名时由调用方传入 group_records）
        if group_records is not None:
            self._print_history_group(suite, group_records)
            return

        print(f"\n{_BOLD}{project}/{suite} — 历史记录{_RESET}\n")

        if not records:
            print("  (无历史记录)")
            return

        # flaky 标注（结果不稳定）
        from .history import detect_flaky
        if detect_flaky(records):
            print(f"  {_YELLOW}⚠ flaky: 近期结果在 PASS/FAIL 间反复翻转{_RESET}")

        print(f"  {'时间':<22} {'状态':<6} {'耗时':>8}  指标")
        print(f"  {'-'*22} {'-'*6} {'-'*8}  {'-'*30}")
        for r in records[:10]:
            ts = r.get("timestamp", "")[:19]
            status = r.get("status", "?")
            dur = f"{r.get('duration_ms', 0):.0f}ms"
            metrics = r.get("metrics", {})
            metric_str = " ".join(
                f"{k}={v}" for k, v in metrics.items()
                if k not in ("exit_code",)
            )
            color = _STATUS_COLORS.get(status, _RESET)
            print(f"  {ts:<22} {color}{status:<6}{_RESET} {dur:>8}  {_DIM}{metric_str}{_RESET}")

        # 回归检测
        if regressions:
            print(f"\n  {_YELLOW}⚠ 检测到退化:{_RESET}")
            for reg in regressions:
                direction = "↓" if reg.pct_change < 0 else "↑"
                print(
                    f"    {reg.metric}: {reg.current:.2f} vs "
                    f"基线 {reg.baseline_avg:.2f} "
                    f"({direction}{abs(reg.pct_change):.1f}%)"
                )
        else:
            print(f"\n  {_GREEN}✓ 未检测到性能退化{_RESET}")

    def _print_history_group(
        self, group: str, group_records: dict[str, list[dict]],
    ) -> None:
        """协议组历史视图 — 合并展示组内全部成员套件的趋势。

        每行 = (套件, 时间, 状态, 耗时, 指标)，指标带模式前缀
        （tcp:/stream:/long:/sweep:）消歧同名键。回归检测保持按套件
        （单套件精确查询），组视图只做趋势合并展示。
        """
        print(f"\n{_BOLD}{group} 协议组 — 历史记录{_RESET}\n")

        if not group_records:
            print("  (组内无历史记录)")
            return

        print(f"  {'套件':<28} {'时间':<14} {'状态':<6} {'耗时':>8}  指标")
        print(f"  {'-'*28} {'-'*14} {'-'*6} {'-'*8}  {'-'*30}")
        for member in sorted(group_records):
            mode = mode_of(member)
            prefix = f"{mode}:" if mode else ""
            for r in group_records[member]:
                ts = r.get("timestamp", "")[:19]
                status = r.get("status", "?")
                dur = f"{r.get('duration_ms', 0):.0f}ms"
                metrics = r.get("metrics", {})
                metric_str = " ".join(
                    f"{prefix}{k}={v}"
                    for k, v in metrics.items()
                    if k not in ("exit_code",)
                )
                color = _STATUS_COLORS.get(status, _RESET)
                print(
                    f"  {member:<28} {ts:<14} {color}{status:<6}{_RESET} "
                    f"{dur:>8}  {_DIM}{metric_str}{_RESET}"
                )
