# Findings & Decisions

## Requirements

- 用 zigtester MCP 替换兄弟项目（zigbox/zigoutbounds/zigfoundation/zigtun/zigproxy/zigdns）测试 skill 中的命令执行和结果解析内容
- 保留不可替代的领域知识，迁移到各项目 CLAUDE.md
- **zigbox 先行试点**，打磨流程后再推广；**zigoutbounds 暂缓**（重构中）

## Research Findings

### Skill 现状扫描
- zigbox 有 2 个测试相关 skill：`tests`（~8K token）和 `zigbox-outbound-dev`（~1K token）
- zigoutbounds 有 2 个测试相关 skill：`tests`（~5K token）和 `outbound-dev`（~4K token）
- zigfoundation 无测试 skill（仅 zig/ 语言 skill）
- zigtun/zigproxy/zigdns 无测试 skill
- 4 个 skill 合计约 18K token 每次加载成本

### zigtester MCP 能力矩阵
- 已实现：`zigtester_list`、`zigtester_run`、`zigtester_scan`、`zigtester_history`
- `zigtester_run` 支持 `--report-format markdown|json|terminal`
- `zigtester_list` 按 level（unit/functional/performance/stress）分组展示
- `zigtester_history` 提供历史回归检测（当前 vs 历史移动平均）— skill 没有的新能力
- `zigtester_scan` 跨项目发现 — skill 没有的新能力

### 领域知识分类
- **可自动化**（70%）：测试命令、输出格式、依赖排序、分层执行
- **不可自动化**（30%）：VM TUN 调试警告（SSH 中断风险）、TUN 透明代理原则（curl/dig 不带参数）、协议开发 6 步流程、黄金法则"Go 先通，Zig 后写"、测试归属边界判断

### zigbox 试点发现（2026-08-07）

1. **TUN 警告已在 CLAUDE.md 中** — zigbox 的 CLAUDE.md § 测试方法已经包含了 VM TUN 调试警告和透明代理原则，无需迁移，只需补充归属边界表和 MCP 引用。

2. **stub skill 仍需保留 TUN 警告摘要** — 虽然 CLAUDE.md 有完整版，但 stub skill 是测试入口，TUN 警告放在这里确保操作前一定看到（安全关键）。

3. **实际节省超预期** — zigbox 试点 token 节省 -83%（预估 -75%），因为 zigbox-outbound-dev 直接删除了（不仅是精简）。

4. **三层架构验证可行**：
   - stub skill（2.2K）— MCP 速查 + 安全警告
   - CLAUDE.md § 测试方法 — 领域知识（原则、边界、契约）
   - zigtester.yaml — 执行定义（已存在，无改动）

5. **stub skill 模板值得微调** — 试点后的 stub 结构（MCP 速查表 → 测试分层 → 安全警告 → 文档指针）清晰有效，可作为 Phase 2-4 的模板。

### 接入差距
- zigbox ✅ 试点完成
- zigtun/zigproxy/zigdns ❌ 尚无 zigtester.yaml — Phase 2 生成
- zigoutbounds ❌ 尚无 zigtester.yaml + 重构中 — Phase 4

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| 薄 skill + CLAUDE.md + zigtester.yaml 三层架构 | 分离"怎么跑"（MCP）、"为什么"（CLAUDE.md）、"跑什么"（zigtester.yaml） |
| zigbox 先行试点，zigoutbounds 暂缓 | zigoutbounds 剧烈重构中，先打磨流程再推广 |
| 先试点（Phase 1）而非全覆盖（原 Phase 1） | zigbox 已有 zigtester.yaml 可立即开始，试点经验指导后续 |
| stub skill 保留项目特有操作警告 | TUN 调试警告无法被 MCP 自动化，必须保留人类可读的操作知识 |
| zigbox-outbound-dev skill 完全删除 | 内容已被 zigbox tests skill 和 zigtester.yaml 覆盖 |
| 领域知识进 CLAUDE.md 而非新 skill | CLAUDE.md 是项目固有文件，每次会话自动加载；避免创建新的 skill 碎片 |
| 不删除现有测试脚本 | zigtester 是包装层，不是替换层 |
| stub skill 保留 TUN 警告摘要（试点验证） | 安全关键信息必须在测试入口可见，不能仅靠 CLAUDE.md |

### 试点后 stub skill 模板（已验证）

```markdown
# <项目> 测试 — zigtester MCP

> 本项目已接入 zigtester 自动测试框架。
> 领域知识见 `CLAUDE.md § 测试方法`。

## MCP 速查
| MCP 工具 | 用途 |
|----------|------|
| `zigtester_list("<project>")` | 列出所有测试套件 |
| `zigtester_run("<project>")` | 运行全部测试 |
| `zigtester_run("<project>", level="unit")` | 仅单元测试 |
| `zigtester_history("<project>", "suite")` | 历史 + 回归检测 |

## 测试分层
（项目特有分层概述）

## ⚠️ <项目特有操作警告>
（安全关键信息，不可省略）

## 相关文档
| 文档 | 内容 |
|------|------|
| `CLAUDE.md § 测试方法` | 领域知识 |
| `zigtester.yaml` | 测试套件定义 |
```

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| 尚无 | — |

## Resources

- `DESIGN.md` — zigtester MCP 优先架构设计文档
- `schemas/zigtester.schema.json` — 配置文件 JSON Schema
- `../zigfoundation/zigtester.yaml` — 单层级配置参考（unit only）
- `../zigbox/zigtester.yaml` — 三层级配置参考（unit + functional + performance）
- `../zigbox/.claude/skills/tests/SKILL.md` — **Phase 1 试点完成** — 精简为 stub（2.2K，-81%）
- `../zigbox/.claude/skills/zigbox-outbound-dev/SKILL.md` — **Phase 1 已删除**
- `../zigbox/CLAUDE.md` — **Phase 1 已更新** — § 测试方法补测试分层、归属边界、MCP 引用
- `../zigoutbounds/.claude/skills/tests/SKILL.md` — Phase 4 待替换
- `../zigoutbounds/.claude/skills/outbound-dev/SKILL.md` — Phase 4 待精简
- `../zigbox/tests/lib/report.py` — TestResult/TestSuite 参考实现
- `../zigbox/tests/SKILL.md` — 89K 权威测试文档（未改动）
- create-tester skill — zigtester.yaml 交互式生成工具

## Visual/Browser Findings

-
