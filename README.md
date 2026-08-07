# zigtester

fixnet 生态自动测试框架 — 元框架，包装各项目现有测试工具，提供统一配置、执行、报告和历史追踪。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

## 项目依赖关系

```
fixnet/
  zigfoundation/  ← 基础库
  libxev/         ← 异步 I/O 事件循环
  zigtun/         ← TUN 设备库
  zigproxy/       ← 代理协议库
  zigdns/         ← DNS 组件库
  zigoutbounds/   ← 重量出站协议
  zigbox/         ← 编排层 + 轻量出站
  zigtester/      ← 自动测试框架 (本项目) — 包装以上所有项目的测试
```

详细架构与开发规范见 [CLAUDE.md](./CLAUDE.md)，完整设计见 [DESIGN.md](./DESIGN.md)。

## 安装

```bash
pip install -e .            # 开发模式
pip install -e ".[monitor]" # 含资源监控（psutil）
```

依赖：Python 3.10+、PyYAML、FastMCP。

## 快速开始

### 1. 生成配置模板

```bash
zigtester init --dir /path/to/your/project --project myproject
```

这会生成一个 `zigtester.yaml`，编辑它声明你的测试层级和套件。

### 2. 发现项目

```bash
zigtester scan --dir ~/works/2025/fixnet
```

### 3. 运行测试

```bash
# 运行所有层级
zigtester run myproject

# 仅单元测试
zigtester run myproject --level unit

# 跨所有已发现项目
zigtester run --all --level unit

# JSON 输出（CI 友好）
zigtester run myproject --report-format json --json-output /tmp/report.json

# Markdown 报表
zigtester run myproject --report-format markdown
```

### 4. 查看历史

```bash
zigtester history myproject all-tests
```

## 架构

```
zigtester/
├── pyproject.toml              # Python 包 + CLI/MCP 入口点
├── schemas/
│   └── zigtester.schema.json   # 配置 JSON Schema
└── src/
    ├── cli.py                  # CLI 入口 (scan/list/run/history/init)
    ├── server.py               # MCP Server（FastMCP，5 个工具）
    ├── config.py               # 数据模型 + YAML 解析 + 校验 + 模板生成
    ├── scanner.py              # 项目发现（递归扫描 zigtester.yaml）
    ├── runner.py               # 执行引擎（子进程 + 超时 + 依赖排序）
    ├── reporter.py             # 三路输出（终端 ANSI / Markdown / JSON）
    ├── metrics.py              # 性能指标提取（5 种内置解析器 + 阈值 + 分位数）
    ├── monitor.py              # 资源监控（psutil 后台线程采样）
    ├── history.py              # 历史存储 + 回归检测（移动平均对比）
    └── plugin.py               # 插件管理（发现/构建/启停测试依赖插件）
plugins/
    ├── local-echo/             # TCP echo + UDP DNS 代理（替代 zigbox --local-echo）
    │   ├── plugin.yaml
    │   └── echo_server.py
    └── sing-box/               # sing-box 统一进程管理 + 10 协议 inbound 双栈配置
        ├── plugin.yaml
        ├── singbox_ctl.py
        ├── configs/
        │   ├── test_server.json
        │   └── base.json
        └── certs/               # TLS 自签名证书
```

## 测试分层

```
Layer 0: unit         — 单元测试（zig build test，纯代码，无网络依赖）
Layer 1: functional   — 功能测试（协议验证、集成测试）
Layer 2: performance  — 性能测试（吞吐/延迟/传输速率 + 阈值检查）
Layer 3: stress       — 压力测试（高并发 + 资源上限监控）
```

## 配置文件格式

```yaml
project: myproject
description: "项目描述"

settings:
  work_dir: "."
  build_command: "zig build"
  timeout_default: 120

plugins:
  - local-echo                # 声明测试依赖插件（zigtester 自动管理生命周期）

levels:
  unit:
    - name: "all-tests"
      command: "zig build test"
      parser: zig_test
      timeout: 120
  functional:
    - name: "smoke-test"
      command: "python3 tests/test_smoke.py"
  performance:
    - name: "bench"
      command: "python3 tests/test_bench.py -c 10 -n 100"
      metrics:
        - name: throughput
          pattern: "吞吐: ([0-9.]+) req/s"
      thresholds:
        throughput:
          min: 100
```

完整规范见 `schemas/zigtester.schema.json`。已接入项目的示例配置：

- `../zigfoundation/zigtester.yaml` — 单层级（unit）
- `../zigbox/zigtester.yaml` — 三层级（unit + functional + performance）+ `plugins: [local-echo]`
- `../zigoutbounds/zigtester.yaml` — 四层级 + `plugins: [local-echo, sing-box]`

## MCP 优先架构

### 为什么 MCP

zigtester 是 MCP 的理想场景 — 大量原始测试输出在服务端解析为结构化摘要，只将关键结果传给 Claude，token 节省可达 10-50x。

| MCP 工具 | 用途 |
|----------|------|
| `zigtester_scan` | 发现所有包含 `zigtester.yaml` 的项目 |
| `zigtester_list` | 列出项目的测试套件 |
| `zigtester_run` | 执行测试，返回结构化摘要 |
| `zigtester_history` | 查看性能历史 + 回归检测 |
| `zigtester_init` | 生成初始配置模板 |

### 配置 MCP Server

```json
{
  "mcpServers": {
    "zigtester": {
      "command": "python3",
      "args": ["-m", "zigtester.server"],
      "env": {"ZIGTESTER_ROOT": "/path/to/workspace"}
    }
  }
}
```

配置后 Claude Code 可直接调用上述工具。MCP Server 在服务端解析原始测试输出，只返回结构化摘要，大幅节省 token。

### 日常使用

在 Claude Code 中直接用自然语言，MCP 自动执行。以 zigoutbounds 开发为例：

| 场景 | 说什么 |
|------|--------|
| 改代码前验证基线 | "跑 xxx 的单元测试" |
| 改代码后快速反馈 | "跑 xxx 的单元测试" |
| 怀疑性能退化 | "看 xxx 的 bench 有没有变慢" |
| 改了基础库（zigfoundation） | "跑全部项目的单元测试" |
| 不知道有什么测试 | "xxx 有哪些测试" |

**不需要记命令，不需要开终端，不需要切目录。**

典型工作流：

```
# 开发前看有什么测试
"zigoutbounds 有哪些测试"
→ 列出 11 套件：unit×1 + functional×7 + performance×3

# 改代码后验证
"跑 zigoutbounds 的单元测试"
→ 1/1 passed，0.2s（自动记录历史）

# 怀疑改坏了
"跑 zigoutbounds 的 functional"
→ crypto-only 全过，E2E 由 zigtester sing-box 插件自动管理（插件未安装时明确提示）

# 改了基础库，全部验证
"跑全部项目的单元测试"
→ 6 项目并行 ~6s，全部通过

# 检查是否变慢
"看 zigoutbounds bench-ss2022 有没有变慢"
→ 当前 520 req/s vs 历史 535 req/s，-2.8%（无回归）
```

### CLI 辅助

MCP Server 供 Claude Code 调用，CLI 供人类终端和 CI/CD 使用，两者共享同一套核心模块：

```bash
# 终端彩色输出（人类阅读）
zigtester run zigbox --level unit

# Markdown 表格（Agent 解析）
zigtester run zigbox --report-format markdown

# JSON 导出（CI 对接）
zigtester run zigbox --report-format json --json-output report.json
```

## 许可

MIT
