"""MCP Server — FastMCP HTTP transport。

提供 5 个工具供 Claude Code 调用：
  zigtester_scan / zigtester_run / zigtester_list / zigtester_history / zigtester_init

服务端解析原始测试输出，只向 Claude 返回结构化摘要，最大化节省 token。

使用 HTTP transport（端口绑定天然互斥，解决 stdio 多实例问题）：
  python -m zigtester.server                  # 默认 127.0.0.1:9020
  ZIGTESTER_PORT=9021 python -m zigtester.server  # 自定义端口
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

from fastmcp import Context, FastMCP


def _augment_path() -> None:
    """补齐 PATH——launchd 启动的服务继承最小 PATH（/usr/bin:/bin:...），
    导致插件/suite 子进程找不到 go/sing-box/xray/homebrew python。

    在 server 启动时把常见二进制目录补到 PATH 头部；runner.py / plugin.py
    的 `dict(os.environ)` 会原样继承补全后的 PATH。
    """
    parts = [p for p in os.environ.get("PATH", "").split(":") if p]
    extras = [
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        "/usr/local/sbin",
        "/usr/local/go/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    for extra in extras:
        if extra and extra not in parts and os.path.isdir(extra):
            parts.insert(0, extra)
    os.environ["PATH"] = ":".join(parts)


_augment_path()

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 9020

# Server 级说明 — initialize 时下发给客户端，是 agent 对 zigtester 的第一手认知
_INSTRUCTIONS = """zigtester — fixnet 生态统一测试框架，所有 zig* 项目测试的唯一入口。

核心约定（必须遵守）：
1. 所有测试一律经本框架执行（zigtester_run）。禁止直接运行项目测试脚本
   （python3 tests/...）——脚本只探测依赖服务不负责启动，直跑必然报错或产生假结果。
2. 测试依赖进程（local-echo / sing-box / xray-core）由插件统一启停。禁止任何
   会话手动启停：sing-box run / xray run / 直接运行 local-echo / pkill 插件进程名，均禁止。
3. 每个测试套件执行前自动自检插件环境（进程存活 + 端口归属），被破坏时自动
   清理恢复；恢复失败则该套件 ERROR，setup_error 含环境规范全文——此时唯一
   正确动作是重新调用 zigtester_run，不要手工修复或手动启停插件。

工具选择：
- 不确定有哪些项目/项目路径 → zigtester_scan（了解后不再需要）
- 查看项目测试套件/层级/套件名 → zigtester_list
- 运行测试 / 单套件最小复现 → zigtester_run
- 性能趋势 / 回归判断 / flaky 识别 → zigtester_history

