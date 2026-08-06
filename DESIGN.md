# zigtester — 自动测试框架设计

> **状态**：Phase 2 代码实现已完成（2026-08-07），核心框架可运行。
> **仓库**：`github.com/fixnet-ai/zigtester`（独立仓库，通过 pip/gh 安装分发）

## Context

当前 6 个兄弟项目（zigfoundation、zigtun、zigproxy、zigdns、zigoutbounds、zigbox）各自拥有独立的测试体系，缺乏统一的测试配置格式、跨项目执行器、标准报告输出和历史回归检测。zigtester 目标是成为一个可被每个项目复用的 Python 自动测试框架，通过标准化配置文件自动扫描并执行单元测试、功能测试、性能测试和压力测试。

## 设计原则

1. **元框架** — 包装现有测试工具（`zig build test`、Python 测试脚本），不替换它们
2. **约定优于配置** — 合理默认值，需要时覆盖
3. **声明式配置** — 每个项目只需放一个 `zigtester.yaml`
4. **渐进接入** — 项目按需添加配置文件即可接入，不强制改造现有测试
5. **可插拔** — 易于添加新的测试类型、协议、指标解析器
6. **MCP 优先** — Claude Code 通过 MCP 工具调用，服务端处理原始数据，只向 Claude 返回结构化摘要，最大化节省 token

## 集成方式：MCP 优先 + CLI 辅助

### 为什么 MCP 而非 Skill

| 对比维度 | Skill 方式 | MCP 方式 |
|---------|-----------|---------|
| **指令加载** | 每次 ~3-5K tokens 加载 SKILL.md | ~50 tokens/工具描述，只加载一次 |
| **测试输出** | 原始 stdout 全量进 context | 服务端解析，只返回结构化摘要 |
| **zig build test 500行输出** | 500 行全进 | → `{passed:88, failed:0, skipped:1}` |
| **压测 100 次原始数据** | 全部进 context | → 服务端计算分位数，只返回 p50/p99 |
| **跨 6 项目 scan** | Claude 手动 ls/read 每个项目 | 一次 `zigtester_scan` 返回结构化列表 |
| **性能历史 30 条** | 全量文本 | → 只返回趋势 + 异常标记 |

**结论**：测试框架是 MCP 的理想场景 — 大量原始数据在服务端处理，只有结构化摘要进入 Claude context。token 节省可达 10-50x。

### 双层架构

```
┌─ Claude Code ─────────────────────────────────────────────┐
│  MCP 工具调用 (结构化请求/响应，极省 token)                  │
│  ├─ zigtester_scan    ← 发现项目                           │
│  ├─ zigtester_run     ← 执行测试                           │
│  ├─ zigtester_list    ← 列出套件                           │
│  ├─ zigtester_history ← 查看历史                           │
│  └─ zigtester_init    ← 生成配置                           │
└───────┬────────────────────────────────────────────────────┘
        │ MCP 协议 (JSON-RPC over stdio)
        ▼
┌─ zigtester MCP Server ────────────────────────────────────┐
│  src/server.py                                             │
│  ├─ 接收 MCP 请求                                          │
│  ├─ 调用核心模块 (scanner/runner/reporter/history)         │
│  ├─ 解析原始输出 → 结构化结果                               │
│  └─ 返回精简 JSON 摘要                                     │
└────────────────────────────────────────────────────────────┘
        │
        ▼  (同时支持)
┌─ zigtester CLI ───────────────────────────────────────────┐
│  zigtester scan|run|list|history|init                      │
│  用途：人类终端手动执行、CI/CD pipeline、cron 定时任务        │
└────────────────────────────────────────────────────────────┘
```

CLI 和 MCP Server 共享同一套核心模块，只是入口不同：
- **CLI** → 人类可读的彩色终端输出
- **MCP** → 结构化 JSON，精简到只含 Claude 需要的信息

## 目录结构

