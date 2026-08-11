"""CLI 入口 — argparse 子命令。

命令: zigtester scan|run|list|history|init
"""

from __future__ import annotations

import argparse
import os
import sys

from .config import VALID_LEVELS
from .history import check_regression, load_history
from .reporter import Reporter
from .runner import run_project, run_workspace
from .scanner import discover


def _get_default_dir() -> str:
    """获取默认扫描目录 — 环境变量或当前目录。"""
    return os.environ.get("ZIGTESTER_ROOT", os.getcwd())


def cmd_scan(args: argparse.Namespace) -> int:
    """scan 子命令 — 发现项目。"""
    root = args.dir or _get_default_dir()
    projects = discover(root, recursive=not args.no_recursive)

    reporter = Reporter(format=args.report_format)
    reporter.print_scan_result(projects)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """list 子命令 — 列出套件。"""
    root = args.dir or _get_default_dir()
    projects = discover(root, recursive=not args.no_recursive)

    if args.project:
        projects = [p for p in projects if p.name == args.project]
        if not projects:
            print(f"未找到项目: {args.project}", file=sys.stderr)
            return 1

    reporter = Reporter(format=args.report_format)
    for p in projects:
        reporter.print_suite_list(p)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """run 子命令 — 执行测试。"""
    root = args.dir or _get_default_dir()
    projects = discover(root, recursive=not args.no_recursive)

    if args.all:
        target_projects = projects
    elif args.project:
        target_projects = [p for p in projects if p.name == args.project]
        if not target_projects:
            print(f"未找到项目: {args.project}", file=sys.stderr)
            return 1
    else:
        # 默认：当前目录下的项目
        cwd = os.getcwd()
        cfg_path = os.path.join(cwd, "zigtester.yaml")
        if os.path.isfile(cfg_path):
            projects = discover(cwd, recursive=False)
            target_projects = projects
        else:
            # 尝试发现所有项目
            target_projects = projects

    if not target_projects:
        print("未发现任何项目。使用 `zigtester init` 创建配置。", file=sys.stderr)
        return 1

    levels = args.level.split(",") if args.level != "all" else []
    # 校验层级名
    for lv in levels:
        if lv not in VALID_LEVELS:
            print(f"无效层级: {lv}，有效值: {', '.join(VALID_LEVELS)}", file=sys.stderr)
            return 1

    reporter = Reporter(
        format=args.report_format,
        verbose=args.verbose,
    )

    if len(target_projects) == 1:
        pr = run_project(
            target_projects[0], levels,
            fail_fast=args.fail_fast,
            no_build=args.no_build,
            suite_filter=args.suite,
        )
        reporter.print_results(pr)

        if args.json_output:
            reporter.save_json(pr, args.json_output)

        # 保存历史
        try:
            from .history import save_run
            save_run(pr)
        except Exception:
            pass

        all_ok = all(s.status in ("PASS", "SKIP") for s in pr.suites)
        return 0 if all_ok else 1
    else:
        ws = run_workspace(
            target_projects, levels,
            parallel=args.parallel,
            fail_fast=args.fail_fast,
            no_build=args.no_build,
        )
        reporter.print_workspace_results(ws)

        if args.json_output:
            import json
            from datetime import datetime, timezone, timedelta
            _CST = timezone(timedelta(hours=8))
            output = {
                "workspace": True,
                "timestamp": datetime.now(_CST).isoformat(),
                "projects": [],
            }
            for pr in ws.projects:
                import json as _json
                output["projects"].append(_json.loads(reporter.to_json(pr)))
            with open(args.json_output, "w") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
            print(f"  报告已保存: {args.json_output}")

        # 保存历史
        for pr in ws.projects:
            try:
                from .history import save_run
                save_run(pr)
            except Exception:
                pass

        all_ok = all(
            s.status in ("PASS", "SKIP")
            for pr in ws.projects
            for s in pr.suites
        )
        return 0 if all_ok else 1


