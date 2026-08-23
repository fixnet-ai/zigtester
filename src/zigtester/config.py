"""配置解析与校验 — YAML 解析、数据模型、模板生成。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ── 数据模型 ──────────────────────────────────────────────

DEFAULT_TIMEOUT = 120
VALID_LEVELS = ("unit", "functional", "performance")
VALID_PARSERS = ("zig_test", "line_count", "test_protocols", "bench", "custom")
VALID_STATUSES = ("PASS", "FAIL", "SKIP", "ERROR")


@dataclass
class MetricDef:
    """性能指标定义 — 从 stdout 提取的正则模式。"""
    name: str
    pattern: str       # 正则，含一个捕获组


@dataclass
class Threshold:
    """阈值约束 — 可选 min/max，违反时套件标记为 FAIL。"""
    min: float | None = None
    max: float | None = None


@dataclass
class ResourceLimits:
    """资源上限 — 超限时记入告警。"""
    memory_mb: float | None = None
    fd_count: int | None = None
    cpu_percent: float | None = None


@dataclass
class ReadyOn:
    """就绪检测探头 — setup 命令执行后等待服务就绪。"""
    type: str = "tcp"            # tcp | process
    port: int = 0
    host: str = "127.0.0.1"
    timeout: int = 30
    interval: float = 0.5


@dataclass
class LifecycleHook:
    """生命周期钩子 — setup 或 teardown 阶段执行的命令。"""
    command: str | None = None   # 要执行的命令（None = 无命令，仅依赖 kill 清理）
    timeout: int = 30             # 独立超时（秒），不计入 suite 的 test timeout
    ready_on: ReadyOn | None = None   # setup 成功后等待就绪的探头
    kill: list[str] | None = None     # teardown 时按进程名清理


@dataclass
class ResourceSnapshot:
    """资源采样快照 — monitor.py 产出(min/avg/peak)。"""
    pid: int = 0
    peak_memory_mb: float = 0.0
    avg_memory_mb: float = 0.0
    min_memory_mb: float = 0.0
    peak_fd_count: int = 0
    avg_fd_count: float = 0.0
    min_fd_count: int = 0
    peak_cpu_pct: float = 0.0
    avg_cpu_pct: float = 0.0
    min_cpu_pct: float = 0.0
    sample_count: int = 0


@dataclass
class ResourceSampling:
    """资源采集配置 — settings.resource_sampling。"""
    interval_s: float = 0.2        # 采样间隔(秒)
    leak_window_s: float = 30.0    # 泄漏判定前/后窗口宽(秒)
    analyze_leak: bool = True      # 是否对长时(per_suite_only)套件算泄漏判定


@dataclass
class ProjectSettings:
    """项目全局设置。"""
    work_dir: str = "."
    build_command: str | None = None
    timeout_default: int = 120
    env: dict[str, str] = field(default_factory=dict)
    resource_sampling: ResourceSampling = field(default_factory=ResourceSampling)


@dataclass
class SuiteConfig:
    """单个测试套件配置。"""
    name: str
    command: str
    timeout: int = DEFAULT_TIMEOUT
    sudo: bool = False
    per_suite_only: bool = False    # 仅允许 --suite 单独运行；level 全量执行时自动跳过
    parser: str = "line_count"
    depends_on: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    metrics: list[MetricDef] = field(default_factory=list)
    thresholds: dict[str, Threshold] = field(default_factory=dict)
    resource_limits: ResourceLimits | None = None
    target: str | None = None    # 资源采集目标进程名（如 "test-engine"）；未声明时只采命令进程本身
    setup: LifecycleHook | None = None
    teardown: LifecycleHook | None = None
    # 资源采集透传字段(parse 时从 settings.resource_sampling + 套件覆盖落值)
    sampling_interval_s: float = 0.2
    analyze_leak: bool = False          # 框架泄漏判定(默认仅 performance+per_suite_only 自动开)
    leak_window_s: float = 30.0

    @property
    def qualified_name(self) -> str:
        return self.name


@dataclass
class LevelConfig:
    """测试层级配置 — 包含多个 SuiteConfig。"""
    suites: list[SuiteConfig] = field(default_factory=list)


@dataclass
class PluginRef:
    """插件引用 — 项目 zigtester.yaml 中声明的插件及覆盖配置。"""
    name: str
    config: dict[str, Any] = field(default_factory=dict)
    host: str | None = None    # 插件服务器 IP（None=按 plugin.yaml host > ZIGTESTER_PLUGIN_HOST > 127.0.0.1）


@dataclass
class ProjectConfig:
    """项目完整配置。"""
    project: str
    project_id: str | None = None     # UUID v4，项目稳定身份（不随目录移动变化）
    description: str = ""
    settings: ProjectSettings = field(default_factory=ProjectSettings)
    levels: dict[str, LevelConfig] = field(default_factory=dict)
    plugins: list[PluginRef] = field(default_factory=list)


@dataclass
class SuiteResult:
    """单套件执行结果。"""
    suite_name: str
    level: str
    status: str = "SKIP"
    duration_ms: float = 0.0
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    metrics: dict[str, float] = field(default_factory=dict)
    resource_peak: ResourceSnapshot = field(default_factory=ResourceSnapshot)
    message: str = ""
    setup_error: str = ""
    teardown_error: str = ""


@dataclass
class ProjectResult:
    """项目执行结果。"""
    project: str
    path: str
    suites: list[SuiteResult] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass
class WorkspaceResult:
    """跨项目聚合结果。"""
    projects: list[ProjectResult] = field(default_factory=list)


@dataclass
class Regression:
    """回归检测结果。"""
    metric: str
    current: float
    baseline_avg: float
    pct_change: float
    is_regression: bool


# ── YAML 解析 ─────────────────────────────────────────────

def _env_subst(value: str) -> str:
    """替换字符串中的 ${VAR} 环境变量。"""
    def _repl(m: re.Match) -> str:
        return os.environ.get(m.group(1), "")
    return re.sub(r"\$\{(\w+)\}", _repl, value)


def _parse_metric_def(raw: dict) -> MetricDef:
    return MetricDef(
        name=str(raw["name"]),
        pattern=str(raw["pattern"]),
    )


def _parse_threshold(raw: dict) -> Threshold:
    return Threshold(
        min=float(raw["min"]) if "min" in raw else None,
        max=float(raw["max"]) if "max" in raw else None,
    )


def _parse_ready_on(raw: dict | None) -> ReadyOn | None:
    """解析就绪检测探头配置。"""
    if raw is None:
        return None
    return ReadyOn(
        type=str(raw.get("type", "tcp")),
        port=int(raw.get("port", 0)),
        host=str(raw.get("host", "127.0.0.1")),
        timeout=int(raw.get("timeout", 30)),
        interval=float(raw.get("interval", 0.5)),
    )


def _parse_lifecycle_hook(raw: dict | None) -> LifecycleHook | None:
    """解析生命周期钩子配置。"""
    if raw is None:
        return None
    return LifecycleHook(
        command=raw.get("command"),
        timeout=int(raw.get("timeout", 30)),
        ready_on=_parse_ready_on(raw.get("ready_on")),
        kill=raw.get("kill"),
    )


def _parse_resource_limits(raw: dict | None) -> ResourceLimits | None:
    if raw is None:
        return None
    return ResourceLimits(
        memory_mb=float(raw["memory_mb"]) if "memory_mb" in raw else None,
        fd_count=int(raw["fd_count"]) if "fd_count" in raw else None,
        cpu_percent=float(raw["cpu_percent"]) if "cpu_percent" in raw else None,
    )


def _parse_suite(
    raw: dict, global_settings: ProjectSettings, level: str = ""
) -> SuiteConfig:
    """解析单个套件配置，全局设置作为默认值。"""
    timeout = raw.get("timeout", global_settings.timeout_default)
    # 合并全局 env 和套件 env（套件优先）
    env = dict(global_settings.env)
    env.update(raw.get("env", {}))

    metrics = [_parse_metric_def(m) for m in raw.get("metrics", [])]
    thresholds = {
        k: _parse_threshold(v) if isinstance(v, dict) else v
        for k, v in raw.get("thresholds", {}).items()
    }

    per_suite_only = bool(raw.get("per_suite_only", False))
    rs = global_settings.resource_sampling
    suite_name = str(raw["name"])
    # 泄漏判定默认: performance 层 + 长时套件(名字含 long,如 bench-long-*)自动开;
    # 套件级显式 analyze_leak 优先。短时基准套件 metrics 保持干净(趋势噪音无意义)。
    analyze_leak = raw.get(
        "analyze_leak",
        bool(level == "performance" and rs.analyze_leak and "long" in suite_name),
    )

    return SuiteConfig(
        name=str(raw["name"]),
        command=_env_subst(str(raw["command"])),
        timeout=int(timeout),
        sudo=bool(raw.get("sudo", False)),
        per_suite_only=per_suite_only,
        parser=str(raw.get("parser", "line_count")),
        depends_on=[str(d) for d in raw.get("depends_on", [])],
        env=env,
        metrics=metrics,
        thresholds=thresholds,
        resource_limits=_parse_resource_limits(raw.get("resource_limits")),
        target=raw.get("target"),
        setup=_parse_lifecycle_hook(raw.get("setup")),
        teardown=_parse_lifecycle_hook(raw.get("teardown")),
        sampling_interval_s=rs.interval_s,
        analyze_leak=bool(analyze_leak),
        leak_window_s=float(raw.get("leak_window_s", rs.leak_window_s)),
    )


def _parse_settings(raw: dict | None) -> ProjectSettings:
    if raw is None:
        return ProjectSettings()
    rs_raw = raw.get("resource_sampling") or {}
    return ProjectSettings(
        work_dir=str(raw.get("work_dir", ".")),
        build_command=raw.get("build_command"),
        timeout_default=int(raw.get("timeout_default", DEFAULT_TIMEOUT)),
        env={str(k): str(v) for k, v in raw.get("env", {}).items()},
        resource_sampling=ResourceSampling(
            interval_s=float(rs_raw.get("interval_s", 0.2)),
            leak_window_s=float(rs_raw.get("leak_window_s", 30.0)),
            analyze_leak=bool(rs_raw.get("analyze_leak", True)),
        ),
    )


def parse_config(path: str) -> ProjectConfig:
    """解析 zigtester.yaml 文件，返回 ProjectConfig。

    未提供值使用合理默认值填充。
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"配置文件为空: {path}")

    project = str(raw["project"])
    project_id = str(raw["id"]) if "id" in raw else None
    description = str(raw.get("description", ""))
    settings = _parse_settings(raw.get("settings"))

    levels: dict[str, LevelConfig] = {}
    raw_levels = raw.get("levels", {})
    for level_name in VALID_LEVELS:
        suite_list = raw_levels.get(level_name, [])
        if suite_list is None:
            suite_list = []
        suites = [_parse_suite(s, settings, level_name) for s in suite_list]
        levels[level_name] = LevelConfig(suites=suites)

    plugins: list[PluginRef] = []
    for p in (raw.get("plugins") or []):
        if isinstance(p, str):
            plugins.append(PluginRef(name=p))
        elif isinstance(p, dict):
            overrides = p.get("config")
            host_raw = p.get("host")
            plugins.append(PluginRef(
                name=str(p["name"]),
                config=dict(overrides) if isinstance(overrides, dict) else {},
                host=str(host_raw) if host_raw is not None else None,
            ))
        else:
            pass  # 忽略无效条目

    return ProjectConfig(
        project=project,
        project_id=project_id,
        description=description,
        settings=settings,
        levels=levels,
        plugins=plugins,
    )


