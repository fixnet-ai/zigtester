# 任务：zigtester MCP 替换兄弟项目 test skills 评估与迁移

> **状态**: 评估完成，待执行
> **创建**: 2026-08-07
> **关联**: DESIGN.md § MCP 优先架构

## 评估结论

**可以用 zigtester MCP 替换兄弟项目测试 skill 中 ~70% 的内容（命令执行 + 结果解析），
但必须保留不可替代的领域知识（VM TUN 调试警告、协议开发流程等）迁移到各项目 CLAUDE.md。**

Token 节省：每次加载测试 skill 从 ~18K → ~5.5K（**-69%**），加 MCP 服务端输出解析额外 10-50x。

## 可替换 vs 不可替换

### MCP 可替换（"怎么跑"）

| Skill 内容 | MCP 工具 |
|-----------|---------|
| 测试命令速查 | `zigtester_list` |
| 运行测试 | `zigtester_run` |
| 输出格式（Markdown/JSON） | `zigtester_run --report-format` |
| 测试分层 | `zigtester_list` 按 level 分组 |
| 依赖排序 | `depends_on` + 拓扑排序 |
| 历史回归 | `zigtester_history`（新增能力） |
| 跨项目扫描 | `zigtester_scan`（新增能力） |

### 不可替换（"为什么这样设计"）→ 迁移到 CLAUDE.md

| 领域知识 | 迁移目标 |
|---------|---------|
| VM TUN 调试警告（SSH 中断、route_exclude_address） | zigbox CLAUDE.md § 测试方法 |
| TUN 透明代理原则（curl/dig 不带参数） | zigbox CLAUDE.md § 测试方法 |
| 协议开发 6 步流程 | zigoutbounds CLAUDE.md § 开发流程 |
| 黄金法则"Go 先通，Zig 后写" | zigoutbounds CLAUDE.md |
| 测试归属边界表 | 各项目 zigtester.yaml + CLAUDE.md |
| KEY=VALUE 输出格式约定 | zigoutbounds CLAUDE.md |

### 不替换（保留）

- 现有测试脚本（test_protocols.py、test_bench.py 等）
- create-tester skill（zigtester 自有）
- zig/zig-async-skill（语言技能）

## 目标架构

```
每个项目的测试设施 = 薄 skill（~30行 MCP 速查）+ CLAUDE.md（领域知识）+ zigtester.yaml（执行定义）
```

## 当前接入状态

| 项目 | zigtester.yaml | test skill |
|------|---------------|-----------|
| zigfoundation | ✅ | 无（仅 zig/ 语言 skill） |
| zigtun | ❌ | 无 |
| zigproxy | ❌ | 无 |
| zigdns | ❌ | 无 |
| zigoutbounds | ❌ | tests + outbound-dev（~180 行） |
| zigbox | ✅ | tests + zigbox-outbound-dev（~180 行） |
| zigtester | — | create-tester |

## 迁移步骤

### Phase 1: zigtester.yaml 全覆盖（先决条件） 🔲

为 zigtun/zigproxy/zigdns/zigoutbounds 生成配置文件。用 create-tester skill 生成后手动微调。

### Phase 2: zigbox skill 替换 🔲

- 将 VM TUN 调试警告 + 归属边界迁移到 zigbox CLAUDE.md
- `.claude/skills/tests/SKILL.md` → stub（MCP 速查 + TUN 警告保留）
- 删除 `.claude/skills/zigbox-outbound-dev/SKILL.md`

### Phase 3: zigoutbounds skill 替换 🔲

- 将 KEY=VALUE 格式约定迁移到 zigoutbounds CLAUDE.md
- `.claude/skills/tests/SKILL.md` → stub
- `.claude/skills/outbound-dev/SKILL.md` → 保留 6 步流程 + 黄金法则

### Phase 4: 简单项目接入 🔲

zigfoundation/zigtun/zigproxy/zigdns 的 CLAUDE.md 添加 `## 测试` 指向 MCP。

### Phase 5: 验证 🔲

`zigtester scan` 发现全部项目 + `zigtester run --all --level unit` 通过。

## 新 stub skill 模板

```markdown
# tests — 通过 zigtester MCP 运行测试

本项目已接入 zigtester。测试通过 MCP 工具运行：

| MCP 工具 | 用途 |
|----------|------|
| `zigtester_list("<project>")` | 列出所有测试套件 |
| `zigtester_run("<project>")` | 运行全部测试 |
| `zigtester_run("<project>", level="unit")` | 仅单元测试 |
| `zigtester_history("<project>", "<suite>")` | 查看历史 + 回归检测 |

## <项目特有操作警告>

## 相关文档
- CLAUDE.md § 测试方法
- zigtester.yaml
```

## Token 节省

| Skill | 替换前 | 替换后 | 节省 |
|-------|-------|-------|------|
| zigbox tests | ~8K | ~2K | -75% |
| zigoutbounds tests | ~5K | ~1.5K | -70% |
| zigoutbounds outbound-dev | ~4K | ~2K | -50% |
| zigbox-outbound-dev | ~1K | 删除 | -100% |
| **合计** | **~18K** | **~5.5K** | **-69%** |

## 不做

- ❌ 不删除测试脚本
- ❌ 不修改 zig/zig-async 语言技能
- ❌ 不强制统一测试工具
