"""MCP Server — FastMCP stdio transport。

提供 5 个工具供 Claude Code 调用：
  zigtester_scan / zigtester_run / zigtester_list / zigtester_history / zigtester_init

服务端解析原始测试输出，只向 Claude 返回结构化摘要，最大化节省 token。
"""

from __future__ import annotations

import os

from fastmcp import FastMCP

mcp = FastMCP("zigtester")


def _default_dir() -> str:
    return os.environ.get("ZIGTESTER_ROOT", os.getcwd())


# ── 工具 ──────────────────────────────────────────────────


@mcp.tool()
def zigtester_scan(dir: str | None = None) -> dict:
    """发现所有包含 zigtester.yaml 的项目。

    Args:
        dir: 扫描根目录，默认从 ZIGTESTER_ROOT 环境变量或当前目录
    """
    from .scanner import discover

    root = dir or _default_dir()
    projects = discover(root)

    return {
        "root": root,
        "count": len(projects),
        "projects": [
            {
                "name": p.name,
                "path": p.path,
                "description": p.config.description,
                "levels": p.levels,
            }
            for p in projects
        ],
    }


@mcp.tool()
def zigtester_list(project: str) -> dict:
    """列出项目中的所有测试套件。

    Args:
        project: 项目名
    """
    from .scanner import discover

    root = _default_dir()
    projects = [p for p in discover(root) if p.name == project]

    if not projects:
        return {"error": f"未找到项目: {project}"}

    p = projects[0]
    levels: dict[str, list[dict]] = {}
    for name, lc in p.config.levels.items():
        if lc.suites:
            levels[name] = [
                {
                    "name": s.name,
                    "command": s.command,
                    "timeout": s.timeout,
                    "sudo": s.sudo,
                    "parser": s.parser,
                    "depends_on": s.depends_on,
                }
                for s in lc.suites
            ]

    return {
        "project": p.name,
        "description": p.config.description,
        "path": p.path,
        "levels": levels,
    }


@mcp.tool()
def zigtester_run(
    project: str,
    level: str = "all",
    suite: str | None = None,
) -> dict:
    """执行测试并返回结构化结果。

    服务端解析原始 stdout/stderr，只返回结构化摘要 —
    不将大量原始输出传给 Claude，大幅节省 token。

    Args:
        project: 项目名
        level: 测试层级 (unit/functional/performance/stress/all)，默认 all
        suite: 指定套件名（可选，不指定则运行该层级所有套件）
    """
    from .config import VALID_LEVELS
    from .runner import run_project
    from .scanner import discover

    root = _default_dir()
    projects = [p for p in discover(root) if p.name == project]

    if not projects:
        return {"error": f"未找到项目: {project}"}

    levels = []
    if level != "all":
        if level not in VALID_LEVELS:
            return {"error": f"无效层级: {level}，有效值: {', '.join(VALID_LEVELS)}"}
        levels = [level]

    pr = run_project(projects[0], levels)

    # 保存历史
    try:
        from .history import save_run
        save_run(pr)
    except Exception:
        pass

    # 构建精简响应 — 不含 stdout/stderr 原文
    suites_out = []
    for s in pr.suites:
        entry: dict = {
            "name": s.suite_name,
            "level": s.level,
            "status": s.status,
            "duration_ms": round(s.duration_ms, 1),
            "exit_code": s.exit_code,
            "metrics": s.metrics,
            "message": s.message,
        }
        # 仅失败时包含 stderr 摘要（最多 500 字符）
        if s.status in ("FAIL", "ERROR") and s.stderr:
            entry["stderr_tail"] = s.stderr[-500:]
        # 资源信息（仅 performance/stress）
        if s.resource_peak.sample_count > 0:
            entry["resource"] = {
                "peak_memory_mb": s.resource_peak.peak_memory_mb,
                "avg_memory_mb": s.resource_peak.avg_memory_mb,
                "peak_fd": s.resource_peak.peak_fd_count,
                "peak_cpu_pct": s.resource_peak.peak_cpu_pct,
            }
        suites_out.append(entry)

    elapsed = pr.finished_at - pr.started_at
    all_pass = sum(1 for s in pr.suites if s.status == "PASS")
    all_fail = sum(1 for s in pr.suites if s.status == "FAIL")
    all_err = sum(1 for s in pr.suites if s.status == "ERROR")
    all_skip = sum(1 for s in pr.suites if s.status == "SKIP")

    return {
        "project": pr.project,
        "elapsed_s": round(elapsed, 1),
        "summary": {
            "total": len(pr.suites),
            "passed": all_pass,
            "failed": all_fail,
            "skipped": all_skip,
            "errors": all_err,
        },
        "suites": suites_out,
    }


@mcp.tool()
def zigtester_history(
    project: str,
    suite: str,
    limit: int = 10,
) -> dict:
    """查看性能历史 + 回归检测。

    只返回趋势摘要和异常标记，不返回完整历史数据。

    Args:
        project: 项目名
        suite: 套件名
        limit: 返回记录数（默认 10）
    """
    from .history import check_regression, load_history

    records = load_history(project, suite, n=limit)
    current_metrics = records[0].get("metrics", {}) if records else {}

    regressions = check_regression(current_metrics, records)

    # 精简历史（只保留时间戳和指标）
    runs_out = []
    for r in records[:limit]:
        runs_out.append({
            "timestamp": r.get("timestamp", ""),
            "status": r.get("status"),
            "duration_ms": r.get("duration_ms"),
            "metrics": r.get("metrics", {}),
        })

    return {
        "project": project,
        "suite": suite,
        "runs": runs_out,
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


@mcp.tool()
def zigtester_init(dir: str, project: str) -> dict:
    """为项目生成初始 zigtester.yaml 配置模板。

    Args:
        dir: 项目目录路径
        project: 项目名
    """
    import os as _os

    from .config import generate_template

    target_path = _os.path.join(dir, "zigtester.yaml")

    if _os.path.exists(target_path):
        return {
            "path": target_path,
            "created": False,
            "error": "配置文件已存在，使用 --force 覆盖",
        }

    content = generate_template(project)
    _os.makedirs(dir, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(content)

    return {
        "path": target_path,
        "created": True,
    }


def main():
    """MCP Server 入口 — stdio transport。"""
    mcp.run()


if __name__ == "__main__":
    main()
