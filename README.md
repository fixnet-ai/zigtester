# zigtester

fixnet 生态自动测试框架。**元框架**——包装各项目现有测试工具，不替换它们。

统一配置发现、子进程执行、输出解析、三路报告、历史追踪，通过 CLI（人类/CI）和 MCP Server（Claude Code）双层入口提供服务。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

## 定位

```
fixnet/
  zigfoundation/  ← 基础库
  libxev/         ← 异步 I/O 事件循环
  zigtun/         ← TUN 设备库
  zigproxy/       ← 代理协议库
  zigdns/         ← DNS 组件库
  zigoutbounds/   ← 重量出站协议
  zigbox/         ← 编排层 + 轻量出站
  zigtester/      ← 自动测试框架 (本项目)
```

已接入 6 个项目，覆盖 unit / functional / performance / stress 四层测试。

## 安装

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                     # 核心
pip install -e ".[monitor]"          # + 资源监控（psutil）
```

依赖：Python 3.10+、PyYAML、FastMCP。

## 快速开始

### 1. 生成配置

```bash
zigtester init --dir /path/to/project --project myproject
```

编辑生成的 `zigtester.yaml`，声明测试套件。

### 2. 扫描项目

```bash
zigtester scan --dir ~/works/2025/fixnet
```

### 3. 运行测试

```bash
zigtester run myproject                          # 所有层级
zigtester run myproject --level unit             # 仅单元测试
zigtester run myproject --level functional --suite e2e-ss2022  # 指定套件 + 自动依赖
zigtester run --all --level unit                 # 全部已发现项目
zigtester run --all --level unit --parallel       # 多项目并行
zigtester run myproject --report-format json --json-output report.json
zigtester run myproject --report-format markdown
zigtester run myproject --fail-fast --verbose
```

`--suite` 指定套件时，自动解析 `depends_on` 传递依赖并按拓扑序执行。

### 4. 查看历史

```bash
zigtester history myproject all-tests
```

## 测试分层

| 层级 | 用途 | 示例 |
|------|------|------|
| `unit` | 单元测试，纯代码，无网络依赖 | `zig build test` |
| `functional` | 功能测试，协议验证，集成测试 | `python3 tests/test_protocols.py` |
| `performance` | 性能测试，吞吐/延迟 + 阈值检查 | `python3 tests/test_bench.py` |
| `stress` | 压力测试，高并发 + 资源上限监控 | `python3 tests/stress.py -c 100` |

## 配置文件

```yaml
project: myproject
description: "项目描述"

settings:
  work_dir: "."
  build_command: "zig build"       # 可选：测试前自动构建
  timeout_default: 120
  env:                             # 可选：全局环境变量
    ZIG_EXE: zig

plugins:                           # 可选：测试依赖插件
  - local-echo                     # 字符串格式，使用插件默认配置
  - name: sing-box                 # 字典格式，覆盖插件默认值
    config:
      ss_port: 18388

levels:
  unit:
    - name: "all-tests"
      command: "zig build test"
      parser: zig_test
      timeout: 180

  functional:
    - name: "smoke-test"
      command: "python3 tests/test_smoke.py"
      timeout: 60

    - name: "e2e-test"
      command: "python3 tests/test_e2e.py"
      timeout: 120
      sudo: true                   # 需要 root 权限
      depends_on:                  # 依赖链：先跑 smoke-test
        - "functional.smoke-test"

  performance:
    - name: "bench"
      command: "python3 tests/test_bench.py -c 10 -n 100"
      parser: bench
      timeout: 120
      metrics:
        - name: throughput
          pattern: "吞吐: ([0-9.]+) req/s"
      thresholds:
        throughput:
          min: 100                 # 低于 100 → FAIL
