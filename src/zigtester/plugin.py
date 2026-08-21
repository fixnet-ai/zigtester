"""插件体系 — 可复用测试基础设施的配置解析与生命周期管理。

插件是自包含目录，包含 `plugin.yaml` 清单文件。
zigtester 负责发现、构建、启动、就绪检测、停止。
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import LifecycleHook, ReadyOn
from .runner import _wait_tcp_ready, _wait_process_ready


# ── 插件数据模型 ────────────────────────────────────────────


@dataclass
class PluginBuild:
    """插件构建配置。"""
    command: str = "zig build"
    work_dir: str = "."


@dataclass
class PluginLifecycle:
    """插件生命周期 — 与 SuiteConfig 的 setup/teardown 格式一致。"""
    start: LifecycleHook
    stop: LifecycleHook


@dataclass
class PluginConfig:
    """插件完整配置。"""
    name: str
    description: str = ""
    path: str = ""              # 插件目录绝对路径
    host: str | None = None     # 插件服务器 IP（None=未配置，运行时 resolve 到
                                # ZIGTESTER_PLUGIN_HOST/127.0.0.1；非本机 = 远程插件模式：
                                # 服务已在远端 host 运行，本机不 build/start/stop，只远端探测）
    build: PluginBuild = field(default_factory=PluginBuild)
    lifecycle: PluginLifecycle | None = None
    config: dict[str, Any] = field(default_factory=dict)  # 项目级覆盖配置
    ports: list[int] = field(default_factory=list)        # 插件监听端口（用于跨插件冲突检测）


# 视为"本机"的 host 值 — 判定插件服务是否运行在本机（本机 = 本地启动/停止/端口归属）
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def _resolve_host(plugin: PluginConfig) -> str:
    """解析插件生效的服务器 host（优先级：显式配置 > ZIGTESTER_PLUGIN_HOST > 127.0.0.1）。"""
    if plugin.host:
        return plugin.host
    return os.environ.get("ZIGTESTER_PLUGIN_HOST", "127.0.0.1")


def _is_local_host(host: str) -> bool:
    """host 是否指向本机（回环或全接口）。非本机 = 远程插件模式。"""
    return host in _LOCAL_HOSTS


# ── 解析 ────────────────────────────────────────────────────


def parse_plugin_config(plugin_dir: str) -> PluginConfig | None:
    """解析 plugin.yaml，返回 PluginConfig。失败返回 None。"""
    yaml_path = os.path.join(plugin_dir, "plugin.yaml")
    if not os.path.isfile(yaml_path):
        return None

    with open(yaml_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return None

    name = str(raw.get("name", os.path.basename(plugin_dir)))
    description = str(raw.get("description", ""))
    # 插件服务器 host（默认 None → 运行时 resolve 到 ZIGTESTER_PLUGIN_HOST/127.0.0.1；
    # 非本机 host = 远程插件模式，服务已在远端 host 运行）
    host = str(raw["host"]) if "host" in raw else None

    # 加载插件默认配置（后续可被项目级 zigtester.yaml 覆盖）
    config_raw = raw.get("config")
    if isinstance(config_raw, dict):
        config = dict(config_raw)
    else:
        config = {}

    # 端口列表（可选）— 用于跨插件端口冲突检测
    ports_raw = raw.get("ports", [])
    if isinstance(ports_raw, list):
        ports = [int(p) for p in ports_raw if isinstance(p, (int, float))]
    else:
        ports = []

    # build
    build_raw = raw.get("build", {})
    build = PluginBuild(
        command=str(build_raw.get("command", "zig build")),
        work_dir=str(build_raw.get("work_dir", ".")),
    )

    # lifecycle
    lc_raw = raw.get("lifecycle")
    if lc_raw is None:
        return PluginConfig(
            name=name,
            description=description,
            path=plugin_dir,
            host=host,
            build=build,
            config=config,
            lifecycle=None,
            ports=ports,
        )

    start_raw = lc_raw.get("start", {})
    stop_raw = lc_raw.get("stop", {})

    lifecycle = PluginLifecycle(
        start=LifecycleHook(
            command=start_raw.get("command"),
            timeout=int(start_raw.get("timeout", 30)),
            ready_on=_parse_plugin_ready_on(start_raw.get("ready_on")),
        ),
        stop=LifecycleHook(
            command=stop_raw.get("command"),
            timeout=int(stop_raw.get("timeout", 10)),
            kill=stop_raw.get("kill"),
        ),
    )

    return PluginConfig(
        name=name,
        description=description,
        path=plugin_dir,
        host=host,
        build=build,
        config=config,
        lifecycle=lifecycle,
        ports=ports,
    )


def _parse_plugin_ready_on(raw: dict | None) -> ReadyOn | None:
    """解析插件 ready_on 配置。"""
    if raw is None:
        return None
    return ReadyOn(
        type=str(raw.get("type", "tcp")),
        port=int(raw.get("port", 0)),
        host=str(raw.get("host", "127.0.0.1")),
        timeout=int(raw.get("timeout", 30)),
        interval=float(raw.get("interval", 0.5)),
    )


# ── 发现 ────────────────────────────────────────────────────


def discover_plugins(zigtester_root: str) -> dict[str, str]:
    """扫描 zigtester/plugins/ 目录，返回 {plugin_name: plugin_dir} 映射。"""
    plugins: dict[str, str] = {}
    plugins_dir = os.path.join(zigtester_root, "plugins")
    if not os.path.isdir(plugins_dir):
        return plugins

    for entry in sorted(os.listdir(plugins_dir)):
        entry_path = os.path.join(plugins_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        if entry.startswith(".") or entry.startswith("_"):
            continue
        yaml_path = os.path.join(entry_path, "plugin.yaml")
        if os.path.isfile(yaml_path):
            plugins[entry] = entry_path

    return plugins


def _find_zigtester_root() -> str | None:
    """向上查找 zigtester 源码包根目录（含 src/zigtester 的目录）。"""
    # 从当前文件所在目录向上查找
    current = os.path.dirname(os.path.abspath(__file__))
    # current = .../src/zigtester/plugin.py → 找 src/.. 即项目根
    for _ in range(5):
        parent = os.path.dirname(current)
        if os.path.isdir(os.path.join(parent, "src", "zigtester")):
            return parent
        current = parent
    return os.getcwd()


# ── 生命周期管理 ────────────────────────────────────────────


def build_plugin(plugin: PluginConfig) -> bool:
    """执行插件的构建命令。失败返回 False（不抛异常，不阻止测试）。"""
    work_dir = os.path.join(plugin.path, plugin.build.work_dir)
    try:
        cmd = shlex.split(plugin.build.command)
        result = subprocess.run(
            cmd,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
        )
        return result.returncode == 0
    except Exception:
        return False


def start_plugin(
    plugin: PluginConfig,
    work_dir: str,
) -> subprocess.Popen | None:
    """启动插件进程（长期服务），等待 readiness probe。失败返回 None。

    插件 config 字段中的键值会转为 PLUGIN_<KEY> 环境变量传入进程。
    """
    if plugin.lifecycle is None or plugin.lifecycle.start.command is None:
        return None

    hook = plugin.lifecycle.start
    try:
        proc = _start_plugin_process(hook, work_dir, plugin.config, plugin.name)

        # 等待就绪
        ro = hook.ready_on
        if ro is not None:
            if ro.type == "tcp":
                ok = _wait_tcp_ready(ro.host, ro.port, ro.timeout, ro.interval)
            elif ro.type == "process":
                target = hook.command.split()[0] if hook.command else ""
                if ro.host and ro.host != "127.0.0.1":
                    target = ro.host
                ok = _wait_process_ready(target, ro.timeout, ro.interval) if target else True
            else:
                ok = True
            if not ok:
                _kill_plugin_process(proc, hook)
                return None

        return proc
    except Exception:
        return None


def stop_plugin(
    proc: subprocess.Popen | None,
    plugin: PluginConfig,
) -> None:
    """停止插件进程 + 进程名清理。"""
    # 远程插件：服务在远端 host，本机无进程可停；pkill 还会误杀本机同名进程
    if not _is_local_host(_resolve_host(plugin)):
        return
    if plugin.lifecycle is None:
        return

    stop_hook = plugin.lifecycle.stop

    # 1) 执行 stop 命令
    if stop_hook.command is not None:
        try:
            sp = _start_plugin_process(stop_hook, plugin.path, None, plugin.name)
            try:
                sp.wait(timeout=stop_hook.timeout)
            except subprocess.TimeoutExpired:
                _kill_plugin_process(sp, stop_hook)
        except Exception:
            pass

    # 2) 杀死插件主进程
    if proc is not None:
        _kill_plugin_process(proc, stop_hook)

    # 3) 进程名清理
    if stop_hook.kill is not None:
        for name in stop_hook.kill:
            try:
                subprocess.run(
                    ["pkill", "-f", name],
                    capture_output=True,
                    timeout=5,
                )
                time.sleep(0.3)
                subprocess.run(
                    ["pkill", "-9", "-f", name],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass


def plugin_log_path(plugin_name: str) -> str:
    """插件 stdout/stderr 排空日志路径。"""
    return f"/tmp/zigtester-plugin-{plugin_name}.log"


def _start_plugin_process(
    hook: LifecycleHook,
    work_dir: str,
    plugin_config: dict[str, Any] | None = None,
    plugin_name: str = "plugin",
) -> subprocess.Popen:
    """启动插件钩子命令（shell 模式）。

    插件配置通过 PLUGIN_<KEY> 环境变量注入进程。

    stdout/stderr 均为 PIPE 且立即由 daemon 线程逐行排空到
    /tmp/zigtester-plugin-<插件名>.log — 否则子进程输出写满 OS 管道
    缓冲（macOS ~64KB）后会阻塞在 write 上，插件假死。
    """
    assert hook.command is not None
    merged_env = dict(os.environ)
    if plugin_config:
        for key, value in plugin_config.items():
            env_key = f"PLUGIN_{key.upper()}"
            merged_env[env_key] = str(value)
    proc = subprocess.Popen(
        hook.command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=merged_env,
        cwd=work_dir,
        shell=True,
    )
    # 排空线程：逐行写入日志文件（"w" truncate，同插件重复启动覆盖）
    logf = open(plugin_log_path(plugin_name), "wb")

    def _drain(stream) -> None:
        try:
            for line in stream:
                logf.write(line)
                logf.flush()
        except (ValueError, OSError):
            pass

    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            threading.Thread(target=_drain, args=(pipe,), daemon=True).start()
    return proc


def _kill_plugin_process(
    proc: subprocess.Popen, hook: LifecycleHook
) -> None:
    """杀死插件进程：SIGTERM → 2s → SIGKILL。"""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=min(2, hook.timeout))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    except Exception:
        pass


# ── 端口冲突检测 ──────────────────────────────────────────────


def _port_listening(host: str, port: int, timeout: float = 0.3) -> bool:
    """检测 TCP 端口是否正在被监听。"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((host, port))
        except OSError:
            return False
        finally:
            s.close()
        return True
    except Exception:
        return False