```
zigtester/
├── pyproject.toml              # Python 包 + MCP server 入口点
├── README.md
├── DESIGN.md                   # 本文件
├── SKILL.md                    # 轻量 skill：告知 Claude 使用 MCP 工具
├── src/
│   ├── __init__.py
│   ├── cli.py                  # CLI 入口（argparse）
│   ├── server.py               # MCP Server 入口（FastMCP / raw stdio）
│   ├── scanner.py              # 配置发现
│   ├── config.py               # 配置解析与校验
│   ├── runner.py               # 测试执行引擎
│   ├── reporter.py             # 输出格式化
│   ├── metrics.py              # 性能指标提取
│   ├── monitor.py              # 资源监控
│   └── history.py              # 历史存储 + 回归检测
└── schemas/
    └── zigtester.schema.json   # 配置文件 JSON Schema
```

## 配置文件格式 (`zigtester.yaml`)

```yaml
# 项目标识
project: zigbox
description: "sing-box 复刻 — 编排层"

# 全局设置
settings:
  work_dir: "."               # 工作目录（默认配置文件所在目录）
  build_command: "zig build"  # 可选：测试前自动构建
  timeout_default: 120        # 默认超时（秒）
  env:                        # 全局环境变量
    ZIG_EXE: zig

# 测试层级
levels:
  unit:
    - name: "all-unit-tests"
      command: "zig build test"
      timeout: 120
      parser: zig_test        # 内置解析器：解析 zig build test 输出
      
  functional:
    - name: "protocol-tests"
      command: "python3 tests/test_protocols.py"
      timeout: 60
      sudo: true
      
  performance:
    - name: "bench-socks5"
      command: "python3 tests/test_bench.py --mode socks5 -c 10 -n 100"
      timeout: 120
      metrics:                # 从 stdout 提取的指标
        - name: throughput_reqs_per_sec
          pattern: "吞吐: ([0-9.]+) req/s"
        - name: latency_p99_ms
          pattern: "p99: ([0-9.]+)ms"
      thresholds:             # 可选：低于/高于阈值视为失败
        throughput_reqs_per_sec:
          min: 100
        latency_p99_ms:
          max: 500
          
  stress:
    - name: "concurrency-stress"
      command: "python3 tests/test_bench.py --mode all -c 50 -n 1000"
      timeout: 300
      resource_limits:        # 资源上限
        memory_mb: 200
        fd_count: 500
        cpu_percent: 80
```

## CLI 接口

### CLI（人类终端）

```
命令：
  zigtester scan [--dir <path>]         扫描项目，发现所有 zigtester.yaml
  zigtester run [<project>] [options]   运行指定项目的测试
  zigtester list [<project>]            列出项目中的所有测试套件
  zigtester history <project>           查看性能历史
  zigtester init [--dir <path>]         为项目生成初始 zigtester.yaml

run 选项：
  --level unit|functional|performance|stress|all  测试层级（默认 all）
  --suite <name>                                  运行指定套件
  --all                                            运行所有已发现项目
  --report-format terminal|markdown|json           输出格式（默认 terminal）
  --json-output <path>                             JSON 输出路径
  --no-build                                       跳过构建步骤
  --verbose                                        详细输出
  --fail-fast                                      首个失败即停止
```

### MCP 工具（Claude Code 调用）

#### `zigtester_scan`
发现所有包含 `zigtester.yaml` 的项目。

```
输入: {dir: "/Users/dasimo/works/2025/fixnet"}  // 可选，默认父目录
输出: {
  projects: [
    {name: "zigfoundation", path: "...", levels: ["unit"]},
    {name: "zigbox", path: "...", levels: ["unit","functional","performance","stress"]},
  ]
}
```

#### `zigtester_list`
列出项目的测试套件。

```
输入: {project: "zigbox"}
输出: {
  project: "zigbox",
  levels: {
    unit: [{name: "all-tests", command: "zig build test", parser: "zig_test"}],
    functional: [{name: "protocols", command: "python3 tests/test_protocols.py", sudo: true}],
  }
}
```

#### `zigtester_run`
执行测试并返回结构化结果（**关键**：服务端解析输出，只返回摘要）。

