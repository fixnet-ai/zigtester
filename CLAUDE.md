# CLAUDE.md

> 通用规则见用户级 `~/.claude/CLAUDE.md`；本文件仅含 zigtester 项目特有信息。

## 项目概述

**zigtester** 是 fixnet 生态的自动测试框架，通过标准化 `zigtester.yaml` 配置文件自动扫描并执行单元测试、功能测试和性能测试（压力测试并入性能测试，作为其长时持续 + 资源趋势形态）。

### 定位

```
fixnet/
  zigfoundation/  ← 基础库
  libxev/         ← 异步 I/O 事件循环
  zigtun/         ← TUN 设备库
  zigproxy/       ← 代理协议库
  zigdns/         ← DNS 组件库
  zigoutbounds/   ← 重量出站协议
  zigbox/         ← 编排层 + 轻量出站
  zigtester/      ← 自动测试框架 (本项目) — 元框架，包装以上所有项目的测试
```

### 核心职责

1. **配置发现** — 扫描目录树，自动发现所有包含 `zigtester.yaml` 的项目
2. **测试执行** — 包装现有测试工具（`zig build test`、Python 测试脚本），提供统一的子进程管理、超时控制、资源监控
3. **输出解析** — 内置 5 种解析器（`zig_test`/`line_count`/`test_protocols`/`bench`/`custom`），从原始输出提取结构化指标
4. **三路报告** — 终端 ANSI 彩色 / Markdown 表格 / JSON，覆盖人类阅读和 CI 对接
5. **历史追踪** — 自动保存每次运行结果，支持性能回归检测（当前 vs 历史移动平均）
6. **双层入口** — MCP Server（Claude Code 调用，服务端解析，节省 token）+ CLI（人类终端/CI）
7. **插件管理** — 自动发现、构建、启动/停止测试依赖插件（echo server、sing-box 等），通过 `plugin.yaml` 声明生命周期
8. **环境自检自愈** — 每个测试套件执行前 pre-flight 自检插件环境（进程存活 + readiness 端口 + **端口归属进程树校验**，lsof + ps 实现）；被外部破坏时自动清理恢复（仅清理可识别的插件残留，未知进程不误杀）；恢复失败 fast fail 并输出「测试环境规范」指引 AI agent 经 zigtester 运行。见 `plugin.py` `PluginManager` / `verify_plugin` / `env_spec_message`，单测 `tests/test_env_guard.py`

### 测试环境治理铁律（生态级，用户裁定）

**所有 zig* 项目的测试（调试、验证、回归）必须经 zigtester 执行**：

- 兄弟项目测试脚本只探测依赖服务（detect-and-error），绝不自启 echo/sing-box/xray
- 任何会话禁止手动启停插件进程（`sing-box run` / `xray run` / local-echo 二进制 / `pkill` 插件名）
- 环境异常时唯一正确动作 = 重新 `zigtester run`（自检自愈机制会恢复环境）
- ⚠️ 访问 127.0.0.1 的 HTTP 客户端必须禁用代理（`requests` 用 `trust_env=False`，`urllib` 用 `ProxyHandler({})`）— 本机 HTTP_PROXY 会劫持 localhost 请求导致假判"插件未运行"

### 设计原则

1. **元框架** — 包装现有测试工具，不替换它们
2. **约定优于配置** — 合理默认值，需要时覆盖
3. **声明式配置** — 每个项目只需放一个 `zigtester.yaml`
4. **渐进接入** — 项目按需添加配置文件即可接入，不强制改造现有测试
5. **MCP 优先** — Claude Code 通过 MCP 工具调用，服务端处理原始数据，只返回结构化摘要

## 全异步 IO 例外

**本项目是 fixnet 生态中唯一允许使用同步 IO 的项目。** 作为 Python 测试框架，zigtester 不参与数据路径，仅通过子进程管理测试生命周期。

