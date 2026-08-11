# zigtester

**一个配置，覆盖所有测试。实测将 AI Agent 测试环节的 Token 消耗降低 60%～85%，工具使用能力和效率大幅提升。** zigtester 是 fixnet 生态的统一测试框架——为 6+ 个独立项目提供一致的测试体验。既是命令行工具，也是 AI 智能体的测试执行引擎。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

## 为什么需要 zigtester

fixnet 生态由多个独立项目组成，每个项目有自己的测试工具、输出格式和运行方式。切换项目时，你需要记住不同的命令、理解不同的输出、手动对比历史数据——这不是测试该有的样子。

- **一处配置，全部统一** — 每个项目放一个 `zigtester.yaml`，框架自动发现、执行、报告
- **无需改造现有测试** — 包装已有工具（`zig build test`、Python 脚本），不替换它们
- **默认资源监控** — 每次测试自动追踪 CPU、内存、文件描述符，无需各项目单独实现
- **性能不会悄悄退化** — 每次运行自动对比历史基线，发现退化立即标记
- **AI Agent 原生驱动** — MCP Server 让 Claude Code 直接执行测试、分析结果、判断回归

## AI Agent 驱动的日常测试

zigtester 最独特的价值在于：**你不再需要亲自跑测试、读输出、对比历史**。把测试的执行和初步分析交给 AI Agent，你只关注它汇报的结论。

让 AI 直接阅读测试原始输出，不仅消耗大量 token，还容易误读格式、遗漏关键信息。zigtester 在服务端完成解析，只向 AI 返回结构化摘要——`zig build test` 的 500 行输出被精炼为 `{passed: 88, failed: 0, skipped: 1}`，压测 100 轮原始数据被提炼为分位数汇总。AI 不再花 token 在"读懂输出"上，而是直接基于结构化结论做判断、给建议。

### 改完代码，随口验证

```
你：我刚改了 zigoutbounds 的 SS2022 加密，帮我跑一下相关测试

Claude Code 调用 MCP：
  → zigtester_list zigoutbounds          # 看看有哪些测试
  → zigtester_run zigoutbounds --suite e2e-ss2022  # 自动包含依赖 crypto-ss2022
  → 返回：2/2 passed，0.8s，内存峰值 34MB

你看到的结果：
  "SS2022 加密相关测试全部通过，没有回归。"
```

### 提交前检查全线健康

```
你：准备发 PR 了，帮我跑一下所有项目的单元测试，看看有没有被我改坏的

Claude Code 调用 MCP：
  → zigtester_run --all --level unit
  → 6 个项目并行执行，5 秒完成
  → 返回结构化摘要（而非每家上百行 zig build test 输出）

你看到的结果：
  "6/6 项目全部通过：zigfoundation(88 pass), zigtun(3 pass),
   zigproxy(5 pass), zigdns(12 pass), zigoutbounds(15 pass), zigbox(23 pass)"
```

### 怀疑性能退化，让 AI 查历史

```
你：这次改动之后 zigbox 的吞吐有没有下降？

Claude Code 调用 MCP：
  → zigtester_history zigbox bench-throughput
  → 加载最近 10 次历史，自动对比基线

你看到的结果：
  "bench-throughput：当前 2180 req/s，历史基线 2160 req/s，变化 +0.9%，无退化。
   资源方面：内存 45MB（基线 42MB，+7%），FD 18（基线 16，+12%），未触发回归告警。"
```

### CI 挂了，让 AI 定位

```
你：GitHub Actions 上 zigoutbounds functional 红了，帮我查一下

Claude Code 调用 MCP：
  → zigtester_run zigoutbounds --level functional
  → 发现 e2e-vless-udp 失败，exit code 1
  → 对比历史：这个 suite 昨天还是 PASS，资源用量也没有异常

你看到的结果：
  "e2e-vless-udp 失败了，这是最近 10 次里首次失败。
   上次通过是昨天 18:30，建议检查今天改动的 VLESS UDP wire format 相关代码。"
```

### 项目搬了目录，历史不丢

```
你：我把 zigoutbounds 从 ~/old-path/ 移到了 ~/new-path/，历史还在吗？

Claude Code 调用 MCP：
  → zigtester run 自动检测到 UUID 匹配，历史记录无缝衔接

你看到的结果：
  "在。zigtester 用 UUID 而非目录路径标识项目，移动不影响历史。"
```