```
输入: {project: "zigfoundation", level: "unit", suite: "all-tests"}
输出: {
  project: "zigfoundation",
  elapsed_ms: 3421,
  suites: [{
    name: "all-tests",
    level: "unit", 
    status: "PASS",
    duration_ms: 3200,
    exit_code: 0,
    summary: "88/88 passed, 1 skipped",
    metrics: {tests_passed: 88, tests_failed: 0, tests_skipped: 1},
    resource: {peak_memory_mb: 45, peak_cpu_pct: 120},
    failure_detail: null
  }]
}
```

#### `zigtester_history`
查看性能历史，自动检测回归。

```
输入: {project: "zigbox", suite: "bench-socks5", limit: 10}
输出: {
  project: "zigbox",
  suite: "bench-socks5",
  runs: [
    {timestamp: "2026-08-07T10:00:00Z", throughput: 520, p99_ms: 85},
  ],
  regressions: [
    {metric: "throughput", current: 520, baseline_avg: 550, pct_change: -5.5, is_regression: false}
  ]
}
```

#### `zigtester_init`
为项目生成初始 `zigtester.yaml` 配置模板。

```
输入: {dir: "/path/to/project", project: "myproject"}
输出: {path: "/path/to/project/zigtester.yaml", created: true}
```

## 核心模块设计

### 1. scanner.py — 配置发现

- 从指定目录递归向上查找 `zigtester.yaml`
- 或扫描 `--dir` 下的所有子目录
- 返回 `DiscoveredProject` 列表（name, path, config）
- 缓存扫描结果

### 2. config.py — 配置解析

- YAML 解析 + JSON Schema 校验
- 默认值填充（timeout=120, parser=line_count）
- 环境变量插值 `${VAR}`
- `Suite` 和 `Level` 数据类

### 3. runner.py — 执行引擎

核心类：
- `TestExecutor`: 单套件执行器（子进程启动/sudo支持/超时控制/stdout捕获/资源监控）
- `DependencyResolver`: 拓扑排序依赖
- `LevelRunner`: 按层级顺序执行

### 4. reporter.py — 输出

复用 zigbox `tests/lib/report.py` 的 `TestResult`/`TestSuite` 模式并增强：
- `TestResult`: name, status(PASS/FAIL/SKIP/ERROR), duration_ms, message, metrics
- `TestSuite`: 聚合多个 TestResult，统计通过/失败/跳过
- `WorkspaceReport`: 跨项目聚合
- 三路输出：终端 ANSI 彩色 / Markdown 表格 / JSON

### 5. metrics.py — 性能指标

- `MetricExtractor`: 从 stdout 用正则提取指标
- 内置提取器：`zig_test`（解析 `X/Y passed; Z skipped`）、`bench_csv`（解析 CSV）
- 分位数计算（线性插值）+ 阈值检查

### 6. monitor.py — 资源监控

- 后台线程，每秒采样进程树 RSS/fd/CPU%（psutil）
- 报告峰值和平均值 + 超限告警

### 7. history.py — 历史回归

- 存储路径：`~/.zigtester/history/<project>/<suite>/<timestamp>.json`
- 保留最近 30 次运行
- `regression_check()`: 当前 vs 最近 5 次移动平均，>20% 退化标记为 REGRESSION

## 模块接口规范

### 数据模型

```python
# config.py

@dataclass
class SuiteConfig:
    """单个测试套件配置"""
    name: str                          # 唯一标识
    command: str                       # 执行命令
    timeout: int = 120                 # 超时秒数
    sudo: bool = False                 # 是否需要 sudo
    parser: str = "line_count"         # 输出解析器
    depends_on: list[str] = []         # 依赖套件名（"level.name" 格式）
    env: dict[str, str] = {}           # 环境变量
    metrics: list[MetricDef] = []      # 性能指标定义
    thresholds: dict[str, Threshold] = {}  # 阈值
    resource_limits: ResourceLimits | None = None

@dataclass
class LevelConfig:
    suites: list[SuiteConfig]

@dataclass
class ProjectConfig:
    project: str
    description: str = ""
    settings: ProjectSettings
    levels: dict[str, LevelConfig]     # unit/functional/performance/stress

# runner.py

@dataclass
class SuiteResult:
    suite_name: str
    level: str                         # unit/functional/performance/stress
    status: str                        # PASS/FAIL/SKIP/ERROR
    duration_ms: float
    exit_code: int | None
    stdout: str
    stderr: str
    metrics: dict[str, float]          # 提取的性能指标
    resource_peak: ResourceSnapshot    # 资源峰值
    message: str

@dataclass
class ProjectResult:
    project: str
    path: str
    suites: list[SuiteResult]
    started_at: float
    finished_at: float

@dataclass
class WorkspaceResult:
    projects: list[ProjectResult]
```

