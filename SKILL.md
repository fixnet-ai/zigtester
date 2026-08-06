# zigtester — 自动测试框架

zigtester 是 fixnet 生态的自动测试框架，通过标准化 `zigtester.yaml` 配置文件，
自动扫描并执行单元测试、功能测试、性能测试和压力测试。

## MCP 工具（Claude Code 内使用）

本框架优先通过 MCP Server 供 Claude Code 调用。配置 MCP Server 后，可直接使用：

| 工具 | 用途 |
|------|------|
| `zigtester_scan` | 发现所有包含 `zigtester.yaml` 的项目 |
| `zigtester_list` | 列出项目的测试套件 |
| `zigtester_run` | 执行测试，返回结构化摘要（**不返回原始 stdout**，大幅节省 token） |
| `zigtester_history` | 查看性能历史 + 自动回归检测 |
| `zigtester_init` | 为项目生成初始配置模板 |

### 启用 MCP Server

在 `.claude/settings.local.json` 中配置：

```json
{
  "mcpServers": {
    "zigtester": {
      "command": "python3",
      "args": ["-m", "zigtester.server"],
      "env": {
        "ZIGTESTER_ROOT": "/Users/dasimo/works/2025/fixnet"
      }
    }
  }
}
```

## CLI 快速参考（终端 / CI 使用）

```bash
# 发现项目
zigtester scan --dir ~/works/2025/fixnet

# 运行 zigfoundation 单元测试
zigtester run zigfoundation --level unit

# 运行 zigbox 所有层级
zigtester run zigbox

# 运行跨项目全部测试
zigtester run --all --level unit

# 查看历史
zigtester history zigbox all-tests

# 生成配置模板
zigtester init --dir ~/works/2025/fixnet/myproject --project myproject
```

## 配置文件名约定

每个项目根目录放置一个 `zigtester.yaml`。zigtester 自动扫描父目录或指定 `--dir` 下的所有子目录。
格式定义见 `schemas/zigtester.schema.json`。

## 与项目自有测试的边界

- zigtester **不替换**各项目的自有测试工具（`zig build test`、`test_protocols.py` 等）
- zigtester 是**元框架**：包装现有工具，提供统一配置、执行、报告和历史追踪
- 各项目测试脚本保持独立，zigtester 不修改它们
