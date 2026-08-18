#!/usr/bin/env python3
"""zigtester 环境自检（pre-flight guard）单元测试。

覆盖 plugin.py 的 verify_plugin / PluginManager 自愈 / 规范输出 /
runner.py 的环境 fast fail 辅助函数。

可独立运行（仅标准库 + 项目源码），不留临时文件和进程：
    python3 tests/test_env_guard.py
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from zigtester.config import (  # noqa: E402
    LevelConfig,
    PluginRef,
    ProjectConfig,
    SuiteConfig,
)
from zigtester.plugin import (  # noqa: E402
    PluginConfig,
    PluginManager,
    _descendant_pids,
    _port_owner_map,
    env_spec_message,
    start_plugin,
    verify_plugin,
)
from zigtester.runner import (  # noqa: E402
    ProjectResult,
    _abort_with_env_error,
    _skip_remaining_suites,
)

# ── 测试脚手架 ─────────────────────────────────────────────

_MINI_SRV = textwrap.dedent(
    """
    import socket, sys
    port = int(sys.argv[1])
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(4)
    while True:
        c, _ = s.accept()
        c.close()
    """
)

_PLUGIN_YAML = textwrap.dedent(
    """
    name: mini
    build:
      command: "true"
    lifecycle:
      start:
        command: "python3 mini_srv.py {port}"
        timeout: 5
        ready_on:
          type: tcp
          port: {port}
          host: "127.0.0.1"
      stop:
        timeout: 3
        kill:
          - "mini_srv.py"
    ports:
      - {port}
    """
)


def _free_port() -> int:
    """找一个空闲的高位端口。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_mini_plugin(tmpdir: str, port: int) -> str:
    """在 tmpdir/plugins/mini 创建最小插件。返回 zigtester root（tmpdir）。"""
    plugdir = os.path.join(tmpdir, "plugins", "mini")
    os.makedirs(plugdir, exist_ok=True)
    with open(os.path.join(plugdir, "mini_srv.py"), "w", encoding="utf-8") as f:
        f.write(_MINI_SRV)
    with open(os.path.join(plugdir, "plugin.yaml"), "w", encoding="utf-8") as f:
        f.write(_PLUGIN_YAML.format(port=port))
    return tmpdir


def _port_open(port: int) -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
        s.close()
        return True
    except OSError:
        return False