### CLI → 模块映射

```
zigtester scan
  └─ scanner.discover(root_dir) → list[DiscoveredProject]
     └─ reporter.print_scan_result(projects)

zigtester run <project> --level unit
  └─ scanner.discover(root_dir) → filtered to <project>
     └─ runner.run_project(project, levels=["unit"])
        ├─ config.parse_config(yaml_path) → ProjectConfig
        ├─ resolver.resolve_order(suites) → ordered suites
        ├─ executor.execute(suite) → SuiteResult  (× N)
        └─ reporter.print_results(ProjectResult)

zigtester list <project>
  └─ scanner.discover(root_dir) → filtered
     └─ reporter.print_suite_list(suites)

zigtester init --dir <path>
  └─ config.generate_template(project_name) → writes zigtester.yaml

zigtester history <project>
  └─ history.load(project) → list[dict]
     └─ reporter.print_history(entries)
```

### 关键函数签名

```python
# scanner.py
def discover(root_dir: str, recursive: bool = True) -> list[DiscoveredProject]
def find_config(start_dir: str) -> str | None  # 向上查找 zigtester.yaml

# config.py
def parse_config(path: str) -> ProjectConfig
def validate_config(config: dict) -> list[str]  # 返回错误列表
def generate_template(project_name: str) -> str  # 返回 YAML 字符串

# runner.py
class TestExecutor:
    def execute(self, suite: SuiteConfig, work_dir: str) -> SuiteResult
    def _start_process(self, cmd: list[str], env: dict, sudo: bool) -> subprocess.Popen
    def _wait_with_timeout(self, timeout: int) -> tuple[int, str, str]
    def _kill_progressive(self) -> None  # SIGTERM → 2s → SIGKILL

class DependencyResolver:
    def resolve(self, suites: list[SuiteConfig]) -> list[SuiteConfig]
    def detect_cycle(self, suites: list[SuiteConfig]) -> list[str] | None

def run_project(project: DiscoveredProject, levels: list[str], 
                fail_fast: bool, no_build: bool) -> ProjectResult
def run_workspace(projects: list[DiscoveredProject], levels: list[str],
                  parallel: bool = False, fail_fast: bool = False,
                  no_build: bool = False) -> WorkspaceResult

# reporter.py
class Reporter:
    def print_results(self, result: ProjectResult | WorkspaceResult, 
                      format: str = "terminal") -> None
    def to_json(self, result: ProjectResult) -> str
    def to_markdown(self, result: ProjectResult) -> str
    def save_json(self, result: ProjectResult, path: str) -> None

# metrics.py
class MetricExtractor:
    def extract(self, stdout: str, definitions: list[MetricDef]) -> dict[str, float]
    def check_thresholds(self, metrics: dict, thresholds: dict) -> list[str]
    @staticmethod
    def calc_percentile(values: list[float], p: float) -> float

# monitor.py
class ResourceMonitor:
    def start(self, pid: int) -> None  # 启动后台采样线程
    def stop(self) -> ResourceSnapshot
    def check_limits(self, snapshot: ResourceSnapshot, 
                     limits: ResourceLimits) -> list[str]

# history.py
def save_run(result: ProjectResult) -> str  # 返回存储路径
def load_history(project: str, suite: str, n: int = 30) -> list[dict]
def check_regression(current: dict, history: list[dict], 
                     threshold_pct: float = 20.0) -> list[Regression]
```