# ── 校验 ──────────────────────────────────────────────────

def validate_config(raw: dict) -> list[str]:
    """校验配置字典，返回错误信息列表（空列表 = 通过）。"""
    errors: list[str] = []

    if not isinstance(raw, dict):
        return ["配置必须是 dict/object"]

    # project
    if "project" not in raw:
        errors.append("缺少必填字段: project")
    else:
        p = raw["project"]
        if not isinstance(p, str) or not re.match(r"^[a-z][a-z0-9_-]*$", p):
            errors.append(f"project 格式无效: {p!r}，需满足 ^[a-z][a-z0-9_-]*$")

    # levels
    if "levels" not in raw:
        errors.append("缺少必填字段: levels")
    else:
        lv = raw["levels"]
        if not isinstance(lv, dict):
            errors.append("levels 必须是 dict/object")
        else:
            for level_name in lv:
                if level_name not in VALID_LEVELS:
                    errors.append(
                        f"未知层级: {level_name!r}，有效值: {', '.join(VALID_LEVELS)}"
                    )
                suites = lv[level_name]
                if not isinstance(suites, list):
                    errors.append(f"levels.{level_name} 必须是数组")
                    continue
                for i, suite in enumerate(suites):
                    if not isinstance(suite, dict):
                        errors.append(f"levels.{level_name}[{i}] 必须是 dict/object")
                        continue
                    if "name" not in suite:
                        errors.append(f"levels.{level_name}[{i}] 缺少 name")
                    if "command" not in suite:
                        errors.append(f"levels.{level_name}[{i}] 缺少 command")
                    parser = suite.get("parser", "line_count")
                    if parser not in VALID_PARSERS:
                        errors.append(
                            f"levels.{level_name}[{i}].parser 无效: {parser!r}，"
                            f"有效值: {', '.join(VALID_PARSERS)}"
                        )
                    # 校验 setup/teardown
                    for hook_key in ("setup", "teardown"):
                        hook = suite.get(hook_key)
                        if hook is not None:
                            if not isinstance(hook, dict):
                                errors.append(f"levels.{level_name}[{i}].{hook_key} 必须是 dict/object")
                                continue
                            ro = hook.get("ready_on")
                            if ro is not None:
                                if not isinstance(ro, dict):
                                    errors.append(f"levels.{level_name}[{i}].{hook_key}.ready_on 必须是 dict/object")
                                else:
                                    rt = ro.get("type", "tcp")
                                    if rt not in ("tcp", "process"):
                                        errors.append(f"levels.{level_name}[{i}].{hook_key}.ready_on.type 无效: {rt!r}，有效值: tcp, process")
                            kill = hook.get("kill")
                            if kill is not None and not isinstance(kill, list):
                                errors.append(f"levels.{level_name}[{i}].{hook_key}.kill 必须是数组")

    # settings (可选)
    if "settings" in raw:
        s = raw["settings"]
        if not isinstance(s, dict):
            errors.append("settings 必须是 dict/object")

    return errors