| ✅ 本项目的做法 | 原因 |
|---------------|------|
| `subprocess.Popen` + `communicate()` | 同步等待测试进程完成 — 测试框架无需异步 |
| 同步文件读写 (`open`/`json.dump`) | 报告和历史存储 — 非数据路径 |
| Python 标准库同步 HTTP | 未来可能的 CI 通知 — 非数据路径 |

**zigtester 验证的 Zig 项目必须遵循全异步 IO 铁律。**

## 编码规则

### Python 3.10+

- 使用 `dataclass` 定义数据模型（配置、结果、指标）
- 类型注解使用 `from __future__ import annotations` 延迟求值
- 所有模块可独立导入，无循环依赖

### 模块依赖层次

```
config.py          ← 零依赖（仅标准库 + PyYAML）
scanner.py         ← config
metrics.py         ← config
monitor.py         ← config
plugin.py          ← config（插件发现、构建、启停）
runner.py          ← config + metrics + monitor + plugin
reporter.py        ← config + scanner
history.py         ← config
cli.py             ← 全部核心模块（用户入口）
server.py          ← 全部核心模块 + FastMCP（MCP 入口）
```

### 接口契约

- 核心模块（config/scanner/metrics/monitor/runner/reporter/history）**不依赖** CLI 或 MCP Server
- CLI 和 MCP Server 共享同一套核心模块，只是入口不同
- CLI → 人类可读的彩色终端输出
- MCP → 结构化 JSON，精简到只含 Claude 需要的信息（不含 stdout/stderr 原文）

### 测试脚本可用性原则（适用于 zigtester 自身）

从用户级 CLAUDE.md § 测试脚本可用性继承，适用于本项目自身的测试脚本：

| # | 原则 |
|---|------|
| 1 | **可独立运行** — 核心模块的单元测试不依赖外部服务或配置文件 |
| 2 | **失败信息可读** — 断言失败时输出期望值和实际值 |
| 3 | **残留清理** — 测试不留下临时文件或进程 |

## 构建与测试命令

```bash
# 安装（开发模式 — 在项目 .venv 中）
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e ".[monitor]"    # 含资源监控（psutil）

# CLI 使用
zigtester scan --dir ~/works/2025/fixnet
zigtester run zigfoundation --level unit
zigtester run --all --level unit --report-format markdown
zigtester run --all --level unit --parallel    # 多项目并行（各项目互不干扰）
zigtester history zigfoundation all-tests

# 或通过模块入口
python -m zigtester scan --dir ~/works/2025/fixnet
python -m zigtester run zigfoundation --level unit

# MCP Server 启动（HTTP transport，常驻后台，端口绑定避免多实例）
ZIGTESTER_ROOT=~/works/2025/fixnet python -m zigtester.server &
# 启动后监听 http://127.0.0.1:9020/mcp，PID 文件 ~/.zigtester/server.pid
```

## 组件标识

| 标识 | 模块 |
|------|------|
| `[scan]` | scanner.py — 项目发现 |
| `[cfg]` | config.py — 配置解析 |
| `[run]` | runner.py — 执行引擎 |
| `[rpt]` | reporter.py — 输出格式化 |
| `[mtr]` | metrics.py — 指标提取 |
| `[mon]` | monitor.py — 资源监控 |
| `[hist]` | history.py — 历史追踪 |
| `[plg]` | plugin.py — 插件管理 |

## 内置输出解析器

| 解析器 | 适用场景 | 行为 |
|--------|---------|------|
| `zig_test` | `zig build test` | 解析 `X/Y passed; Z skipped` 及 `X passed; Y skipped; Z failed.` |
| `line_count` | 任意命令 | exit 0 → PASS，输出行数作为指标 |
| `test_protocols` | zigbox test_protocols.py | 解析 `总计 X \| 通过 Y \| 失败 Z` |
| `bench` | zigbox test_bench.py | 解析吞吐/延迟分位/传输速率 |
| `custom` | 用户定义 | 用 regex patterns 提取指标 |

## 配置文件格式

