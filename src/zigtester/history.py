"""历史存储 + 回归检测。

存储路径：~/.zigtester/history.db（SQLite，单文件）
每个套件最多保留 30 次历史记录。

迁移：首次访问时自动从旧版 JSON 文件（~/.zigtester/history/<project>/<suite>/*.json）
迁移到 SQLite。迁移后 JSON 文件保留不删（安全回退）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .config import ProjectResult, Regression, SuiteResult

# 北京时间
_CST = timezone(timedelta(hours=8))
_MAX_HISTORY = 30

# SQLite 路径
_DB_PATH = Path.home() / ".zigtester" / "history.db"
# 旧版 JSON 数据路径（迁移源）
_JSON_HISTORY_DIR = Path.home() / ".zigtester" / "history"

# 连接缓存（线程本地，避免 WAL 模式下多线程竞争）
_tls_db: threading.local = threading.local()


# ── 数据库基础设施 ──────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    """获取线程本地的 SQLite 连接（自动建表 + 迁移）。"""
    db = getattr(_tls_db, "conn", None)
    if db is not None:
        return db

    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(str(_DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = sqlite3.Row

    _ensure_schema(db)
    _migrate_json_to_sqlite(db)

    _tls_db.conn = db
    return db


def _ensure_schema(db: sqlite3.Connection) -> None:
    """创建表和索引（幂等）。"""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,         -- UUID v4
            name TEXT NOT NULL,          -- 人类可读名（project 字段）
            path TEXT                    -- 最近一次执行路径（信息性）
        );

        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL REFERENCES projects(id),
            suite TEXT NOT NULL,
            level TEXT NOT NULL,
            timestamp TEXT NOT NULL,     -- ISO 8601
            status TEXT NOT NULL,
            duration_ms REAL,
            exit_code INTEGER,
            metrics_json TEXT,           -- JSON blob（灵活容纳不同 parser 的指标）
            resource_json TEXT,          -- JSON blob {peak_memory_mb, peak_fd, peak_cpu_pct}
            message TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_runs_project_suite
            ON runs(project_id, suite);
        CREATE INDEX IF NOT EXISTS idx_runs_timestamp
            ON runs(timestamp);
        CREATE INDEX IF NOT EXISTS idx_projects_name
            ON projects(name);
    """)


# ── JSON → SQLite 迁移 ──────────────────────────────────────

