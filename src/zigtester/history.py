"""历史存储 + 回归检测。

存储路径：~/.zigtester/history/<project>/<suite>/<timestamp>.json
每个套件最多保留 30 次历史记录。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .config import ProjectResult, Regression, SuiteResult

# 北京时间
_CST = timezone(timedelta(hours=8))
_MAX_HISTORY = 30


def _history_dir() -> Path:
    """历史记录根目录。"""
    return Path.home() / ".zigtester" / "history"


def save_run(result: ProjectResult) -> str:
    """保存一次项目运行结果到历史存储。

    Returns:
        存储目录路径
    """
    timestamp = datetime.now(_CST).strftime("%Y%m%dT%H%M%S")
    base = _history_dir() / result.project

    for suite in result.suites:
        suite_dir = base / suite.suite_name
        suite_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "project": result.project,
            "suite": suite.suite_name,
            "level": suite.level,
            "timestamp": datetime.now(_CST).isoformat(),
            "status": suite.status,
            "duration_ms": suite.duration_ms,
            "exit_code": suite.exit_code,
            "metrics": suite.metrics,
            "resource": {
                "peak_memory_mb": suite.resource_peak.peak_memory_mb,
                "peak_fd": suite.resource_peak.peak_fd_count,
                "peak_cpu_pct": suite.resource_peak.peak_cpu_pct,
            },
            "message": suite.message,
        }

        filepath = suite_dir / f"{timestamp}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)

        # 清理旧记录（保留最近 MAX_HISTORY 条）
        _prune(suite_dir)

    return str(base)


def _prune(suite_dir: Path) -> None:
    """清理旧记录，只保留最近 _MAX_HISTORY 个文件。"""
    files = sorted(suite_dir.glob("*.json"))
    if len(files) > _MAX_HISTORY:
        for old in files[:-1 * _MAX_HISTORY]:
            old.unlink(missing_ok=True)


def load_history(project: str, suite: str, n: int = 30) -> list[dict]:
    """加载指定套件的历史记录，按时间降序排列。

    Args:
        project: 项目名
        suite: 套件名
        n: 返回最近 N 条记录

    Returns:
        历史记录列表（最新在前）
    """
    suite_dir = _history_dir() / project / suite
    if not suite_dir.is_dir():
        return []

    files = sorted(suite_dir.glob("*.json"), reverse=True)[:n]
    records: list[dict] = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                records.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass

    return records


def check_regression(
    current: dict[str, float],
    history: list[dict],
    threshold_pct: float = 20.0,
) -> list[Regression]:
    """检查当前指标相对于历史基线是否退化。

    基线 = 最近 5 次（或更少）的移动平均。
    若某指标 > threshold_pct 的退化，标记为 REGRESSION。

    Args:
        current: 当前指标字典 {metric_name: value}
        history: load_history() 返回的历史记录（最新在前）
        threshold_pct: 退化百分比阈值（默认 20%）

    Returns:
        Regression 列表
    """
    if not history:
        return []

    regressions: list[Regression] = []

    # 取最近 5 次（或全部）作为基线
    baseline_window = history[:5]

    # 从历史记录中提取指标
    # 所有记录的指标汇总
    all_metric_names: set[str] = set()
    for r in baseline_window:
        metrics = r.get("metrics", {})
        if isinstance(metrics, dict):
            all_metric_names.update(metrics.keys())

    for metric_name in all_metric_names:
        cur_val = current.get(metric_name)
        if cur_val is None:
            continue

        # 收集基线值
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

        # 吞吐类指标：越低越差；延迟类指标：越高越差
        # 这里用 pct_change 的绝对值判断（双向退化检测）
        is_regression = abs(pct_change) > threshold_pct and pct_change < 0

        # 对于延迟/错误类指标，升高也是退化
        if "latency" in metric_name or "error" in metric_name or "failed" in metric_name:
            is_regression = abs(pct_change) > threshold_pct and pct_change > 0

        regressions.append(Regression(
            metric=metric_name,
            current=round(cur_val, 2),
            baseline_avg=round(baseline_avg, 2),
            pct_change=round(pct_change, 1),
            is_regression=is_regression,
        ))

    return [r for r in regressions if r.is_regression]