完整 JSON Schema 见 `schemas/zigtester.schema.json`。示例配置见各兄弟项目的 `zigtester.yaml`：

- `../zigfoundation/zigtester.yaml` — 单层级（unit）
- `../zigbox/zigtester.yaml` — 三层级（unit + functional + performance）+ `plugins: [local-echo]`
- `../zigoutbounds/zigtester.yaml` — 三层级（unit + functional + performance）+ `plugins: [local-echo, sing-box]`（长时套件 `bench-long-*` 位于 performance 层，经 `per_suite_only` 隔离）

### plugins 字段

项目可在 `zigtester.yaml` 中声明所需的测试依赖插件，zigtester 在测试前自动启动、测试后自动停止：

```yaml
plugins:
  - local-echo                # 字符串格式（使用默认配置）
  - name: sing-box            # 字典格式（可覆盖插件默认配置）
    config:
      ss_port: 18388
```

插件配置通过 `PLUGIN_<KEY>` 环境变量注入进程。插件定义位于 `zigtester/plugins/<name>/plugin.yaml`。

## 可插拔设计

### 输出解析器扩展

通过 `parser` 字段和 `metrics` 正则模式扩展，无需修改 zigtester 源码：

```yaml
levels:
  performance:
    - name: "my-benchmark"
      command: "./run_benchmark.sh"
      parser: custom                    # 自定义解析器
      metrics:
        - name: my_metric
          pattern: "MY_SCORE: ([0-9.]+)"  # 正则捕获组
      thresholds:
        my_metric:
          min: 1000
```

### 测试依赖插件

通过 `plugins/<name>/plugin.yaml` 定义可复用的测试依赖（echo server、sing-box 等），含构建命令、启动/停止生命周期、就绪检测：

```yaml
# plugins/local-echo/plugin.yaml（统一 Go 程序）
name: local-echo
build:
  command: "go build -o local-echo ."
lifecycle:
  start:
    command: "exec ./local-echo --tcp-port 13333 --dns-port 5533 --h2-port 13335
              --h3-port 13336 --http-port 18080 --tls-port 18443
              --real-dns-port 15353 --bench-port 13337 --stream-port 13338
              --cert certs/localhost.crt --key certs/localhost.key"
    timeout: 10
    ready_on:
      type: tcp
      port: 13333
  stop:
    timeout: 5
    kill: ["local-echo"]
```

local-echo 端口契约（各项目测试脚本共享，禁止项目脚本自行启停 echo server）：

| 端口 | 协议 | 用途 |
|------|------|------|
| 13333 | TCP+UDP | 协议自适应 echo（SOCKS5/CONNECT/HTTP）+ UDP raw echo |
| 5533 | UDP | DNS echo（FakeIP 模式） |
| 15353 | UDP | DNS echo（real-map 模式，zigbox 矩阵） |
| 13335 / 13336 | TCP / UDP | H2 / H3 echo（请求体回显） |
| 18080 / 18443 | TCP | 额外 HTTP / TLS 回显（zigbox 矩阵 curl 目标） |
| 13337 | TCP | bench 纯字节 echo（短连接压测，10ms idle 主动关） |
| 13338 | TCP | stream 纯字节 echo（长连接压测，无 idle——N:1 轮转空隙会误杀长连接） |

## 设计文档

完整设计见 `DESIGN.md`（架构决策、数据模型、接口规范、设计依据、实施计划）。

## 参考代码

- `../zigbox/tests/lib/report.py` — TestResult/TestSuite 四状态模型（PASS/FAIL/SKIP/ERROR）+ 三路输出（复用模式）
- `../zigbox/tests/lib/zigbox.py` — 子进程生命周期管理（抽象为 TestExecutor）
- `../zigbox/tests/SKILL.md` — 3 层测试模型（unit/functional/performance）
- `../zigoutbounds/tests/protocol-tester/` — 依赖管道（crypto-only → E2E）+ benchmark 编排