```

### 内置输出解析器

| 解析器 | 适用场景 | 行为 |
|--------|---------|------|
| `zig_test` | `zig build test` | 解析 `X/Y passed; Z skipped` |
| `line_count` | 任意命令（默认） | exit 0 → PASS，输出行数作为指标 |
| `test_protocols` | zigbox test_protocols.py | 解析 `总计 X \| 通过 Y \| 失败 Z` |
| `bench` | 性能压测 | 吞吐/延迟分位/传输速率 |
| `custom` | 用户自定义 | 用 `metrics.pattern` 正则捕获 |

### suite 依赖

`depends_on` 使用 `level.name` 格式声明前序套件。zigtester 自动拓扑排序，检测循环依赖。`--suite` 过滤时递归包含所有传递依赖。

## 插件体系

测试依赖（echo server、sing-box 等）通过 `plugin.yaml` 声明生命周期，zigtester 自动发现、构建、启动、就绪检测、停止。项目在 `zigtester.yaml` 中声明即可接入。

### 加载链

```
plugin.yaml config:    →  插件默认配置（端口、地址等）
zigtester.yaml config: →  项目级覆盖（合并到默认值之上）
PLUGIN_<KEY> env vars: →  注入插件进程
```

### 已内置插件

| 插件 | 用途 | 默认端口 |
|------|------|---------|
| `local-echo` | TCP echo + DNS echo | 13333 |
| `sing-box` | 全协议代理服务器（10 协议 inbound） | 9090 (API) + 各协议端口 |

### 自定义插件

```yaml
# plugins/my-plugin/plugin.yaml
name: my-plugin
description: "描述"
config:
  port: 9000
build:
  command: "zig build"
  work_dir: "."
lifecycle:
  start:
    command: "python3 server.py --port ${PLUGIN_PORT}"
    timeout: 10
    ready_on:
      type: tcp
      port: 9000
  stop:
    timeout: 5
    kill: ["my-plugin"]
```

## MCP 优先架构

zigtester MCP Server 在服务端解析原始测试输出，只向 Claude 返回结构化摘要——token 节省可达 10-50x。

### MCP 工具

| 工具 | 参数 | 用途 |
|------|------|------|
| `zigtester_scan` | `dir?` | 发现所有含 `zigtester.yaml` 的项目 |
| `zigtester_list` | `project` | 列出项目的测试套件 |
| `zigtester_run` | `project`, `level?`, `suite?` | 执行测试，返回结构化摘要 |
| `zigtester_history` | `project`, `suite`, `limit?` | 性能历史 + 回归检测 |
| `zigtester_init` | `dir`, `project` | 生成初始配置模板 |

### 部署

MCP Server 使用 HTTP transport。端口绑定天然互斥，从物理上杜绝 stdio 模式的多实例僵尸进程问题。

```bash
# 启动（常驻后台）
ZIGTESTER_ROOT=~/works/2025/fixnet python -m zigtester.server &

# → 监听 http://127.0.0.1:9020/mcp
# → PID 文件：~/.zigtester/server.pid
```

Claude Code 配置（`~/.claude.json` 的 `mcpServers` 段）：

```json
{
  "zigtester": {
    "type": "http",
    "url": "http://127.0.0.1:9020/mcp"
  }
}
```

### 典型工作流

在 Claude Code 中直接用自然语言，MCP 自动执行：

```
"zigoutbounds 有哪些测试"
→ unit×1 + functional×8 + performance×3

"跑 zigoutbounds 的单元测试"
→ 1/1 passed，0.2s

"跑 zigoutbounds 的 functional 层的 e2e-ss2022"
→ 自动包含依赖 crypto-ss2022，2/2 passed

"跑全部项目的单元测试"
→ 6 项目并行 ~6s，全部通过

"看 zigoutbounds bench-ss2022 有没有变慢"
→ 当前 2168 req/s vs 历史 2140 req/s，+1.3%（无回归）
```

## 目录结构

```
zigtester/
├── pyproject.toml
├── schemas/
│   └── zigtester.schema.json
├── src/zigtester/
│   ├── cli.py                  # CLI 入口
│   ├── server.py               # MCP Server（HTTP transport）
│   ├── config.py               # 数据模型 + YAML 解析 + 校验
│   ├── scanner.py              # 项目发现
│   ├── runner.py               # 执行引擎（子进程 + 超时 + 依赖排序 + suite 过滤）
│   ├── reporter.py             # 三路输出（终端 ANSI / Markdown / JSON）
│   ├── metrics.py              # 指标提取 + 阈值检查
│   ├── monitor.py              # 资源监控（psutil）
│   ├── history.py              # 历史存储 + 回归检测
│   └── plugin.py               # 插件管理
├── plugins/
│   ├── local-echo/             # TCP echo server
│   │   ├── plugin.yaml
│   │   └── echo_server.py
│   └── sing-box/               # sing-box 进程管理
│       ├── plugin.yaml
│       ├── singbox_ctl.py
│       └── configs/
└── DESIGN.md                   # 完整设计文档
```

## 许可

MIT