def check_port_conflicts(
    plugins: list[PluginConfig],
    host: str = "127.0.0.1",
) -> list[str]:
    """检查一组插件声明的端口是否与已监听端口冲突。

    两种冲突类型：
      1. 跨插件冲突 — 不同插件声明了相同端口（YAML 配置错误）
      2. 系统冲突 — 插件声明的端口已被系统中其他进程占用（残留进程）

    返回冲突描述列表（空列表 = 无冲突）。
    """
    conflicts: list[str] = []

    # 1) 跨插件重复端口检测
    port_to_plugin: dict[int, list[str]] = {}
    for plugin in plugins:
        for port in plugin.ports:
            port_to_plugin.setdefault(port, []).append(plugin.name)

    for port, names in sorted(port_to_plugin.items()):
        if len(names) > 1:
            conflicts.append(
                f"port {port} 被多个插件同时声明: {', '.join(names)}"
            )

    # 2) 系统已占用端口检测 — 仅本地插件（远程插件端口在远端 host，不占本机）
    for plugin in plugins:
        phost = _resolve_host(plugin)
        if not _is_local_host(phost):
            continue
        for port in plugin.ports:
            if _port_listening(phost, port):
                conflicts.append(
                    f"插件 {plugin.name} 声明的端口 {phost}:{port} 已被占用（可能是其他 zigtester 插件残留）"
                )

    return conflicts


