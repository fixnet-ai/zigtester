#!/usr/bin/env python3
"""zt-11 四项机制的单元测试（①watchdog ②总预算 ③P95 审计 ⑤隐式单跑）。

覆盖 config 解析 + runner 行为 + history 审计纯函数。
可独立运行（仅标准库 + 项目源码 + python3），无需插件/网络：
    python3 tests/test_watchdog_budget_audit.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from zigtester.config import parse_config  # noqa: E402
from zigtester.history import audit_suite_timeout  # noqa: E402
from zigtester.runner import run_project  # noqa: E402
from zigtester.scanner import DiscoveredProject  # noqa: E402

_RESULTS: list[tuple[str, bool, str]] = []

# 命令串嵌在 YAML 双引号字符串里，内部禁用双引号——静默用 sleep，
# 持续输出用先落盘的 chatty.py（run_project 的 cwd = 项目根 = tmp）。
_SLEEP_60 = "sleep 60"
_SLEEP_2 = "sleep 2"

_CHATTY_PY = (
    "import sys, time\n"
    "for i in range(8):\n"
    "    print(i)\n"
    "    sys.stdout.flush()\n"
    "    time.sleep(0.3)\n"
)


def check(name: str, fn) -> None:
    try:
        fn()
        _RESULTS.append((name, True, ""))
    except AssertionError as e:
        _RESULTS.append((name, False, str(e)))
    except Exception as e:  # noqa: BLE001
        _RESULTS.append((name, False, f"异常: {type(e).__name__}: {e}"))


def _load_project(tmp: str, yaml_text: str) -> DiscoveredProject:
    cfg_path = os.path.join(tmp, "zigtester.yaml")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(yaml_text)
    cfg = parse_config(cfg_path)
    return DiscoveredProject(
        name=cfg.project,
        path=tmp,
        config_path=cfg_path,
        config=cfg,
    )


def _suite(pr, name):
    for s in pr.suites:
        if s.suite_name == name:
            return s
    raise AssertionError(f"结果中未找到套件 {name}")


# ── ① no-output watchdog ──────────────────────────────────


def test_watchdog_kills_silent_proc():
    yaml_text = f"""\
project: wd-test
settings:
  work_dir: "."
levels:
  functional:
    - name: silent-hang
      command: "{_SLEEP_60}"
      timeout: 30
      no_output_timeout_s: 2
"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _load_project(tmp, yaml_text)
        pr = run_project(proj, levels=["functional"], no_build=True)
        s = _suite(pr, "silent-hang")
        assert s.status == "FAIL", (s.status, s.message)
        assert s.kill_reason.startswith("no_output"), s.kill_reason
        assert "no-output watchdog" in s.message, s.message
        # 2s 窗口触发而非 30s 硬超时——止损生效
        assert s.duration_ms < 15000, s.duration_ms


def test_watchdog_output_resets_window():
    yaml_text = """\
project: wd-chatty
settings:
  work_dir: "."
levels:
  functional:
    - name: chatty
      command: "python3 chatty.py"
      timeout: 30
      no_output_timeout_s: 1
"""
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "chatty.py"), "w", encoding="utf-8") as f:
            f.write(_CHATTY_PY)
        proj = _load_project(tmp, yaml_text)
        pr = run_project(proj, levels=["functional"], no_build=True)
        s = _suite(pr, "chatty")
        # 每 0.3s 有输出 → 1s 窗口永不触发 → 跑满 ~2.4s 正常退出
        assert s.status == "PASS", (s.status, s.message)
        assert s.kill_reason == "", s.kill_reason
        assert s.duration_ms >= 2000, s.duration_ms


def test_watchdog_inert_when_window_ge_timeout():
    yaml_text = """\
project: wd-inert
settings:
  work_dir: "."
levels:
  functional:
    - name: short-hard-timeout
      command: "sleep 6"
      timeout: 4
      no_output_timeout_s: 300
"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _load_project(tmp, yaml_text)
        pr = run_project(proj, levels=["functional"], no_build=True)
        s = _suite(pr, "short-hard-timeout")
        # 窗口(300s) >= timeout(4s) → watchdog 不武装，硬超时兜底（zig_test 默认形态）
        # 命令须显著长于 timeout（sleep 4 @timeout 4 会因 spawn 时钟差抢先自然退出）
        assert s.status == "FAIL", (s.status, s.message)
        assert s.kill_reason == "timeout", s.kill_reason
        assert s.message == "超时", s.message


# ── ② run 级总预算 ────────────────────────────────────────


def test_total_budget_skips_rest():
    yaml_text = f"""\
project: budget-test
settings:
  work_dir: "."
levels:
  functional:
    - name: first
      command: "{_SLEEP_2}"
      timeout: 20
    - name: second
      command: "{_SLEEP_2}"
      timeout: 20
"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _load_project(tmp, yaml_text)
        # 预算 1s：套件 first 启动前检查(0s<1s)通过、跑 2s；
        # second 启动前检查(~2s>=1s) → SKIP（zt-11 ②）
        pr = run_project(proj, levels=["functional"], no_build=True, total_budget_s=1)
        assert _suite(pr, "first").status == "PASS", pr
        s2 = _suite(pr, "second")
        assert s2.status == "SKIP", (s2.status, s2.message)
        assert "总预算" in s2.message, s2.message


def test_total_budget_disabled_by_zero():
    yaml_text = """\
project: budget-off
settings:
  work_dir: "."
levels:
  functional:
    - name: quick
      command: "true"
      timeout: 20
"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _load_project(tmp, yaml_text)
        pr = run_project(proj, levels=["functional"], no_build=True, total_budget_s=0)
        assert _suite(pr, "quick").status == "PASS", pr


# ── ⑤ timeout≥300s 隐式单跑 ──────────────────────────────


def test_implicit_solo_skips_in_full_run():
    yaml_text = """\