# ── 模板生成 ──────────────────────────────────────────────

import uuid as _uuid


def _new_project_id() -> str:
    """生成新的 UUID v4 项目标识。"""
    return str(_uuid.uuid4())


_TEMPLATE = """# zigtester 测试配置 — 自动生成
# 项目标识（勿手动修改 id）
project: {project_name}
id: {project_id}
description: ""

# 全局设置
settings:
  work_dir: "."
  build_command: "zig build"     # 可选：测试前自动构建
  timeout_default: 120            # 默认超时（秒）
  # env:                         # 全局环境变量
  #   ZIG_EXE: zig

# 测试层级
levels:
  unit:
    - name: "all-tests"
      command: "zig build test"
      parser: zig_test
      timeout: 120

  # functional:
  #   - name: "smoke-test"
  #     command: "python3 tests/test_smoke.py"
  #     timeout: 60

  # performance:
  #   - name: "bench-baseline"
  #     command: "python3 tests/test_bench.py -c 10 -n 100"
  #     timeout: 120
  #     metrics:
  #       - name: throughput
  #         pattern: "吞吐: ([0-9.]+) req/s"
  #     thresholds:
  #       throughput:
  #         min: 100
  #     # setup:                   # 可选：测试前启动依赖服务
  #     #   command: "zig-out/bin/mock-server"
  #     #   timeout: 15
  #     #   ready_on:
  #     #     type: tcp
  #     #     port: 9999
  #     # teardown:                # 可选：测试后清理（失败/超时也会执行）
  #     #   kill: ["mock-server"]

  # performance 长时持续 + 资源趋势（原 stress 层并入性能层，压力=性能测试的长时形态）:
  #   - name: "bench-long"
  #     command: "python3 tests/test_bench.py --long --duration 40"
  #     timeout: 300
  #     per_suite_only: true    # 长时套件仅允许 --suite 单跑，禁止混入 --level performance 全量
  #     resource_limits:
  #       memory_mb: 200
  #       fd_count: 500
"""


def generate_template(project_name: str) -> str:
    """为项目生成初始 zigtester.yaml 内容。"""
    return _TEMPLATE.format(
        project_name=project_name,
        project_id=_new_project_id(),
    )


def ensure_project_id(config_path: str, config: ProjectConfig) -> str:
    """确保项目有 UUID 标识。

    若已有 id 则直接返回；否则生成新 UUID 并写回 zigtester.yaml
    （保留文件原有结构和注释，仅在 `project:` 行后追加一行 `id:`）。

    Returns:
        项目的稳定 UUID（已有或新生成的）
    """
    if config.project_id is not None:
        return config.project_id

    new_id = _new_project_id()
    config.project_id = new_id

    # 读原文件，在 project: 行后插入 id: 行
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        inserted = False
        new_lines: list[str] = []
        for line in lines:
            new_lines.append(line)
            if not inserted and line.startswith("project:"):
                new_lines.append(f"id: {new_id}\n")
                inserted = True

        if inserted:
            with open(config_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
    except OSError:
        pass  # 写回失败不阻塞测试

    return new_id