# 自愈稳定期（秒）：重启后 verify 通过 ≠ 进程稳定。曾发生插件启动后
# 3 秒内自行退出，而自愈恰在"死亡窗口"内 verify 通过，造成"恢复成功"假象
# （ready_on 端口可连只代表刚启动时活着）。因此 verify 通过后额外等待
# 一个稳定期再复检一次，复检仍异常 → fast fail。
_HEAL_STABILITY_DELAY = 2.5


# ── 环境自检与自愈（pre-flight guard）───────────────────────
#
# 背景：兄弟项目会话可能绕过 zigtester 直接跑测试脚本 / 手动启停插件，
# 导致插件进程被误杀或端口被残留进程抢占。若不检测就继续执行，
# 测试结果会被误判（连不上依赖 → 全 FAIL，或连上残留旧实例 → 假结果）。
#
# 防线：每个测试套件执行前 ensure_ready() 自检 → 异常时自愈 →
# 自愈失败 fast fail 并输出测试环境规范。


def _descendant_pids(root_pid: int) -> set[int]:
    """收集 root_pid 及其全部后代 PID（一次 ps 调用构建 ppid 映射）。"""
    pids = {root_pid}
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,ppid"], capture_output=True, text=True, timeout=5
        )
        if out.returncode != 0:
            return pids
        ppid_of: dict[int, int] = {}
        for line in out.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                ppid_of[int(parts[0])] = int(parts[1])
        # 从每个已知 pid 向上找 root，命中则加入（避免递归实现）
        for pid in ppid_of:
            cur, hops = pid, 0
            while cur not in pids and hops < 16:
                cur = ppid_of.get(cur, 0)
                if cur == 0:
                    break
                if cur in pids:
                    pids.add(pid)
                    break
                hops += 1
    except Exception:
        pass
    return pids