project: solo-test
settings:
  work_dir: "."
levels:
  functional:
    - name: long-suite
      command: "true"
      timeout: 300
    - name: unlocked
      command: "true"
      timeout: 300
      allow_full_run: true
    - name: short-suite
      command: "true"
      timeout: 299
"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _load_project(tmp, yaml_text)
        pr = run_project(proj, levels=["functional"], no_build=True)
        s = _suite(pr, "long-suite")
        assert s.status == "SKIP", (s.status, s.message)
        assert "zt-11" in s.message and "allow_full_run" in s.message, s.message
        # 逃生门解锁后全量正常跑
        assert _suite(pr, "unlocked").status == "PASS", pr
        # 阈值以下不受影响
        assert _suite(pr, "short-suite").status == "PASS", pr


def test_implicit_solo_runs_via_suite_filter():
    yaml_text = """\
project: solo-filter
settings:
  work_dir: "."
levels:
  functional:
    - name: long-suite
      command: "true"
      timeout: 300
"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _load_project(tmp, yaml_text)
        pr = run_project(proj, levels=["functional"], no_build=True,
                         suite_filter="long-suite")
        assert _suite(pr, "long-suite").status == "PASS", pr


def test_config_parses_new_fields():
    yaml_text = """\
project: cfg-fields
settings:
  work_dir: "."
  total_budget_s: 1200
levels:
  unit:
    - name: t1
      command: "true"
      timeout: 300
      allow_full_run: true
      no_output_timeout_s: 45
"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _load_project(tmp, yaml_text)
        st = proj.config.settings
        assert st.total_budget_s == 1200, st
        s = proj.config.levels["unit"].suites[0]
        assert s.allow_full_run is True
        assert s.no_output_timeout_s == 45
        assert s.solo_by_default is False  # allow_full_run 解锁
        # 旧配置默认值：不写新字段 → 不隐式单跑 / 窗口走默认 / 预算走默认
        with tempfile.TemporaryDirectory() as tmp2:
            proj2 = _load_project(tmp2, 'project: old\nlevels:\n  unit:\n    - name: t\n      command: "true"\n')
            s2 = proj2.config.levels["unit"].suites[0]
            assert s2.solo_by_default is False
            assert s2.no_output_timeout_s == 0
            assert s2.allow_full_run is False
            assert proj2.config.settings.total_budget_s == 0


# ── ③ timeout 审计（纯函数）──────────────────────────────


def test_audit_redundant_and_drift():
    v = audit_suite_timeout(600, [3200, 3300, 3400], [3200, 3300, 3400])
    assert v is not None, v
    assert v["p95_s"] == 3.4, v
    assert any(w.startswith("redundant") for w in v["warnings"]), v
    assert any(w.startswith("drift") for w in v["warnings"]), v


def test_audit_hit_ceiling():
    v = audit_suite_timeout(300, [1000, 1100, 1200], [1000, 1100, 1200, 295000])
    assert v is not None, v
    assert any(w.startswith("hit_ceiling") for w in v["warnings"]), v


def test_audit_healthy_and_insufficient():
    assert audit_suite_timeout(10, [3000, 3200, 3400], [3000, 3200, 3400]) is None
    assert audit_suite_timeout(600, [3200, 3300], [3200, 3300]) is None  # <3 条 PASS


def test_audit_slack_filter_drops_small_budgets():
    # 亚秒级套件（route-geo 形态）：冗余百倍但绝对富余 <120s → 无止损价值不出行
    assert audit_suite_timeout(60, [100, 110, 120], [100, 110, 120]) is None
    # 大预算同形态（tun-scenarios 形态）仍出行，且建议值带 30s 下限
    v = audit_suite_timeout(300, [100, 110, 120], [100, 110, 120])
    assert v is not None and any("建议 ≈30s" in w for w in v["warnings"]), v


def main() -> int:
    checks = [
        ("① watchdog — 静默进程判卡死早于硬超时", test_watchdog_kills_silent_proc),
        ("① watchdog — 持续输出刷新窗口不误杀", test_watchdog_output_resets_window),
        ("① watchdog — 窗口≥timeout 不武装走硬超时", test_watchdog_inert_when_window_ge_timeout),
        ("② 总预算 — 耗尽后剩余套件 SKIP", test_total_budget_skips_rest),
        ("② 总预算 — 0 禁用", test_total_budget_disabled_by_zero),
        ("⑤ 隐式单跑 — 全量跳过/逃生门/阈值下不受影响", test_implicit_solo_skips_in_full_run),
        ("⑤ 隐式单跑 — --suite 显式运行", test_implicit_solo_runs_via_suite_filter),
        ("config — 新字段解析与旧配置默认值", test_config_parses_new_fields),
        ("③ 审计 — redundant/drift 判据", test_audit_redundant_and_drift),
        ("③ 审计 — hit_ceiling 判据", test_audit_hit_ceiling),
        ("③ 审计 — 健康与历史不足不出行", test_audit_healthy_and_insufficient),
        ("③ 审计 — 绝对富余门槛滤掉亚秒套件噪声", test_audit_slack_filter_drops_small_budgets),
    ]
    for name, fn in checks:
        check(name, fn)

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    failed = len(_RESULTS) - passed
    print(f"\n总计 {len(_RESULTS)} | 通过 {passed} | 失败 {failed}")
    for name, ok, msg in _RESULTS:
        if not ok:
            print(f"  FAIL {name}: {msg}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