诊断提示：本机若配置 HTTP_PROXY，curl 访问 127.0.0.1 需加 --noproxy '*'。"""

mcp = FastMCP("zigtester", instructions=_INSTRUCTIONS)


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


_HEARTBEAT_INTERVAL_S = 10.0


async def _run_with_progress(ctx: Context, runner) -> Any:
    """在线程池执行同步 runner，事件循环周期发 progress 通知保活。

    `run_project` 是同步阻塞的（子进程 `communicate`）。若直接在 async 工具
    里同步调用会阻塞事件循环，心跳无法并发。因此用 `asyncio.to_thread` 放到
    线程池，主循环每 `_HEARTBEAT_INTERVAL_S` 秒发一次 `report_progress`
    （progress 单调递增 + message 已运行秒数），既满足 MCP 协议进度语义
    （progress 值 MUST increase），也让客户端在长任务期间持续收到 SSE 事件、
    避免 idle 超时。
    """
    run_task = asyncio.create_task(asyncio.to_thread(runner))

    async def _heartbeat() -> None:
        counter = 0
        start = time.monotonic()
        while not run_task.done():
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
            if run_task.done():
                break
            counter += 1
            elapsed = int(time.monotonic() - start)
            try:
                await ctx.report_progress(
                    counter, None, f"tests running ({elapsed}s elapsed)"
                )
            except Exception:
                # 进度通知失败（如客户端未带 progressToken）不影响测试执行
                pass

    hb_task = asyncio.create_task(_heartbeat())
    try:
        return await run_task
    finally:
        hb_task.cancel()


# ── 工具 ──────────────────────────────────────────────────


@mcp.tool()
def zigtester_scan(dir: str | None = None) -> dict:
    """发现目录树下所有接入 zigtester 的项目（名称 + 路径 + 层级概览）。

    仅在不确定有哪些项目/项目路径时使用一次；了解项目后直接用
    zigtester_list / zigtester_run 传项目路径即可，不要每次都 scan。
    返回的 path 是后续工具 dir 参数应传的值。

    Args:
        dir: 扫描根目录（默认当前目录；日常用 fixnet 工作区根目录）
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
    """列出项目的全部测试套件（按 unit/functional/performance/stress 分组）。

    运行测试前先用它确认：层级名、套件名、套件是否需要 sudo——
    zigtester_run 的 level/suite 参数必须与此处名称一致。
    套件名也是 zigtester_history 的 suite 参数来源。

    Args:
        dir: 项目目录路径（或其任意子目录，自动向上查找 zigtester.yaml），默认当前目录
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
async def zigtester_run(
    level: str = "all",
    suite: str | None = None,
    dir: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """执行测试并返回结构化结果 — 所有测试的唯一入口。

    服务端自动完成：依赖插件启停（local-echo/sing-box/xray-core）、
    每个套件执行前环境自检（进程存活+端口归属）、环境被破坏时自动恢复。

    结果解读：
    - `report` 字段是预格式化 Markdown 表格，**必须原样展示给用户**，不要改写摘抄
    - 失败套件（FAIL）→ 看 `failure_lines`（具体失败用例行）和 `stderr_tail` 定位根因
    - ERROR 套件 → 通常是环境问题而非代码问题；`setup_error` 含环境规范全文，
      唯一正确动作是重新调用本工具（自动恢复环境），禁止手动启停插件进程
    - SKIP = 前序失败/环境缺失联动跳过
    - 单套件最小复现：suite 传套件名（自动含其依赖），比全量更快定位问题

    Args:
        level: 测试层级 unit/functional/performance/stress/all，默认 all
        suite: 仅运行指定套件（名称先经 zigtester_list 查询），可选
        dir: 项目目录路径（或其任意子目录，自动向上查找 zigtester.yaml），默认当前目录
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

    if ctx is not None:
        # 长任务：线程池跑 run_project，事件循环发 progress 心跳保活，
        # 避免同步阻塞导致 MCP 客户端超时。
        pr = await _run_with_progress(
            ctx, lambda: run_project(p, levels, suite_filter=suite)
        )
    else:
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
        # 仅失败时包含失败用例行（数据源 stdout）和 stderr 摘要（最多 500 字符）
        if s.status in ("FAIL", "ERROR"):
            from .reporter import extract_failure_lines
            failure_lines = extract_failure_lines(s.stdout)
            if failure_lines:
                entry["failure_lines"] = failure_lines
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
    """查看套件历史趋势 + 回归检测 + flaky 识别。

    适用场景：性能优化前后对比、判断指标是否退化、确认结果是否稳定。
    - regressions 中 is_regression=true 表示当前值显著偏离历史基线（基线只
      统计 PASS 记录，环境破坏数据不污染）
    - flaky=true 表示近期 PASS/FAIL 反复翻转——该套件结果不稳定，
      单次运行不足为凭，需多次确认后再下结论

    Args:
        project: 项目名（zigtester_scan 返回的 name）
        suite: 套件名（zigtester_list 返回的名称）
        limit: 返回记录数（默认 10）
    """
    from .history import check_regression, detect_flaky, load_history

    records = load_history(project, suite, n=limit)
    current_metrics = records[0].get("metrics", {}) if records else {}
    current_resource = records[0].get("resource", {}) if records else {}

    regressions = check_regression(current_metrics, records, current_resource=current_resource)
    flaky = detect_flaky(records)

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
        "flaky": flaky,
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
    """为项目生成初始 zigtester.yaml 配置模板（新项目接入 zigtester 用）。

    生成后按项目实际测试命令编辑模板再使用；已有配置时不会覆盖。
    更推荐交互式接入：见 zigtester 仓库的 create-tester skill。

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
        # json_response=False → 走 SSE 流：立即发 priming event（首字节秒到），
        # progress 通知作为 SSE 事件流式下发。若用 True，mcp SDK 会吞掉所有
        # progress 通知、只在最终 response 才返回单 JSON，长任务会撞客户端
        # per-request 超时。
        mcp.run(transport="http", host=host, port=port, stateless_http=True, json_response=False)
    finally:
        _cleanup_pid()
        print(f"[zigtester] MCP Server 已停止 (PID {pid})", file=sys.stderr)


if __name__ == "__main__":
    main()