## 核心能力

### 四层测试模型

| 层级 | 关注点 | 典型场景 |
|------|--------|---------|
| **unit** | 纯代码正确性 | `zig build test`，无网络依赖 |
| **functional** | 协议与集成 | 多协议互通验证、端到端功能 |
| **performance** | 吞吐与延迟 | 压测基准、阈值检查、回归检测 |
| **stress** | 高负载稳定性 | 大并发、资源上限监控、长时间运行 |

### 默认资源监控，零配置

每次测试自动采集进程的 CPU、内存、文件描述符，直接显示在报告中。无需各项目在测试脚本里埋监控代码。资源指标的回归检测同样自动执行：内存涨了 30%、FD 泄露涨了 50%，历史对比时自动标记。

### 插件即测试依赖

echo server、sing-box、xray-core——这些测试依赖通过 `plugin.yaml` 声明生命周期。项目只需在配置中声明 `plugins:`，zigtester 自动完成构建、启动、就绪检测和停止清理，端口冲突自动检测。

## 快速开始

```bash
# 安装
git clone https://github.com/fixnet-ai/zigtester
cd zigtester
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 为项目生成配置
zigtester init --dir ../myproject --project myproject

# 编辑生成的 zigtester.yaml，然后运行
zigtester run myproject
```

zigtester 也提供 CLI 模式，适合传统终端使用和 CI 流水线：

```bash
zigtester scan                          # 发现了哪些项目
zigtester run zigoutbounds              # 跑所有层级
zigtester run --all --level unit        # 全部项目的单元测试
zigtester history zigoutbounds bench    # 查看性能历史
```

配置文件示例（完整说明见 [DESIGN.md](./DESIGN.md)）：

```yaml
project: myproject
description: "一句话描述"

settings:
  build_command: "zig build"
  timeout_default: 120

plugins:
  - local-echo

levels:
  unit:
    - name: "all-tests"
      command: "zig build test"
      parser: zig_test
  performance:
    - name: "bench"
      command: "python3 tests/bench.py"
      parser: bench
      metrics:
        - name: throughput
          pattern: "吞吐: ([0-9.]+) req/s"
      thresholds:
        throughput:
          min: 100
```

## MCP Server 部署

```bash
# 启动（常驻后台，端口绑定自动互斥，无多实例问题）
ZIGTESTER_ROOT=~/works/2025/fixnet python -m zigtester.server &

# Claude Code 配置（~/.claude.json 的 mcpServers 段）：
{
  "zigtester": {
    "type": "http",
    "url": "http://127.0.0.1:9020/mcp"
  }
}
```

MCP Server 提供 5 个工具：`zigtester_scan`、`zigtester_list`、`zigtester_run`、`zigtester_history`、`zigtester_init`。Claude Code 自动按需调用，你只需用自然语言描述意图。

### 为什么 MCP 而非让 AI 直接读测试输出

| 直接读原始输出 | 通过 zigtester MCP |
|-------------|-------------------|
| `zig build test` 500 行全进 context | → `{passed: 88, failed: 0, skipped: 1}` |
| 压测 100 轮原始数据全进 | → 服务端算好分位数，只返回 summary |
| 跨 6 个项目需逐个 `ls`/`read` | → 一次 `zigtester_scan` 返回结构化列表 |
| 历史 30 条全量文本 | → 只回趋势 + 异常标记 |
| AI 容易误读原始输出格式 | → 结构化数据，准确率 100% |

## 输出格式

同一个结果，三种输出，覆盖不同场景：

| 格式 | 谁在用 | 特点 |
|------|-------|------|
| **终端** | 人类日常开发 | ANSI 彩色、清晰分层、资源摘要 |
| **Markdown** | AI Agent（MCP 返回） | 紧凑表格、无冗余 |
| **JSON** | CI 流水线、程序消费 | 完整结构化数据 |

## 技术概要

- **Python 3.10+**，依赖 PyYAML + FastMCP
- **SQLite** 单文件历史存储（WAL 模式），自动从旧版 JSON 迁移
- **HTTP transport** MCP Server，端口绑定自然互斥
- **UUID 项目标识**，目录移动不丢历史
- 子进程隔离执行，超时控制 + 信号优雅终止 + 残留清理
- 内置 5 种输出解析器，支持自定义正则扩展

## 许可

MIT
