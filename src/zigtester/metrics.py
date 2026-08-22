"""性能指标提取 — 从 stdout 解析指标、检查阈值、计算分位数。"""

from __future__ import annotations

import re
from typing import Any

from .config import MetricDef, Threshold


# zigbox test_bench.py + zigoutbounds test_engine.py 的 bench 指标提取正则。
# 单档用 re.search 对全 stdout 取首匹配;多档(并发扫描)按行提取存 {key}_c{N}。
_BENCH_PATTERNS: list[tuple[str, str]] = [
    (r"吞吐:\s*([0-9.]+)\s*req/s", "throughput_reqs_per_sec"),
    (r"传输:\s*([0-9.]+)\s*MB/s", "transfer_mb_per_sec"),
    (r"p50:\s*([0-9.]+)\s*ms", "latency_p50_ms"),
    (r"p95:\s*([0-9.]+)\s*ms", "latency_p95_ms"),
    (r"p99:\s*([0-9.]+)\s*ms", "latency_p99_ms"),
    (r"p50:\s+([0-9.]+)(?!\s*ms)", "latency_p50_raw"),
    (r"p95:\s+([0-9.]+)(?!\s*ms)", "latency_p95_raw"),
    (r"p99:\s+([0-9.]+)(?!\s*ms)", "latency_p99_raw"),
    (r"错误:\s*([0-9]+)", "error_count"),
    (r"成功:\s*([0-9]+)", "success_count"),
    (r"总计:\s*([0-9]+)", "total_requests"),
    # zigoutbounds test_engine.py bench 输出（KEY=VALUE 格式）:
    (r"LATENCY_MIN_MS=([0-9.]+)", "latency_min_ms"),
    (r"AVG_MS=([0-9.]+)", "latency_avg_ms"),
    (r"P50_MS=([0-9.]+)", "latency_p50_ms"),
    (r"P99_MS=([0-9.]+)", "latency_p99_ms"),
    (r"THROUGHPUT_MBPS=([0-9.]+)", "throughput_mbps"),
    (r"REQ_S=([0-9.]+)", "req_s"),
    (r"FAILED=([0-9]+)", "failed"),
    # test_engine.py 并发扫描的资源采样（per-concurrency 内存/fd/CPU）:
    (r"MEMORY_PEAK_MB=([0-9.]+)", "memory_peak_mb"),
    (r"CPU_PCT=([0-9.]+)", "cpu_pct"),
    (r"FD_PEAK=([0-9]+)", "fd_peak"),
    # bench_long（长时持续 + 资源趋势，test_engine.py --long）单行 KEY=VALUE:
    (r"DURATION_S=([0-9.]+)", "duration_s"),
    (r"OK_REQ=([0-9]+)", "success_count"),
    (r"ERRORS=([0-9]+)", "error_count"),
    (r"ERROR_RATE=([0-9.]+)", "error_rate"),
    (r"RSS_GROWTH_MB=(-?[0-9.]+)", "rss_growth_mb"),
    (r"FD_GROWTH=(-?[0-9]+)", "fd_growth"),
    (r"CPU_HEAD_PCT=([0-9.]+)", "cpu_head_pct"),
    (r"CPU_TAIL_PCT=([0-9.]+)", "cpu_tail_pct"),
    (r"SAMPLES=([0-9]+)", "samples"),
    # 长时压测中文格式（zigbox/zigproxy test_stress.py，经此不依赖 yaml custom 也能入库）:
    (r"总请求数:\s*([0-9]+)", "total_requests"),
    (r"错误数:\s*([0-9]+)", "error_count"),
    (r"失败:\s*([0-9]+)", "error_count"),
    (r"错误率:\s*([0-9.]+)", "error_rate"),
    (r"p99_ms:\s*([0-9.]+)", "latency_p99_ms"),
    (r"rss_growth_mb:\s*(-?[0-9.]+)", "rss_growth_mb"),
    (r"fd_growth:\s*(-?[0-9]+)", "fd_growth"),
    (r"cpu_head_pct:\s*([0-9.]+)", "cpu_head_pct"),
    (r"cpu_tail_pct:\s*([0-9.]+)", "cpu_tail_pct"),
    (r"采样点数:\s*([0-9]+)", "samples"),
    (r"并发连接:\s*([0-9]+)", "concurrency"),
    (r"吞吐:\s*([0-9.]+)\s*conn/s", "conn_per_sec"),
]


