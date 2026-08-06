"""性能指标提取 — 从 stdout 解析指标、检查阈值、计算分位数。"""

from __future__ import annotations

import re
from typing import Any

from .config import MetricDef, Threshold


class MetricExtractor:
    """从命令 stdout 提取性能指标。"""

    # 内置解析器映射
    BUILTIN = {"zig_test", "line_count", "test_protocols", "bench"}

    def __init__(self, parser_name: str, custom_patterns: list[MetricDef] | None = None):
        self.parser_name = parser_name
        self.custom_patterns = custom_patterns or []

    def extract(self, stdout: str, exit_code: int) -> dict[str, float]:
        """从 stdout 提取指标字典。"""
        base = self._parse_builtin(stdout, exit_code)
        custom = self._parse_custom(stdout)
        base.update(custom)
        return base

    def _parse_builtin(self, stdout: str, exit_code: int) -> dict[str, float]:
        """使用内置解析器。"""
        parser = getattr(self, f"_parse_{self.parser_name}", None)
        if parser is None:
            # 未知解析器 → 退化为 line_count
            return self._parse_line_count(stdout, exit_code)
        return parser(stdout, exit_code)

    def _parse_custom(self, stdout: str) -> dict[str, float]:
        """用正则提取自定义指标。"""
        result: dict[str, float] = {}
        for mdef in self.custom_patterns:
            match = re.search(mdef.pattern, stdout)
            if match:
                try:
                    result[mdef.name] = float(match.group(1))
                except (ValueError, IndexError):
                    pass
        return result

    # ── 内置解析器 ─────────────────────────────────────────

    @staticmethod
    def _parse_zig_test(stdout: str, exit_code: int) -> dict[str, float]:
        """解析 zig build test 输出。

        格式: "X/Y passed; Z skipped" 或 "All X tests passed."
        """
        result: dict[str, float] = {
            "exit_code": float(exit_code),
            "tests_passed": 0.0,
            "tests_total": 0.0,
            "tests_skipped": 0.0,
            "tests_failed": 0.0,
        }

        # 匹配 "123/125 passed; 2 skipped"
        m = re.search(r"(\d+)/(\d+)\s+passed\s*;\s*(\d+)\s+skipped", stdout)
        if m:
            passed = int(m.group(1))
            total = int(m.group(2))
            skipped = int(m.group(3))
            result["tests_passed"] = float(passed)
            result["tests_total"] = float(total)
            result["tests_skipped"] = float(skipped)
            result["tests_failed"] = float(total - passed - skipped)
            return result

        # 匹配 "All X tests passed."
        m = re.search(r"All\s+(\d+)\s+tests?\s+passed", stdout)
        if m:
            total = int(m.group(1))
            result["tests_passed"] = float(total)
            result["tests_total"] = float(total)
            return result

        # 匹配 "X passed; Y skipped; Z failed." (Zig 0.14+ 格式)
        m = re.search(
            r"(\d+)\s+passed\s*;\s*(\d+)\s+skipped\s*;\s*(\d+)\s+failed",
            stdout,
        )
        if m:
            p = int(m.group(1))
            s = int(m.group(2))
            f = int(m.group(3))
            result["tests_passed"] = float(p)
            result["tests_skipped"] = float(s)
            result["tests_failed"] = float(f)
            result["tests_total"] = float(p + f + s)
            return result

        # 匹配 "X passed; Y failed; Z skipped" (备用)
        m_p = re.search(r"(\d+)\s+passed", stdout)
        m_f = re.search(r"(\d+)\s+failed", stdout)
        m_s = re.search(r"(\d+)\s+skipped", stdout)
        if m_p or m_f or m_s:
            p = int(m_p.group(1)) if m_p else 0
            f = int(m_f.group(1)) if m_f else 0
            s = int(m_s.group(1)) if m_s else 0
            result["tests_passed"] = float(p)
            result["tests_failed"] = float(f)
            result["tests_skipped"] = float(s)
            result["tests_total"] = float(p + f + s)

        return result

    @staticmethod
    def _parse_line_count(stdout: str, exit_code: int) -> dict[str, float]:
        """exit 0 → PASS，输出行数作为指标。"""
        lines = [l for l in stdout.splitlines() if l.strip()]
        return {
            "exit_code": float(exit_code),
            "line_count": float(len(lines)),
        }

    @staticmethod
    def _parse_test_protocols(stdout: str, exit_code: int) -> dict[str, float]:
        """解析 zigbox test_protocols.py 输出。

        格式: "总计 X | 通过 Y | 失败 Z | 跳过 W"
        """
        result: dict[str, float] = {
            "exit_code": float(exit_code),
            "tests_total": 0.0,
            "tests_passed": 0.0,
            "tests_failed": 0.0,
            "tests_skipped": 0.0,
            "tests_errors": 0.0,
        }
        m = re.search(
            r"总计\s+(\d+)\s*\|\s*通过\s+(\d+)\s*\|\s*失败\s+(\d+)\s*\|\s*跳过\s+(\d+)",
            stdout,
        )
        if m:
            result["tests_total"] = float(m.group(1))
            result["tests_passed"] = float(m.group(2))
            result["tests_failed"] = float(m.group(3))
            result["tests_skipped"] = float(m.group(4))
        return result

    @staticmethod
    def _parse_bench(stdout: str, exit_code: int) -> dict[str, float]:
        """解析 zigbox test_bench.py 输出，提取吞吐/延迟/传输速率。

        格式样例:
          吞吐: 520.3 req/s
          p50: 12.5ms
          p99: 85.2ms
          传输: 45.6 MB/s
        """
        result: dict[str, float] = {
            "exit_code": float(exit_code),
        }
        patterns: list[tuple[str, str]] = [
            (r"吞吐:\s*([0-9.]+)\s*req/s", "throughput_reqs_per_sec"),
            (r"传输:\s*([0-9.]+)\s*MB/s", "transfer_mb_per_sec"),
            (r"p50:\s*([0-9.]+)\s*ms", "latency_p50_ms"),
            (r"p95:\s*([0-9.]+)\s*ms", "latency_p95_ms"),
            (r"p99:\s*([0-9.]+)\s*ms", "latency_p99_ms"),
            (r"错误:\s*([0-9]+)", "error_count"),
            (r"成功:\s*([0-9]+)", "success_count"),
            (r"总计:\s*([0-9]+)", "total_requests"),
        ]
        for pat, key in patterns:
            m = re.search(pat, stdout)
            if m:
                try:
                    result[key] = float(m.group(1))
                except ValueError:
                    pass
        return result

    # ── 阈值检查 ───────────────────────────────────────────

    @staticmethod
    def check_thresholds(
        metrics: dict[str, float], thresholds: dict[str, Threshold]
    ) -> list[str]:
        """检查指标是否满足阈值，返回违规描述列表。"""
        violations: list[str] = []
        for name, th in thresholds.items():
            val = metrics.get(name)
            if val is None:
                continue
            if th.min is not None and val < th.min:
                violations.append(
                    f"{name}={val} < min={th.min}"
                )
            if th.max is not None and val > th.max:
                violations.append(
                    f"{name}={val} > max={th.max}"
                )
        return violations

    # ── 分位数计算 ─────────────────────────────────────────

    @staticmethod
    def calc_percentile(values: list[float], p: float) -> float:
        """计算百分位数（线性插值）。

        Args:
            values: 数值列表
            p: 百分位 (0-100)

        Returns:
            百分位数值
        """
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        idx = (p / 100.0) * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])
