"""插件体系 — 可复用测试基础设施的配置解析与生命周期管理。

插件是自包含目录，包含 `plugin.yaml` 清单文件。
zigtester 负责发现、构建、启动、就绪检测、停止。
"""

from __future__ import annotations

import os
import shlex
import subprocess
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
    build: PluginBuild = field(default_factory=PluginBuild)
    lifecycle: PluginLifecycle | None = None
    config: dict[str, Any] = field(default_factory=dict)  # 项目级覆盖配置


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

    # 加载插件默认配置（后续可被项目级 zigtester.yaml 覆盖）
    config_raw = raw.get("config")
    if isinstance(config_raw, dict):
        config = dict(config_raw)
    else:
        config = {}

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
            build=build,
            config=config,
            lifecycle=None,
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
        build=build,
        config=config,
        lifecycle=lifecycle,
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
        proc = _start_plugin_process(hook, work_dir, plugin.config)

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
    if plugin.lifecycle is None:
        return

    stop_hook = plugin.lifecycle.stop

    # 1) 执行 stop 命令
    if stop_hook.command is not None:
        try:
            sp = _start_plugin_process(stop_hook, plugin.path)
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


def _start_plugin_process(
    hook: LifecycleHook, work_dir: str, plugin_config: dict[str, Any] | None = None
) -> subprocess.Popen:
    """启动插件钩子命令（shell 模式）。

    插件配置通过 PLUGIN_<KEY> 环境变量注入进程。
    """
    assert hook.command is not None
    merged_env = dict(os.environ)
    if plugin_config:
        for key, value in plugin_config.items():
            env_key = f"PLUGIN_{key.upper()}"
            merged_env[env_key] = str(value)
    return subprocess.Popen(
        hook.command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=merged_env,
        cwd=work_dir,
        shell=True,
    )


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