def _port_owner_map() -> dict[int, set[int]] | None:
    """一次 lsof 拿全部监听端口 → {port: {pid}}（TCP LISTEN + UDP bound）。

    lsof 不可用或输出不可解析时返回 None（调用方降级处理）。
    """
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-iUDP"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return None
    except Exception:
        return None

    import re
    owners: dict[int, set[int]] = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 9 or parts[1] in ("PID",) or not parts[1].isdigit():
            continue
        pid = int(parts[1])
        name = parts[8]
        # TCP: 仅 LISTEN 状态；UDP: 排除已连接（->）的临时端口
        if "TCP" in parts[4] and "(LISTEN)" not in line:
            continue
        if "UDP" in parts[4] and "->" in name:
            continue
        m = re.search(r":(\d{1,5})(?:$|\s|->)", name)
        if m is None:
            continue
        port = int(m.group(1))
        owners.setdefault(port, set()).add(pid)
    return owners


def verify_plugin(plugin: PluginConfig, proc: subprocess.Popen | None) -> list[str]:
    """校验插件健康状态。返回问题列表（空 = 健康）。

    三层校验：
      1. 进程存活 — proc 退出即异常（仅本地模式；远程模式 proc=None）
      2. readiness 端口可连 — ready_on 声明的 TCP 端口（远程模式探测远端 host）
      3. 端口归属 — 声明的每个端口，其占用者必须包含本插件进程树成员
         （防外部进程抢端口；lsof 不可见 + 端口可连 = root 残留进程，同样视为异常；
          仅本地模式，远程端口在远端 host 不适用）
    """
    problems: list[str] = []
    host = _resolve_host(plugin)
    local = _is_local_host(host)

    # 1) 进程存活（仅本地模式）
    if local:
        if proc is None:
            problems.append(f"插件 {plugin.name} 本机进程缺失（远程模式误用 verify_plugin）")
            return problems
        if proc.poll() is not None:
            problems.append(
                f"插件 {plugin.name} 进程已退出 (exit={proc.returncode})"
            )
            return problems

    # 2) readiness 端口 — 探测 host 用插件级 host（远程 = 远端可达性）
    ro = None
    if plugin.lifecycle is not None:
        ro = plugin.lifecycle.start.ready_on
    if ro is not None and ro.type == "tcp" and ro.port:
        if not _port_listening(host, ro.port):
            problems.append(
                f"插件 {plugin.name} readiness 端口 {host}:{ro.port} 不可达"
            )

    # 3) 端口归属（仅本地模式）
    if local and plugin.ports:
        owner_map = _port_owner_map()
        if owner_map is not None:
            tree = _descendant_pids(proc.pid)
            for port in plugin.ports:
                owners = owner_map.get(port, set())
                if owners and not (owners & tree):
                    problems.append(
                        f"端口 {port} 被插件 {plugin.name} 进程树之外的进程占用 "
                        f"(PID {sorted(owners)})"
                    )
                elif not owners and _port_listening("127.0.0.1", port):
                    # 端口可连但 lsof 看不到占用者 → 不可见（root）进程
                    problems.append(
                        f"端口 {port} 被当前用户不可见的进程占用（可能是 root 残留进程），"
                        f"插件 {plugin.name} 无法接管"
                    )

    return problems