### 配置 JSON Schema 核心约束

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["project", "levels"],
  "properties": {
    "project": {"type": "string", "pattern": "^[a-z][a-z0-9_-]*$"},
    "levels": {
      "type": "object",
      "properties": {
        "unit":        {"$ref": "#/$defs/level"},
        "functional":  {"$ref": "#/$defs/level"},
        "performance": {"$ref": "#/$defs/level"},
        "stress":      {"$ref": "#/$defs/level"}
      }
    }
  },
  "$defs": {
    "level": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "command"],
        "properties": {
          "name":    {"type": "string", "pattern": "^[a-z][a-z0-9_-]*$"},
          "command": {"type": "string"},
          "timeout": {"type": "integer", "default": 120},
          "sudo":    {"type": "boolean", "default": false},
          "parser":  {"type": "string", "enum": ["zig_test","line_count","custom"], "default": "line_count"}
        }
      }
    }
  }
}
```

## 6 个兄弟项目的 zigtester.yaml 规格

### zigfoundation
```yaml
project: zigfoundation
description: "基础库 — 网络/字节序/日志/缓冲等"
levels:
  unit:
    - {name: "all-tests", command: "zig build test", parser: zig_test}
```

### zigtun
```yaml
project: zigtun  
description: "TUN 设备 + lwIP 协议栈"
levels:
  unit:
    - {name: "all-tests", command: "zig build test", parser: zig_test}
```

### zigproxy
```yaml
project: zigproxy
description: "代理协议检测 + 入站连接"
levels:
  unit:
    - {name: "all-tests", command: "zig build test", parser: zig_test}
  functional:
    - {name: "doh-integration", command: "python3 tools/test_doh.py"}
```

### zigdns
```yaml
project: zigdns
description: "DNS 客户端 + 缓存 + FakeIP"
levels:
  unit:
    - {name: "all-tests", command: "zig build test", parser: zig_test}
  functional:
    - {name: "cli-tests", command: "zig-out/bin/zigdns test"}
```

### zigoutbounds
```yaml
project: zigoutbounds
description: "重量出站协议 — SS/Trojan/Hysteria2"
levels:
  unit:
    - {name: "all-tests", command: "zig build test", parser: zig_test}
  functional:
    - {name: "crypto-ss2022", command: "python3 tests/protocol-tester/test_protocols.py ss2022 --crypto-only", depends_on: ["unit.all-tests"]}
    - {name: "crypto-trojan", command: "python3 tests/protocol-tester/test_protocols.py trojan --crypto-only"}
    - {name: "crypto-hysteria2", command: "python3 tests/protocol-tester/test_protocols.py hysteria2 --crypto-only"}
  performance:
    - {name: "bench-ss2022", command: "python3 tests/protocol-tester/benchmark.py ss2022 -c 4 -n 100"}
```

### zigbox
```yaml
project: zigbox
description: "sing-box 复刻 — 编排层"
levels:
  unit:
    - {name: "all-tests", command: "zig build test", parser: zig_test}
  functional:
    - {name: "protocols", command: "python3 tests/test_protocols.py", sudo: true}
    - {name: "route-geo", command: "python3 tests/test_route_geo.py"}
  performance:
    - {name: "bench-socks5", command: "python3 tests/test_bench.py --mode socks5 -c 10 -n 100"}
    - {name: "bench-direct", command: "python3 tests/test_bench.py --mode direct -c 10 -n 100"}