def cmd_history(args: argparse.Namespace) -> int:
    """history 子命令 — 查看历史。"""
    records = load_history(args.project, args.suite, n=args.limit)

    # 当前指标（最新一条）
    current_metrics = records[0].get("metrics", {}) if records else {}
    current_resource = records[0].get("resource", {}) if records else {}

    regressions = check_regression(current_metrics, records, current_resource=current_resource)

    reporter = Reporter(format=args.report_format)
    reporter.print_history(args.project, args.suite, records, regressions)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """init 子命令 — 生成配置模板。"""
    from .config import generate_template

    target_dir = args.dir or os.getcwd()
    target_path = os.path.join(target_dir, "zigtester.yaml")

    if os.path.exists(target_path) and not args.force:
        print(f"配置文件已存在: {target_path}", file=sys.stderr)
        print("使用 --force 覆盖。", file=sys.stderr)
        return 1

    project_name = args.project or os.path.basename(os.path.abspath(target_dir))
    content = generate_template(project_name)

    os.makedirs(target_dir, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"配置模板已生成: {target_path}")
    if args.report_format == "json":
        import json
        print(json.dumps({"path": target_path, "created": True}))
    return 0


def main() -> None:
    """CLI 主入口。"""
    parser = argparse.ArgumentParser(
        prog="zigtester",
        description="fixnet 生态自动测试框架",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # ── scan ──────────────────────────────────────────────
    p_scan = sub.add_parser("scan", help="扫描项目，发现所有 zigtester.yaml")
    p_scan.add_argument("--dir", help="扫描根目录")
    p_scan.add_argument("--no-recursive", action="store_true",
                        help="不递归扫描子目录")
    p_scan.add_argument("--report-format", default="terminal",
                        choices=["terminal", "markdown", "json"],
                        help="输出格式（默认 terminal）")

    # ── list ──────────────────────────────────────────────
    p_list = sub.add_parser("list", help="列出项目中的测试套件")
    p_list.add_argument("project", nargs="?", help="项目名（可选）")
    p_list.add_argument("--dir", help="扫描根目录")
    p_list.add_argument("--no-recursive", action="store_true",
                        help="不递归扫描子目录")
    p_list.add_argument("--report-format", default="terminal",
                        choices=["terminal", "markdown", "json"],
                        help="输出格式（默认 terminal）")

    # ── run ───────────────────────────────────────────────
    p_run = sub.add_parser("run", help="运行测试")
    p_run.add_argument("project", nargs="?", help="项目名")
    p_run.add_argument("--dir", help="扫描根目录")
    p_run.add_argument("--no-recursive", action="store_true",
                       help="不递归扫描子目录")
    p_run.add_argument("--level", default="all",
                       help=f"测试层级，逗号分隔 ({', '.join(VALID_LEVELS)}, all)")
    p_run.add_argument("--suite", help="仅运行指定套件（自动包含其依赖）")
    p_run.add_argument("--all", dest="all", action="store_true",
                       help="运行所有已发现项目")
    p_run.add_argument("--report-format", default="terminal",
                       choices=["terminal", "markdown", "json"],
                       help="输出格式（默认 terminal）")
    p_run.add_argument("--json-output", help="JSON 输出路径")
    p_run.add_argument("--no-build", action="store_true",
                       help="跳过构建步骤")
    p_run.add_argument("--verbose", action="store_true",
                       help="详细输出")
    p_run.add_argument("--fail-fast", action="store_true",
                       help="首个失败即停止")
    p_run.add_argument("--parallel", action="store_true",
                       help="并行执行多个项目（仅 --all 模式有效）")

    # ── history ───────────────────────────────────────────
    p_hist = sub.add_parser("history", help="查看性能历史")
    p_hist.add_argument("project", help="项目名")
    p_hist.add_argument("suite", help="套件名")
    p_hist.add_argument("--limit", type=int, default=10,
                        help="显示记录数（默认 10）")
    p_hist.add_argument("--report-format", default="terminal",
                        choices=["terminal", "markdown", "json"],
                        help="输出格式（默认 terminal）")

    # ── init ──────────────────────────────────────────────
    p_init = sub.add_parser("init", help="生成初始 zigtester.yaml")
    p_init.add_argument("--dir", help="目标目录（默认当前目录）")
    p_init.add_argument("--project", help="项目名（默认目录名）")
    p_init.add_argument("--force", action="store_true",
                        help="覆盖已有文件")
    p_init.add_argument("--report-format", default="terminal",
                        choices=["terminal", "markdown", "json"],
                        help="输出格式（默认 terminal）")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    handlers = {
        "scan": cmd_scan,
        "list": cmd_list,
        "run": cmd_run,
        "history": cmd_history,
        "init": cmd_init,
    }

    handler = handlers.get(args.command)
    if handler is None:
        print(f"未知命令: {args.command}", file=sys.stderr)
        sys.exit(1)

    try:
        rc = handler(args)
        sys.exit(rc)
    except KeyboardInterrupt:
        print("\n中断", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
