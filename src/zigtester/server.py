"""MCP Server — FastMCP HTTP transport。

提供 5 个工具供 Claude Code 调用：
  zigtester_scan / zigtester_run / zigtester_list / zigtester_history / zigtester_init

服务端解析原始测试输出，只向 Claude 返回结构化摘要，最大化节省 token。

使用 HTTP transport（端口绑定天然互斥，解决 stdio 多实例问题）：
  python -m zigtester.server                  # 默认 127.0.0.1:9020
  ZIGTESTER_PORT=9021 python -m zigtester.server  # 自定义端口
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastmcp import FastMCP

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 9020

mcp = FastMCP("zigtester")


# ── 辅助 ──────────────────────────────────────────────────


def _load_project(dir: str | None) -> "DiscoveredProject | None":
    """从 dir 向上查找 zigtester.yaml 并加载为 DiscoveredProject。

    不传 dir 则使用当前工作目录。找不到返回 None。
    """
    from .config import parse_config
    from .scanner import DiscoveredProject, find_config

    target = dir or os.getcwd()
    config_path = find_config(target)
    if config_path is None:
        return None

    cfg = parse_config(config_path)
    return DiscoveredProject(
        name=cfg.project,
        path=os.path.dirname(config_path),
        config_path=config_path,
        config=cfg,
    )


# ── 工具 ──────────────────────────────────────────────────


@mcp.tool()
def zigtester_scan(dir: str | None = None) -> dict:
    """发现目录树下所有包含 zigtester.yaml 的项目（辅助工具）。

    仅在不确定有哪些项目时使用一此，了解项目名后直接用
    zigtester_list / zigtester_run + 项目目录路径即可，
    不需要每次都 scan。

    Args:
        dir: 扫描根目录，默认当前目录
    """
    from .scanner import discover

    root = dir or os.getcwd()
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
def zigtester_list(dir: str | None = None) -> dict:
    """列出项目中的所有测试套件。

    dir 指向项目目录（或其任意子目录），工具自动向上查找
    zigtester.yaml。不传则用当前目录。

    Args:
        dir: 项目目录路径（或其子目录），默认当前目录
    """
    p = _load_project(dir)

    if p is None:
        return {"error": f"在 {dir or os.getcwd()} 及其父目录中未找到 zigtester.yaml"}

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
    level: str = "all",
    suite: str | None = None,
    dir: str | None = None,
) -> dict:
    """执行测试并返回结构化结果。

    dir 指向项目目录（或其任意子目录），工具自动向上查找
    zigtester.yaml。不传则用当前目录。

    返回的 `report` 字段是预格式化的 Markdown 表格，**必须原样展示给用户**，
    不要改写、摘抄或重新排版。

    Args:
        level: 测试层级 (unit/functional/performance/stress/all)，默认 all
        suite: 指定套件名（可选，不指定则运行该层级所有套件）
        dir: 项目目录路径（或其子目录），默认当前目录
    """
    from .config import VALID_LEVELS
    from .reporter import Reporter
    from .runner import run_project

    p = _load_project(dir)

    if p is None:
        return {"error": f"在 {dir or os.getcwd()} 及其父目录中未找到 zigtester.yaml"}

    levels = []
    if level != "all":
        if level not in VALID_LEVELS:
            return {"error": f"无效层级: {level}，有效值: {', '.join(VALID_LEVELS)}"}
        levels = [level]

    pr = run_project(p, levels, suite_filter=suite)

    # 保存历史
    try:
        from .history import save_run
        from .config import ensure_project_id
        pid = ensure_project_id(p.config_path, p.config)
        save_run(pr, pid, p.path)
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
        if s.setup_error:
            entry["setup_error"] = s.setup_error
        if s.teardown_error:
            entry["teardown_error"] = s.teardown_error
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
        "report": Reporter.compact_markdown(pr),
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
    current_resource = records[0].get("resource", {}) if records else {}

    regressions = check_regression(current_metrics, records, current_resource=current_resource)

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


_PID_FILE = Path.home() / ".zigtester" / "server.pid"


def _write_pid() -> int:
    """写入 PID 文件（父目录自动创建）。返回当前 pid。"""
    pid = os.getpid()
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(pid))
    return pid


def _cleanup_pid() -> None:
    """删除 PID 文件（仅当内容匹配当前 pid）。"""
    try:
        if _PID_FILE.exists() and _PID_FILE.read_text().strip() == str(os.getpid()):
            _PID_FILE.unlink()
    except OSError:
        pass


def main():
    """MCP Server 入口 — HTTP transport。"""
    host = os.environ.get("ZIGTESTER_HOST", _DEFAULT_HOST)
    port = int(os.environ.get("ZIGTESTER_PORT", str(_DEFAULT_PORT)))

    pid = _write_pid()
    print(f"[zigtester] MCP Server 启动: http://{host}:{port}  (PID {pid})", file=sys.stderr)

    try:
        mcp.run(transport="http", host=host, port=port)
    finally:
        _cleanup_pid()
        print(f"[zigtester] MCP Server 已停止 (PID {pid})", file=sys.stderr)


if __name__ == "__main__":
    main()