```

## 内置输出解析器

| 解析器 | 适用场景 | 行为 |
|--------|---------|------|
| `zig_test` | `zig build test` | 解析 `X/Y passed; Z skipped`，提取通过/失败数 |
| `line_count` | 任意命令 | exit 0 → PASS，输出行数作为指标 |
| `test_protocols` | zigbox test_protocols.py | 解析 `总计 X \| 通过 Y \| 失败 Z` |
| `bench` | zigbox test_bench.py | 解析吞吐/延迟分位/传输速率 |
| `custom` | 用户定义 | 用 regex patterns 提取指标 |

## 设计依据 — 复用现有模式

zigtester 不是凭空设计，而是从 6 个兄弟项目的现有测试实践中提炼共性、统一标准：

| 模式 | 来源 | zigtester 如何复用 |
|------|------|-------------------|
| `TestResult` / `TestSuite` 类 | zigbox `tests/lib/report.py` | 直接复用其四状态模型 (PASS/FAIL/SKIP/ERROR) + 三路输出 |
| `ZigboxProcess` 生命周期 | zigbox `tests/lib/zigbox.py` | 抽象为 `TestExecutor`，支持任意子进程的 start/stop/超时/日志 |
| 4 层测试模型 | zigbox `tests/SKILL.md` | 标准化为 `unit/functional/performance/stress` |
| crypto-only → E2E 管道 | zigoutbounds `test_protocols.py` | 通过 `depends_on` 实现阶段间依赖 |
| `benchmark.py` 指标提取 | zigoutbounds | 抽象为 `MetricExtractor` + 正则 pattern |
| `zigbox.stat` 健康检查 | zigbox `test_all.py` | 抽象为 `ResourceMonitor`（psutil 后端） |
| `zig build test` 输出 | 全部 6 个项目 | 内置 `zig_test` 解析器 |
| Markdown 报表 | zigbox `report.py::print_markdown()` | 统一 Markdown 表格格式 |
| SKILL.md 测试文档 | zigbox `.claude/skills/tests/` | zigtester 自带轻量 `SKILL.md` |

## SKILL.md 规范

zigtester 自带轻量 SKILL.md（~50 行），内容仅包含：
- zigtester 是什么（一句话）
- MCP 工具列表和用途（指向 MCP server，不写具体用法）
- CLI 快速参考（2-3 个最常用命令）
- 配置文件名约定（`zigtester.yaml`）
- 与 zigbox tests skill 的边界

## MCP Server 实现方案

### FastMCP 骨架

```python
# src/server.py
from fastmcp import FastMCP

mcp = FastMCP("zigtester")

@mcp.tool()
def zigtester_scan(dir: str | None = None) -> dict:
    """发现所有包含 zigtester.yaml 的项目"""

@mcp.tool()
def zigtester_run(project: str, level: str = "all", suite: str | None = None) -> dict:
    """执行测试并返回结构化结果"""

@mcp.tool()
def zigtester_list(project: str) -> dict:
    """列出项目中的所有测试套件"""

@mcp.tool()
def zigtester_history(project: str, suite: str, limit: int = 10) -> dict:
    """查看性能历史 + 回归检测"""

@mcp.tool()
def zigtester_init(dir: str, project: str) -> dict:
    """生成初始 zigtester.yaml"""

def main():
    mcp.run()  # stdio transport
```

### Claude Code 配置

```json
{
  "mcpServers": {
    "zigtester": {
      "command": "python3",
      "args": ["-m", "zigtester.server"],
      "env": {"ZIGTESTER_ROOT": "/Users/dasimo/works/2025/fixnet"}
    }
  }
}
```

## 分阶段实施计划

| 阶段 | 内容 | 状态 |
|------|------|------|
| **Phase 1** | 设计文档 + JSON Schema + 接口规范 | ✅ 已完成 |
| **Phase 2** | Python 包骨架 + 全部 9 个核心模块 + zigfoundation/zigbox zigtester.yaml | ✅ 已完成 |
| **Phase 3** | 其余 4 项目 zigtester.yaml (zigtun/zigproxy/zigdns/zigoutbounds) | 🔲 待开始 |
| **Phase 4** | CI 集成 + GitHub Actions + 通知 | 🔲 待开始 |

> **实施日期**：2026-08-07
> **Phase 2 实际实现内容**（超出原计划）：
> - 全部 9 个模块一次性实现（config/scanner/metrics/monitor/runner/reporter/history/cli/server）
> - 三路输出（终端 ANSI / Markdown / JSON）完整可用
> - zig_test 解析器支持 Zig 0.14+ 输出格式（`343 passed; 1 skipped; 0 failed.`）
> - stderr/stdout 合并解析（zig build test 输出到 stderr）
> - 端到端验证通过：zigtester → zigfoundation `zig build test` → 343/344 passed