def _proc_cmdline(pid: int) -> str:
    """读取进程完整命令行（用于识别残留进程身份）。"""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def _cleanup_stale_processes(plugins: list[PluginConfig], ports: list[int]) -> tuple[bool, list[str]]:
    """清理声明端口上的残留插件进程。

    仅清理可识别为已知插件进程的占用者（命令行匹配插件 kill 名单或
    启动命令二进制名）；未知进程不动（避免误杀无关服务）。

    Returns:
        (是否全部清理干净, 未解决问题的描述列表)
    """
    unresolved: list[str] = []
    owner_map = _port_owner_map()
    if owner_map is None:
        # lsof 不可用 → 无法识别占用者，退化为 pkill 名单清理
        pass
    else:
        for port in ports:
            owners = owner_map.get(port, set())
            for pid in owners:
                cmdline = _proc_cmdline(pid)
                if not cmdline:
                    continue
                matched = any(
                    hint and hint in cmdline
                    for plugin in plugins
                    for hint in _plugin_proc_hints(plugin)
                )
                if not matched:
                    unresolved.append(
                        f"端口 {port} 被未知进程占用 (PID {pid}: {cmdline[:80]})"
                    )

    # 清理可识别的残留（kill 名单 + 启动命令特征）— 仅本地插件；
    # 远程插件服务在 host，本机 pkill 只会误杀本机同名进程
    for plugin in plugins:
        if not _is_local_host(_resolve_host(plugin)):
            continue
        _pkill_names(plugin)
    # 等待端口释放
    deadline = time.time() + 5
    while time.time() < deadline:
        if not any(_port_listening("127.0.0.1", p) for p in ports):
            break
        time.sleep(0.3)

    leftovers = [
        f"端口 {p} 仍被占用" for p in ports if _port_listening("127.0.0.1", p)
    ]
    return (not leftovers and not unresolved), unresolved + leftovers


def _plugin_proc_hints(plugin: PluginConfig) -> list[str]:
    """插件进程的命令行识别特征。

    仅两类安全特征：
      - plugin.yaml stop.kill 名单（如 local-echo / sing-box / xray）
      - start 命令中的脚本文件名（*.py，如 singbox_ctl.py / xray_ctl.py）

    不取解释器/子命令等通用 token 作为特征 — pkill -f 会误杀无关进程
    （如首 token python3 会误杀 zigtester 自身，子命令 serve 会误杀任何
    含该词的进程）。
    """
    hints: list[str] = []
    if plugin.lifecycle is not None:
        if plugin.lifecycle.stop.kill:
            hints.extend(plugin.lifecycle.stop.kill)
        cmd = plugin.lifecycle.start.command
        if cmd:
            try:
                tokens = shlex.split(cmd)
            except ValueError:
                tokens = cmd.split()
            for tok in tokens:
                base = os.path.basename(tok)
                if base.endswith(".py") and len(base) >= 5:
                    hints.append(base)
    return hints


def _pkill_names(plugin: PluginConfig) -> None:
    """按插件 kill 名单 + 启动命令特征清理残留进程。"""
    names = _plugin_proc_hints(plugin)
    for name in names:
        if name in (".", "..") or len(name) < 3:
            # 过短的匹配串会误杀无关进程（如 "xray" 之外的偶然匹配）
            continue
        try:
            subprocess.run(["pkill", "-f", name], capture_output=True, timeout=5)
        except Exception:
            pass
    if names:
        time.sleep(0.3)
        for name in names:
            if name in (".", "..") or len(name) < 3:
                continue
            try:
                subprocess.run(["pkill", "-9", "-f", name], capture_output=True, timeout=5)
            except Exception:
                pass