def _wait_port_gone(port: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_open(port):
            return True
        time.sleep(0.2)
    return False


# unstable 变体：bind+listen 后主线程 sleep 1 秒即自杀 — 保证 1 秒内
# 端口可连（ready_on 通过），但进程短命，用于检验 _heal 延迟复检
_UNSTABLE_SRV = textwrap.dedent(
    """
    import os, socket, sys, time
    port = int(sys.argv[1])
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(4)
    time.sleep(1)
    os._exit(0)
    """
)


class _Ctx:
    """每个用例的临时环境（目录 + 端口 + 管理器）。"""

    def __init__(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="zt-envguard-")
        self.port = _free_port()
        self.root = _make_mini_plugin(self.tmpdir, self.port)
        self.plugdir = os.path.join(self.tmpdir, "plugins", "mini")

    def make_unstable(self) -> None:
        """把 mini 插件的服务端替换为 1 秒自杀的 unstable 变体。"""
        with open(
            os.path.join(self.plugdir, "mini_srv.py"), "w", encoding="utf-8"
        ) as f:
            f.write(_UNSTABLE_SRV)

    def cleanup(self) -> None:
        subprocess.run(
            ["pkill", "-f", f"mini_srv.py {self.port}"], capture_output=True
        )
        shutil.rmtree(self.tmpdir, ignore_errors=True)


_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
        _RESULTS.append((name, True, ""))
    except AssertionError as e:
        _RESULTS.append((name, False, str(e)))
    except Exception as e:  # noqa: BLE001
        _RESULTS.append((name, False, f"异常: {type(e).__name__}: {e}"))


# ── 用例 ───────────────────────────────────────────────────


def test_descendant_pids() -> None:
    """进程树收集：父 shell → python 孙进程都能被归入。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        tree = _descendant_pids(proc.pid)
        assert proc.pid in tree, f"根进程 {proc.pid} 不在树内: {tree}"
    finally:
        proc.kill()
        proc.wait()


def test_port_owner_map_contains_listener() -> None:
    """lsof 端口映射能找到本进程启动的监听者。"""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c",
         f"import socket,time;s=socket.socket();s.bind(('127.0.0.1',{port}));"
         f"s.listen(2);time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not _port_open(port):
            time.sleep(0.1)
        owner_map = _port_owner_map()
        if owner_map is None:
            # lsof 不可用的环境（极少）— 跳过而非失败
            print(f"  [skip] lsof 不可用，跳过归属断言")
            return
        assert port in owner_map, f"端口 {port} 未出现在 owner map 中"
        assert proc.pid in owner_map[port], (
            f"监听者 {proc.pid} 不在端口 {port} 的占用者 {owner_map[port]} 中"
        )
    finally:
        proc.kill()
        proc.wait()


def test_verify_healthy() -> None:
    """健康插件：verify 返回空问题列表。"""
    ctx = _Ctx()
    try:
        from zigtester.plugin import parse_plugin_config
        pcfg = parse_plugin_config(ctx.plugdir)
        assert pcfg is not None
        proc = start_plugin(pcfg, pcfg.path)
        assert proc is not None, "mini 插件启动失败"
        try:
            problems = verify_plugin(pcfg, proc)
            assert problems == [], f"健康插件不应有问题，实际: {problems}"
        finally:
            proc.terminate()
            proc.wait()
    finally:
        ctx.cleanup()


def test_plugin_pipe_drained() -> None:
    """插件大量 stdout 输出不应阻塞启动 — PIPE 由排空线程落到 /tmp 日志。"""
    ctx = _Ctx()
    try:
        from zigtester.plugin import parse_plugin_config
        # chatty 变体：bind 前先向 stdout 写 ~200KB（超过 macOS 管道缓冲 64KB）
        with open(
            os.path.join(ctx.plugdir, "mini_srv.py"), "w", encoding="utf-8"
        ) as f:
            f.write(
                "import socket, sys\n"
                "port = int(sys.argv[1])\n"
                "for i in range(10000):\n"
                "    print('chatty line %d' % i, flush=True)\n"
                "s = socket.socket()\n"
                "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
                "s.bind(('127.0.0.1', port))\n"
                "s.listen(4)\n"
                "while True:\n"
                "    c, _ = s.accept()\n"
                "    c.close()\n"
            )
        pcfg = parse_plugin_config(ctx.plugdir)
        assert pcfg is not None
        t0 = time.time()
        proc = start_plugin(pcfg, pcfg.path)
        elapsed = time.time() - t0
        assert proc is not None, "chatty 插件启动失败（可能管道阻塞导致 ready 超时）"
        assert elapsed < 10, f"start_plugin 阻塞过久: {elapsed:.1f}s"
        try:
            problems = verify_plugin(pcfg, proc)
            assert problems == [], f"排空后插件应健康，实际: {problems}"
            log_path = f"/tmp/zigtester-plugin-{pcfg.name}.log"
            assert os.path.exists(log_path), f"插件日志未生成: {log_path}"
            # 等排空线程追平（进程长驻，全部 10000 行应写入日志）
            deadline = time.time() + 5
            lines = 0
            while time.time() < deadline:
                with open(log_path, encoding="utf-8", errors="replace") as lf:
                    lines = sum(1 for _ in lf)
                if lines >= 10000:
                    break
                time.sleep(0.2)
            assert lines >= 10000, (
                f"插件日志应含 10000 行（PIPE 已排空），实际 {lines} 行"
            )
        finally:
            proc.terminate()
            proc.wait()
    finally:
        ctx.cleanup()


def test_verify_dead_process() -> None:
    """进程退出：verify 报告进程已退出。"""
    ctx = _Ctx()
    try:
        from zigtester.plugin import parse_plugin_config
        pcfg = parse_plugin_config(ctx.plugdir)
        assert pcfg is not None
        proc = start_plugin(pcfg, pcfg.path)
        assert proc is not None
        proc.kill()
        proc.wait()
        problems = verify_plugin(pcfg, proc)
        assert any("已退出" in p for p in problems), (
            f"应报告进程退出，实际: {problems}"
        )
    finally:
        ctx.cleanup()


def test_verify_foreign_port_owner() -> None:
    """端口被外部进程抢占：verify 报告归属异常。"""
    ctx = _Ctx()
    try:
        from zigtester.plugin import parse_plugin_config
        # mini 插件声明 port 但被无关外部进程监听（不属于插件进程树）
        foreign = subprocess.Popen(
            [sys.executable, "-c",
             f"import socket,time;s=socket.socket();s.bind(('127.0.0.1',{ctx.port}));"
             f"s.listen(2);time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 5
        while time.time() < deadline and not _port_open(ctx.port):
            time.sleep(0.1)
        # 构造插件配置但进程本身是另一个不相关的 python（活着、无树关系）
        pcfg = parse_plugin_config(ctx.plugdir)
        assert pcfg is not None
        decoy = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            problems = verify_plugin(pcfg, decoy)
            assert any("进程树之外" in p or "不可见" in p for p in problems), (
                f"应报告端口被外部进程占用，实际: {problems}"
            )
        finally:
            foreign.kill(); foreign.wait()
            decoy.kill(); decoy.wait()
    finally:
        ctx.cleanup()


def test_ensure_ready_heals_dead_plugin() -> None:
    """自愈：插件被杀后 ensure_ready 自动重启并恢复健康。"""
    ctx = _Ctx()
    try:
        mgr = PluginManager()
        fatal = mgr.prepare([PluginRef(name="mini")], zigtester_root=ctx.root)
        assert fatal is None, f"prepare 不应失败: {fatal}"
        try:
            # 外部破坏：杀死插件进程（模拟兄弟项目会话误杀）
            mgr._plugins[0][1].kill()
            mgr._plugins[0][1].wait()
            fatal = mgr.ensure_ready()
            assert fatal is None, f"自愈应成功: {fatal}"
            problems = verify_plugin(mgr._plugins[0][0], mgr._plugins[0][1])
            assert problems == [], f"自愈后应健康: {problems}"
        finally:
            mgr.stop_all()
        assert _wait_port_gone(ctx.port), "stop_all 后端口应释放"
    finally:
        ctx.cleanup()


def test_ensure_ready_fast_fail_when_unrecoverable() -> None:
    """自愈失败 → fast fail 并输出测试环境规范。"""
    ctx = _Ctx()
    try:
        mgr = PluginManager()
        fatal = mgr.prepare([PluginRef(name="mini")], zigtester_root=ctx.root)
        assert fatal is None, f"prepare 不应失败: {fatal}"
        try:
            # 破坏到无法恢复：删掉服务端脚本，kill 进程后重启必失败
            os.unlink(os.path.join(ctx.plugdir, "mini_srv.py"))
            mgr._plugins[0][1].kill()
            mgr._plugins[0][1].wait()
            fatal = mgr.ensure_ready()
            assert fatal is not None, "自愈失败时应返回致命错误"
            assert "测试环境规范" in fatal, f"应包含测试环境规范: {fatal[:200]}"
            assert "zigtester run" in fatal, f"应指引 zigtester run: {fatal[:200]}"
        finally:
            mgr.stop_all()
    finally:
        ctx.cleanup()


def test_prepare_cleans_stale_plugin_process() -> None:
    """prepare 自动清理残留插件进程：旧实例占端口 → 清理 → 启动新实例。"""
    ctx = _Ctx()
    try:
        # 制造残留：手动启动一个 mini_srv（模拟上一轮 zigtester 异常退出的残留）
        stale = subprocess.Popen(
            [sys.executable, "mini_srv.py", str(ctx.port)],
            cwd=ctx.plugdir,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 5
        while time.time() < deadline and not _port_open(ctx.port):
            time.sleep(0.1)
        assert _port_open(ctx.port), "残留实例未就绪（测试前置失败）"

        mgr = PluginManager()
        fatal = mgr.prepare([PluginRef(name="mini")], zigtester_root=ctx.root)
        assert fatal is None, f"prepare 应自动清理残留并成功: {fatal}"
        try:
            assert mgr.ensure_ready() is None
            problems = verify_plugin(mgr._plugins[0][0], mgr._plugins[0][1])
            assert problems == [], f"新实例应健康: {problems}"
            assert stale.poll() is not None, "残留进程应已被清理"
        finally:
            mgr.stop_all()
    finally:
        ctx.cleanup()


def test_prepare_fails_on_unknown_occupier() -> None:
    """未知进程占端口 → 不误杀，fast fail 输出规范。"""
    ctx = _Ctx()
    try:
        foreign = subprocess.Popen(
            [sys.executable, "-c",
             f"import socket,time;s=socket.socket();s.bind(('127.0.0.1',{ctx.port}));"
             f"s.listen(2);time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 5
        while time.time() < deadline and not _port_open(ctx.port):
            time.sleep(0.1)
        try:
            mgr = PluginManager()
            fatal = mgr.prepare([PluginRef(name="mini")], zigtester_root=ctx.root)
            assert fatal is not None, "未知占用者应导致 fast fail"
            assert "未知进程" in fatal or "仍被占用" in fatal, (
                f"应说明未知占用: {fatal[:300]}"
            )
            assert "测试环境规范" in fatal
            assert foreign.poll() is None, "未知进程绝不能被误杀"
        finally:
            foreign.kill()
            foreign.wait()
    finally:
        ctx.cleanup()


def test_env_spec_message_contents() -> None:
    """规范文本包含关键指引元素。"""
    plugin = PluginConfig(name="local-echo", ports=[13333, 5533])
    msg = env_spec_message(["端口 13333 被外部进程占用"], [plugin])
    assert "测试环境规范" in msg
    assert "zigtester" in msg
    assert "禁止" in msg
    assert "13333" in msg, "应包含端口排查提示"


def test_abort_with_env_error_marks_suites() -> None:
    """runner fast fail 辅助：首个 ERROR + 其余 SKIP。"""
    cfg = ProjectConfig(
        project="demo",
        levels={
            "unit": LevelConfig(suites=[
                SuiteConfig(name="u1", command="true"),
                SuiteConfig(name="u2", command="true"),
            ]),
            "functional": LevelConfig(suites=[
                SuiteConfig(name="f1", command="true"),
            ]),
        },
    )
    result = ProjectResult(project="demo", path="/tmp/demo", started_at=time.time())
    _abort_with_env_error(result, cfg, ["unit", "functional"], "环境不可用")
    statuses = [(s.suite_name, s.status) for s in result.suites]
    assert statuses == [("u1", "ERROR"), ("u2", "SKIP"), ("f1", "SKIP")], (
        f"期望首个 ERROR 其余 SKIP，实际: {statuses}"
    )
    assert result.suites[0].setup_error == "环境不可用"
    assert result.finished_at > 0


def test_skip_remaining_suites() -> None:
    """suite 中途失败：同层剩余 + 后续层级全部 SKIP。"""
    cfg = ProjectConfig(
        project="demo",
        levels={
            "unit": LevelConfig(suites=[
                SuiteConfig(name="u1", command="true"),
                SuiteConfig(name="u2", command="true"),
                SuiteConfig(name="u3", command="true"),
            ]),
            "functional": LevelConfig(suites=[
                SuiteConfig(name="f1", command="true"),
            ]),
        },
    )
    result = ProjectResult(project="demo", path="/tmp/demo", started_at=time.time())
    # 模拟 u2 环境自检失败：其后的套件（u3、f1）应全部 SKIP
    _skip_remaining_suites(result, cfg, ["unit", "functional"], "unit", "u2")
    statuses = [(s.suite_name, s.status) for s in result.suites]
    assert statuses == [("u3", "SKIP"), ("f1", "SKIP")], (
        f"期望 u3/f1 SKIP，实际: {statuses}"
    )


def test_heal_fast_fail_on_unstable_plugin() -> None:
    """unstable 插件自愈应 fast fail：重启后短命进程逃不过延迟复检。

    流程：prepare（ready_on 通过，但插件 1 秒后自杀）→ 等自杀 →
    ensure_ready 触发自愈 → 新实例同样短命，首次 verify 落在死亡窗口内
    通过 → 延迟复检发现死亡 → fast fail 返回含"测试环境规范"的致命错误。
    """
    ctx = _Ctx()
    ctx.make_unstable()
    try:
        mgr = PluginManager()
        fatal = mgr.prepare([PluginRef(name="mini")], zigtester_root=ctx.root)
        assert fatal is None, f"prepare 不应失败（端口就绪窗口内）: {fatal}"
        try:
            # 等插件自杀（1 秒自杀 + 余量）
            deadline = time.time() + 5
            while time.time() < deadline:
                if mgr._plugins[0][1].poll() is not None:
                    break
                time.sleep(0.2)
            assert mgr._plugins[0][1].poll() is not None, "插件应在 1 秒左右自杀"

            fatal = mgr.ensure_ready()
            assert fatal is not None, (
                "unstable 插件自愈后应被延迟复检发现并 fast fail"
            )
            assert "测试环境规范" in fatal, f"应包含测试环境规范: {fatal[:200]}"
            assert "稳定期" in fatal, f"应说明稳定期复检失败: {fatal[:300]}"
        finally:
            mgr.stop_all()
    finally:
        ctx.cleanup()


_FAKE_PROJECT_YAML = """\
project: {name}
levels:
  unit:
    - name: "u1"
      command: "true"
"""


def test_run_workspace_grouped_parallel() -> None:
    """run_workspace 分组并行：2 个无插件 fake 项目 parallel=True 全部执行，
    且结果按传入顺序输出（不受完成先后影响）。"""
    from zigtester.config import parse_config
    from zigtester.runner import run_workspace
    from zigtester.scanner import DiscoveredProject

    tmpdir = tempfile.mkdtemp(prefix="zt-wsgroup-")
    try:
        projects = []
        for name in ("fake-a", "fake-b"):
            pdir = os.path.join(tmpdir, name)
            os.makedirs(pdir)
            cfg_path = os.path.join(pdir, "zigtester.yaml")
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(_FAKE_PROJECT_YAML.format(name=name))
            cfg = parse_config(cfg_path)
            projects.append(
                DiscoveredProject(name=name, path=pdir, config_path=cfg_path, config=cfg)
            )

        ws = run_workspace(projects, ["unit"], parallel=True)
        names = [pr.project for pr in ws.projects]
        assert names == ["fake-a", "fake-b"], (
            f"两个项目都应执行且顺序稳定，实际: {names}"
        )
        for pr in ws.projects:
            statuses = [s.status for s in pr.suites]
            assert statuses == ["PASS"], (
                f"{pr.project} 套件应 PASS，实际: {statuses}"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── 入口 ───────────────────────────────────────────────────

def main() -> int:
    checks = [
        ("进程树收集 _descendant_pids", test_descendant_pids),
        ("端口归属映射 _port_owner_map", test_port_owner_map_contains_listener),
        ("verify — 健康插件", test_verify_healthy),
        ("插件 PIPE 排空 — 大量输出不阻塞", test_plugin_pipe_drained),
        ("verify — 进程退出", test_verify_dead_process),
        ("verify — 外部进程抢端口", test_verify_foreign_port_owner),
        ("自愈 — 插件被杀后自动恢复", test_ensure_ready_heals_dead_plugin),
        ("fast fail — 自愈失败输出规范", test_ensure_ready_fast_fail_when_unrecoverable),
        ("prepare — 自动清理残留插件", test_prepare_cleans_stale_plugin_process),
        ("prepare — 未知占用者不误杀", test_prepare_fails_on_unknown_occupier),
        ("规范文本内容", test_env_spec_message_contents),
        ("runner — 全项目环境 fast fail", test_abort_with_env_error_marks_suites),
        ("runner — 剩余套件 SKIP", test_skip_remaining_suites),
        ("自愈 — unstable 插件延迟复检 fast fail", test_heal_fast_fail_on_unstable_plugin),
        ("runner — run_workspace 分组并行", test_run_workspace_grouped_parallel),
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