def _migrate_json_to_sqlite(db: sqlite3.Connection) -> None:
    """一次性迁移：将旧版 JSON 文件导入 SQLite。

    仅在 projects 表为空且 JSON 目录存在时执行。
    迁移后 JSON 文件保留不删。
    """
    # 检查是否已迁移（projects 表非空）
    row = db.execute("SELECT COUNT(*) as cnt FROM projects").fetchone()
    if row["cnt"] > 0:
        return

    if not _JSON_HISTORY_DIR.is_dir():
        return

    # 收集所有 JSON 文件
    json_files: list[Path] = []
    for proj_dir in _JSON_HISTORY_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        for suite_dir in proj_dir.iterdir():
            if not suite_dir.is_dir():
                continue
            json_files.extend(suite_dir.glob("*.json"))

    if not json_files:
        return

    # 项目名 → UUID 映射（旧数据没有 UUID，按项目名生成确定性 UUID）
    import uuid as _uuid
    project_ids: dict[str, str] = {}

    def _name_to_uuid(name: str) -> str:
        """从项目名生成确定性 UUID（namespace DNS + name）。"""
        if name not in project_ids:
            project_ids[name] = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"{name}.zigtester"))
        return project_ids[name]

    records_by_project: dict[str, list[dict]] = {}
    for fp in json_files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        proj_name = record.get("project", "unknown")
        records_by_project.setdefault(proj_name, []).append(record)

    for proj_name, records in records_by_project.items():
        pid = _name_to_uuid(proj_name)
        # 尝试从现有 zigtester.yaml 读取真实 UUID（如果项目已被 init）
        db.execute(
            "INSERT OR IGNORE INTO projects(id, name, path) VALUES(?, ?, ?)",
            (pid, proj_name, ""),
        )

        for rec in records:
            db.execute(
                """INSERT INTO runs
                   (project_id, suite, level, timestamp, status,
                    duration_ms, exit_code, metrics_json, resource_json, message)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pid,
                    rec.get("suite", ""),
                    rec.get("level", ""),
                    rec.get("timestamp", ""),
                    rec.get("status", "SKIP"),
                    rec.get("duration_ms", 0),
                    rec.get("exit_code"),
                    json.dumps(rec.get("metrics", {}), ensure_ascii=False),
                    json.dumps(rec.get("resource", {}), ensure_ascii=False),
                    rec.get("message", ""),
                ),
            )

    db.commit()


# ── 公开 API ────────────────────────────────────────────────

def _resolve_project_id(db: sqlite3.Connection, name_or_id: str) -> str | None:
    """将项目名或 UUID 解析为 UUID。优先精确匹配 UUID，再按名称查。"""
    # 尝试 UUID 精确匹配
    row = db.execute("SELECT id FROM projects WHERE id = ?", (name_or_id,)).fetchone()
    if row is not None:
        return row["id"]
    # 按名称查找
    row = db.execute("SELECT id FROM projects WHERE name = ?", (name_or_id,)).fetchone()
    if row is not None:
        return row["id"]
    # 前缀匹配（UUID 前 8 位即可唯一标识）
    if len(name_or_id) >= 8:
        row = db.execute(
            "SELECT id FROM projects WHERE id LIKE ?",
            (name_or_id + "%",),
        ).fetchone()
        if row is not None:
            return row["id"]
    return None


def save_run(result: ProjectResult, project_id: str, project_path: str = "") -> str:
    """保存一次项目运行结果到 SQLite。

    Args:
        result: 项目执行结果
        project_id: 项目 UUID
        project_path: 项目路径（信息性，记录最近位置）

    Returns:
        DB 路径
    """
    db = _get_db()
    now = datetime.now(_CST).isoformat()

    # 更新项目记录（插入或更新路径）
    db.execute(
        """INSERT INTO projects(id, name, path) VALUES(?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET path = excluded.path, name = excluded.name""",
        (project_id, result.project, project_path),
    )

    for suite in result.suites:
        metrics_json = json.dumps(suite.metrics, ensure_ascii=False)
        resource_data = {
            "peak_memory_mb": suite.resource_peak.peak_memory_mb,
            "peak_fd": suite.resource_peak.peak_fd_count,
            "peak_cpu_pct": suite.resource_peak.peak_cpu_pct,
        }
        resource_json = json.dumps(resource_data, ensure_ascii=False)

        db.execute(
            """INSERT INTO runs
               (project_id, suite, level, timestamp, status,
                duration_ms, exit_code, metrics_json, resource_json, message)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                suite.suite_name,
                suite.level,
                now,
                suite.status,
                suite.duration_ms,
                suite.exit_code,
                metrics_json,
                resource_json,
                suite.message,
            ),
        )

        # 清理旧记录（每套件最多 _MAX_HISTORY 条）
        _prune_suite(db, project_id, suite.suite_name)

    db.commit()
    return str(_DB_PATH)


def _prune_suite(db: sqlite3.Connection, project_id: str, suite: str) -> None:
    """删除超出 _MAX_HISTORY 的旧记录。"""
    db.execute(
        """DELETE FROM runs WHERE id IN (
               SELECT id FROM runs
               WHERE project_id = ? AND suite = ?
               ORDER BY timestamp DESC
               LIMIT -1 OFFSET ?
           )""",
        (project_id, suite, _MAX_HISTORY),
    )


def load_history(project: str, suite: str, n: int = 30) -> list[dict]:
    """加载指定套件的历史记录，按时间降序排列。

    Args:
        project: 项目名或 UUID
        suite: 套件名
        n: 返回最近 N 条记录

    Returns:
        历史记录列表（最新在前），每条与旧 JSON 格式兼容：
        {project, suite, level, timestamp, status, duration_ms,
         exit_code, metrics, resource, message}
    """
    db = _get_db()
    pid = _resolve_project_id(db, project)
    if pid is None:
        return []

    rows = db.execute(
        """SELECT r.*, p.name as project_name FROM runs r
           JOIN projects p ON r.project_id = p.id
           WHERE r.project_id = ? AND r.suite = ?
           ORDER BY r.timestamp DESC
           LIMIT ?""",
        (pid, suite, n),
    ).fetchall()

    records: list[dict] = []
    for row in rows:
        try:
            metrics = json.loads(row["metrics_json"])
        except (json.JSONDecodeError, TypeError):
            metrics = {}
        try:
            resource = json.loads(row["resource_json"])
        except (json.JSONDecodeError, TypeError):
            resource = {}

        records.append({
            "project": row["project_name"],
            "suite": row["suite"],
            "level": row["level"],
            "timestamp": row["timestamp"],
            "status": row["status"],
            "duration_ms": row["duration_ms"],
            "exit_code": row["exit_code"],
            "metrics": metrics,
            "resource": resource,
            "message": row["message"] or "",
        })

    return records