def env_spec_message(problems: list[str], plugins: list[PluginConfig]) -> str:
    """生成测试环境规范文本 — fast fail 时输出给 AI agent，指引正确做法。"""
    ports = sorted({p for plugin in plugins for p in plugin.ports})
    port_hint = ",".join(str(p) for p in ports[:8]) + ("..." if len(ports) > 8 else "")
    names = ", ".join(pl.name for pl in plugins) or "测试插件"
    lines = [
        f"测试环境自检失败（已尝试自动恢复未成功）: {'; '.join(problems)}",
        "==== 测试环境规范（AI agent 必读）====",
        f"1. 测试依赖进程（{names}）由 zigtester 插件统一启停 — 任何会话禁止手动启停这些进程（禁止 sing-box run / xray run / 直接运行 local-echo 二进制 / pkill 插件进程名）",
        "2. 所有测试必须经 zigtester 执行：zigtester run <project> 或 MCP zigtester_run（不要直接运行项目测试脚本 python3 tests/...，脚本只探测依赖不负责启动）",
        "3. 环境被破坏时不要手工修复 — 重新执行 zigtester run 即可，zigtester 会自动清理可识别的残留并恢复环境",
        f"排查: lsof -nP -iTCP -iUDP | grep -E '{port_hint}' ; ps aux | grep -E 'local-echo|sing-box|xray'",
        f"插件日志: {', '.join(plugin_log_path(pl.name) for pl in plugins)}",
        "======================================",
    ]
    return "\n".join(lines)


