"""输出格式化 — 终端 ANSI 彩色 / Markdown 表格 / JSON。

复用 zigbox tests/lib/report.py 的 TestResult/TestSuite 模式并增强。
"""

from __future__ import annotations

import json
import os
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
        level_order = ["unit", "functional", "performance", "stress"]
        for level_name in level_order:
            lc = project.config.levels.get(level_name)
            if lc is None or not lc.suites:
                continue
            label = {"unit": "单元", "functional": "功能",
                     "performance": "性能", "stress": "压力"}.get(level_name, level_name)
            print(f"  {_CYAN}[{level_name}]{_RESET} {label}")
            for s in lc.suites:
                deps = f" ← {', '.join(s.depends_on)}" if s.depends_on else ""
                sudo_mark = f" {_YELLOW}[sudo]{_RESET}" if s.sudo else ""
                print(f"    {s.name}: {s.command}{sudo_mark}{deps}")
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

        # 按状态排序
        for status in ["PASS", "FAIL", "ERROR", "SKIP"]:
            for s in result.suites:
                if s.status != status:
                    continue
                self._print_suite_terminal(s)

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

        line = f"  {color}{icon}{_RESET} {level_tag}{suite.suite_name}{duration}{msg}"
        print(line)

        # 详细模式显示指标
        if self.verbose and suite.metrics:
            for k, v in suite.metrics.items():
                print(f"      {_DIM}{k}={v}{_RESET}")

        # 详细模式显示资源
        if self.verbose and suite.resource_peak.sample_count > 0:
            rp = suite.resource_peak
            print(f"      {_DIM}mem: peak={rp.peak_memory_mb}MB "
                  f"avg={rp.avg_memory_mb}MB | "
                  f"fd: peak={rp.peak_fd_count} | "
                  f"cpu: peak={rp.peak_cpu_pct}%{_RESET}")

        # 显示 setup/teardown 错误（始终显示，非 verbose 也展示关键信息）
        if suite.setup_error:
            print(f"      {_YELLOW}⚠ setup: {suite.setup_error}{_RESET}")
        if suite.teardown_error:
            print(f"      {_YELLOW}⚠ teardown: {suite.teardown_error}{_RESET}")

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
        lines.append("| 状态 | 层级 | 套件 | 耗时 | 说明 |")
        lines.append("|------|------|------|------|------|")
        for s in result.suites:
            icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "○", "ERROR": "⚠"}.get(
                s.status, "?"
            )
            duration = f"{s.duration_ms:.0f}ms" if s.duration_ms > 0 else "-"
            msg = s.message.replace("|", "\\|") if s.message else "-"
            lines.append(f"| {icon} | {s.level} | {s.suite_name} | {duration} | {msg} |")
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
            "| 层级 | 套件 | 状态 | 耗时 | 关键指标 |",
            "|------|------|------|------|----------|",
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

            # 关键指标：优先用 message，fallback 到退出码
            key = s.message.strip() if s.message else f"exit={s.exit_code}"

            # 拼接 setup/teardown 警告
            notes = ""
            if s.setup_error:
                notes += f" ⚠setup"
            if s.teardown_error:
                notes += f" ⚠teardown"

            lines.append(
                f"| {s.level} | {s.suite_name} | {emoji} {s.status} | {dur_str} | {key}{notes} |"
            )

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
    ) -> None:
        """打印历史记录和回归检测结果。"""
        if self.format == "json":
            print(json.dumps({
                "project": project,
                "suite": suite,
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
            }, indent=2, ensure_ascii=False))
            return

        print(f"\n{_BOLD}{project}/{suite} — 历史记录{_RESET}\n")

        if not records:
            print("  (无历史记录)")
            return

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