def list_projects() -> list[dict]:
    """列出所有已知项目（从 projects 表）。

    Returns:
        [{id, name, path}, ...]
    """
    db = _get_db()
    rows = db.execute(
        "SELECT id, name, path FROM projects ORDER BY name"
    ).fetchall()
    return [{"id": row["id"], "name": row["name"], "path": row["path"]} for row in rows]


# ── 回归检测 ────────────────────────────────────────────────

def check_regression(
    current: dict[str, float],
    history: list[dict],
    threshold_pct: float = 20.0,
    current_resource: dict[str, float] | None = None,
) -> list[Regression]:
    """检查当前指标相对于历史基线是否退化。

    基线 = 最近 5 次（或更少）的移动平均。
    若某指标 > threshold_pct 的退化，标记为 REGRESSION。

    Args:
        current: 当前性能指标字典 {metric_name: value}
        history: load_history() 返回的历史记录（最新在前）
        threshold_pct: 退化百分比阈值（默认 20%）
        current_resource: 当前资源指标 {peak_memory_mb, peak_fd, peak_cpu_pct}
                          （可选，传入后一并检测资源回归）

    Returns:
        Regression 列表（仅 is_regression=True 的条目）
    """
    if not history:
        return []

    regressions: list[Regression] = []

    # 取最近 5 次（或全部）作为基线
    baseline_window = history[:5]

    # ── 性能指标回归 ──
    all_metric_names: set[str] = set()
    for r in baseline_window:
        metrics = r.get("metrics", {})
        if isinstance(metrics, dict):
            all_metric_names.update(metrics.keys())

    for metric_name in all_metric_names:
        cur_val = current.get(metric_name)
        if cur_val is None:
            continue

        baseline_values: list[float] = []
        for r in baseline_window:
            m = r.get("metrics", {})
            if isinstance(m, dict) and metric_name in m:
                try:
                    baseline_values.append(float(m[metric_name]))
                except (ValueError, TypeError):
                    pass

        if not baseline_values:
            continue

        baseline_avg = sum(baseline_values) / len(baseline_values)
        if baseline_avg == 0:
            continue

        pct_change = ((cur_val - baseline_avg) / abs(baseline_avg)) * 100
        is_regression = _is_metric_regression(metric_name, pct_change, threshold_pct)

        if is_regression:
            regressions.append(Regression(
                metric=metric_name,
                current=round(cur_val, 2),
                baseline_avg=round(baseline_avg, 2),
                pct_change=round(pct_change, 1),
                is_regression=True,
            ))

    # ── 资源指标回归 ──
    # history 存储格式：{peak_memory_mb, peak_fd, peak_cpu_pct}
    # 资源指标方向：升高 = 退化（更多内存、更多 FD、更高 CPU）
    if current_resource:
        for hist_key in ("peak_memory_mb", "peak_fd", "peak_cpu_pct"):
            cur_val = current_resource.get(hist_key)
            if cur_val is None or cur_val == 0:
                continue

            baseline_values: list[float] = []
            for r in baseline_window:
                res = r.get("resource", {})
                if isinstance(res, dict) and hist_key in res:
                    try:
                        val = float(res[hist_key])
                        if val > 0:
                            baseline_values.append(val)
                    except (ValueError, TypeError):
                        pass

            if len(baseline_values) < 2:
                continue

            baseline_avg = sum(baseline_values) / len(baseline_values)
            if baseline_avg == 0:
                continue

            pct_change = ((cur_val - baseline_avg) / baseline_avg) * 100
            is_regression = pct_change > threshold_pct

            if is_regression:
                # 用户可读的指标名
                display_name = {
                    "peak_memory_mb": "peak_memory_mb",
                    "peak_fd": "peak_fd_count",
                    "peak_cpu_pct": "peak_cpu_pct",
                }.get(hist_key, hist_key)
                regressions.append(Regression(
                    metric=display_name,
                    current=round(cur_val, 2),
                    baseline_avg=round(baseline_avg, 2),
                    pct_change=round(pct_change, 1),
                    is_regression=True,
                ))

    return [r for r in regressions if r.is_regression]


def _is_metric_regression(
    metric_name: str, pct_change: float, threshold_pct: float,
) -> bool:
    """判断性能指标是否构成退化。

    吞吐类（throughput/reqs/rate）：下降 = 退化
    延迟/错误类（latency/error/failed/duration）：升高 = 退化
    其他：双向检测
    """
    if abs(pct_change) <= threshold_pct:
        return False

    if any(kw in metric_name for kw in ("latency", "error", "failed", "duration")):
        return pct_change > 0

    if any(kw in metric_name for kw in ("throughput", "reqs", "rate", "passed", "total")):
        return pct_change < 0

    # 默认双向检测
    return True