class PluginManager:
    """项目级插件生命周期管理 — 启动预检、每套件前自检自愈、停止。

    用法（runner.run_project）：
        mgr = PluginManager()
        fatal = mgr.prepare(cfg.plugins)      # 冲突预检 + 清残留 + 启动
        if fatal: fast_fail(...)
        for suite in suites:
            fatal = mgr.ensure_ready()        # 每套件前自检 + 自愈
            if fatal: fast_fail(...)
            execute(suite)
        mgr.stop_all()                        # finally 保证
    """

    def __init__(self) -> None:
        self._plugins: list[tuple[PluginConfig, subprocess.Popen]] = []

    @property
    def plugins(self) -> list[PluginConfig]:
        return [pcfg for pcfg, _ in self._plugins]

    def prepare(self, plugin_refs: list, zigtester_root: str | None = None) -> str | None:
        """预检（跨插件冲突 + 残留清理）并启动全部插件。

        Args:
            plugin_refs: 项目声明的插件引用（PluginRef 列表）
            zigtester_root: zigtester 根目录（测试注入用；默认自动探测）

        Returns:
            致命错误描述（含测试环境规范）或 None（全部就绪）。
        """
        try:
            available = discover_plugins(zigtester_root or _find_zigtester_root())
        except Exception:
            available = {}

        # 第一遍：解析全部插件配置（合并项目级覆盖），暂不启动
        all_parsed: list[tuple[Any, PluginConfig]] = []
        for plugin_ref in plugin_refs:
            if plugin_ref.name not in available:
                return (
                    f"插件 {plugin_ref.name} 未找到（zigtester/plugins/ 下无此插件）"
                )
            pcfg = parse_plugin_config(available[plugin_ref.name])
            if pcfg is None:
                return f"插件 {plugin_ref.name} 的 plugin.yaml 解析失败"
            if plugin_ref.config:
                pcfg.config.update(plugin_ref.config)
            # 项目级 host 覆盖（优先级最高）：zigtester.yaml plugins.<name>.host
            if plugin_ref.host:
                pcfg.host = plugin_ref.host
            all_parsed.append((plugin_ref, pcfg))

        if not all_parsed:
            return None

        pcfgs = [pcfg for _, pcfg in all_parsed]

        # 跨插件端口重复声明 — 配置错误，无法自愈
        conflicts = check_port_conflicts(pcfgs)
        dup = [c for c in conflicts if "被多个插件同时声明" in c]
        if dup:
            return env_spec_message(
                ["跨插件端口重复声明（zigtester.yaml / plugin.yaml 配置错误）: " + "; ".join(dup)],
                pcfgs,
            )

        # 系统占用 → 尝试自动清理残留（可识别的插件进程）
        occupied = [c for c in conflicts if "已被占用" in c]
        if occupied:
            all_ports = [p for pl in pcfgs for p in pl.ports]
            ok, unresolved = _cleanup_stale_processes(pcfgs, all_ports)
            if not ok:
                return env_spec_message(
                    ["插件端口被占用且自动清理失败: " + "; ".join(occupied + unresolved)],
                    pcfgs,
                )

        # 启动（本地）或远端就绪探测（远程）
        for _, pcfg in all_parsed:
            if not _is_local_host(_resolve_host(pcfg)):
                # 远程模式：服务已在 host 运行，本机不 build/start，仅就绪探测
                ro = None
                if pcfg.lifecycle is not None:
                    ro = pcfg.lifecycle.start.ready_on
                if ro is not None and ro.type == "tcp":
                    phost = _resolve_host(pcfg)
                    if not _wait_tcp_ready(phost, ro.port, ro.timeout, ro.interval):
                        self.stop_all()
                        return env_spec_message(
                            [f"远端插件 {pcfg.name} 就绪探测失败（{phost}:{ro.port} 不可达）"],
                            pcfgs,
                        )
                self._plugins.append((pcfg, None))
                continue
            build_plugin(pcfg)
            proc = start_plugin(pcfg, pcfg.path)
            if proc is None:
                # 启动失败 → 清理已启动的，fast fail
                problems = [f"插件 {pcfg.name} 启动失败（build/command/ready_on 见上方输出）"]
                self.stop_all()
                return env_spec_message(problems, pcfgs)
            self._plugins.append((pcfg, proc))

        return None

    def ensure_ready(self) -> str | None:
        """每个测试套件执行前自检全部插件；异常时自愈；自愈失败返回致命错误。"""
        for idx, (pcfg, proc) in enumerate(self._plugins):
            problems = verify_plugin(pcfg, proc)
            if not problems:
                continue
            # 自愈：stop（含 kill 名单清理）→ 等端口释放 → 重启 → 复检
            print(
                f"[plg] 环境异常，尝试自动恢复插件 {pcfg.name}: {'; '.join(problems)}",
                file=sys.stderr,
            )
            healed_proc, remaining = self._heal(pcfg, proc)
            self._plugins[idx] = (pcfg, healed_proc) if healed_proc else (pcfg, proc)
            if remaining:
                return env_spec_message(remaining, self.plugins)
            print(f"[plg] 插件 {pcfg.name} 自动恢复成功", file=sys.stderr)
        return None

    def _heal(
        self, pcfg: PluginConfig, proc: subprocess.Popen | None
    ) -> tuple[subprocess.Popen | None, list[str]]:
        """自愈单个插件：停止 + 清残留 + 重启 + 复检。

        远程插件（服务在 host）无法在本机重启 → 仅复检；探测失败即 fast fail
        （由调用方 ensure_ready 按 remaining 决定是否中止）。
        """
        if not _is_local_host(_resolve_host(pcfg)):
            remaining = verify_plugin(pcfg, None)
            if remaining:
                return None, [
                    f"远端插件 {pcfg.name} 仍不可达: " + "; ".join(remaining)
                ]
            return None, []

        stop_plugin(proc, pcfg)
        if pcfg.ports:
            deadline = time.time() + 5
            while time.time() < deadline:
                if not any(_port_listening("127.0.0.1", p) for p in pcfg.ports):
                    break
                time.sleep(0.3)
        new_proc = start_plugin(pcfg, pcfg.path)
        if new_proc is None:
            return None, [f"插件 {pcfg.name} 自愈重启失败"]
        remaining = verify_plugin(pcfg, new_proc)
        if remaining:
            return new_proc, remaining
        # 延迟复检：verify 通过只代表"此刻活着"，不能排除启动后秒退的
        # 死亡窗口假阳性 — 等一个稳定期再复检一次
        time.sleep(_HEAL_STABILITY_DELAY)
        remaining = verify_plugin(pcfg, new_proc)
        if remaining:
            return new_proc, [
                f"插件 {pcfg.name} 自愈后稳定期复检失败（疑似启动后短命进程）: "
                + "; ".join(remaining)
            ]
        return new_proc, []

    def stop_all(self) -> None:
        """逆序停止全部插件（幂等，可重复调用）。"""
        for pcfg, proc in reversed(self._plugins):
            try:
                stop_plugin(proc, pcfg)
            except Exception:
                pass
        self._plugins.clear()