def _bench_from_text(text: str) -> dict[str, float]:
    """在给定文本(整段 stdout 或单行)内提取全部 bench 指标(每 key 首匹配)。"""
    d: dict[str, float] = {}
    for pat, key in _BENCH_PATTERNS:
        m = re.search(pat, text)
        if m:
            try:
                d[key] = float(m.group(1))
            except ValueError:
                pass
    return d


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

        支持格式:
          - "X/Y passed; Z skipped" (旧版 Zig 输出)
          - "All X tests passed." (Zig 单文件测试)
          - "X passed; Y skipped; Z failed." (Zig 0.14+)
          - Zig 0.16 --listen=- TAP 协议 (无 test count 文本行)

        当无 test count 文本行时由 exit_code 驱动判断:
          exit_code=0 → 视为通过 (tests_total=1, tests_passed=1)
          exit_code≠0 → 视为失败 (tests_total=1, tests_failed=1)
        避免 fallthrough 正则误匹配日志中的随机数字。
        """
        result: dict[str, float] = {
            "exit_code": float(exit_code),
            "tests_passed": 0.0,
            "tests_total": 0.0,
            "tests_skipped": 0.0,
            "tests_failed": 0.0,
        }

        # 匹配 "123/125 passed; 2 skipped"
        m = re.search(r"(\d+)/(\d+)[ \t]+passed[ \t]*;[ \t]*(\d+)[ \t]+skipped", stdout)
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
        m = re.search(r"All[ \t]+(\d+)[ \t]+tests?[ \t]+passed", stdout)
        if m:
            total = int(m.group(1))
            result["tests_passed"] = float(total)
            result["tests_total"] = float(total)
            return result

        # 匹配 "93 pass, 1 skip (94 total)" — Zig 0.16 --listen=- 带 skip 变体
        # （如 zigdns 93/94 passed 1 skipped）
        m = re.search(
            r"(\d+)[ \t]+pass,[ \t]+(\d+)[ \t]+skip[ \t]+\((\d+)[ \t]+total\)",
            stdout,
        )
        if m:
            passed = int(m.group(1))
            skipped = int(m.group(2))
            total = int(m.group(3))
            result["tests_passed"] = float(passed)
            result["tests_skipped"] = float(skipped)
            result["tests_total"] = float(total)
            result["tests_failed"] = float(total - passed - skipped)
            return result

        # 匹配 "202 pass (202 total)" — Zig 0.16 --listen=- 输出格式
        # （如 "run test zigbox-tests 202 pass (202 total) 56ms MaxRSS:11M"）。
        # 0.16 的 listen 模式其实有 count 行，此前 fallthrough 到 exit_code
        # 驱动会误报 1/1（2026-08-18 实测 zig build test 202 测试误记 1）。
        m = re.search(r"(\d+)[ \t]+pass[ \t]+\((\d+)[ \t]+total\)", stdout)
        if m:
            result["tests_passed"] = float(m.group(1))
            result["tests_total"] = float(m.group(2))
            return result

        # 匹配 "X passed; Y skipped; Z failed." (Zig 0.14+ 格式)
        m = re.search(
            r"(\d+)[ \t]+passed[ \t]*;[ \t]*(\d+)[ \t]+skipped[ \t]*;[ \t]*(\d+)[ \t]+failed",
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
        # 使用 stricter 正则：仅匹配测试汇总行，而非日志中的随机数字
        # 测试汇总行特征：以数字开头，"passed"/"failed"/"skipped" 是行中的主要词汇
        m_p = re.search(r"^(\d+)[ \t]+passed", stdout, re.MULTILINE)
        m_f = re.search(r"^(\d+)[ \t]+failed", stdout, re.MULTILINE)
        m_s = re.search(r"^(\d+)[ \t]+skipped", stdout, re.MULTILINE)
        if m_p or m_f or m_s:
            p = int(m_p.group(1)) if m_p else 0
            f = int(m_f.group(1)) if m_f else 0
            s = int(m_s.group(1)) if m_s else 0
            result["tests_passed"] = float(p)
            result["tests_failed"] = float(f)
            result["tests_skipped"] = float(s)
            result["tests_total"] = float(p + f + s)
            return result

        # 无任何 test count 文本行 → 由 exit_code 驱动
        # 用于 Zig 0.16 --listen=- TAP 协议等不输出传统测试汇总的格式
        if exit_code != 0:
            result["tests_total"] = 1.0
            result["tests_failed"] = 1.0
        else:
            result["tests_total"] = 1.0
            result["tests_passed"] = 1.0

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
        """解析 zigbox test_bench.py / zigoutbounds test_engine.py 输出，提取吞吐/延迟。

        格式样例:
          吞吐: 520.3 req/s
          p50: 12.5ms
          p99: 85.2ms
          传输: 45.6 MB/s

        多档并发扫描（test_engine.py --concurrency-sweep 输出多行
        `MODE=bench-tcp CONCURRENCY=N`）→ 每档存 `{key}_c{N}`，裸 key 保留
        首档作默认（兼容 message/history 显示）。
        """
        result: dict[str, float] = {
            "exit_code": float(exit_code),
        }
        # 多档并发扫描：收集全部 bench-tcp 行及其并发档位
        sweep: list[tuple[int, str]] = []
        for line in stdout.splitlines():
            m = re.search(r"MODE=bench-tcp CONCURRENCY=(\d+)", line)
            if m:
                sweep.append((int(m.group(1)), line))
        if len({c for c, _ in sweep}) >= 2:
            for c, line in sweep:
                for k, v in _bench_from_text(line).items():
                    result[f"{k}_c{c}"] = v
            # 裸 key 保留最小并发档（首档）作默认
            first = min(sweep, key=lambda x: x[0])
            result.update(_bench_from_text(first[1]))
            return result
        # 单档：re.search 对全 stdout 取首匹配（bench-tcp 行在 bench-udp 前 = TCP 数据）
        result.update(_bench_from_text(stdout))
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
