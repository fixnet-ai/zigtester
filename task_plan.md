# Task Plan: zigtester MCP 替换兄弟项目 test skills

> **创建**: 2026-08-07
> **更新**: 2026-08-07 — zigoutbounds 暂缓（剧烈重构中），zigbox 先行试点
> **关联**: DESIGN.md § MCP 优先架构

## Goal

用 zigtester MCP 替换兄弟项目测试 skill 中 ~70% 的命令执行/结果解析内容，将不可替代的领域知识迁移到各项目 CLAUDE.md，实现每次加载 token 从 ~18K 降至 ~5.5K（-69%）。

## Current Phase

Phase 1 ✅ | Phase 2 ✅ | Phase 3 ✅ | Phase 4 ✅ | Phase 5 ✅ | Phase 6 ✅

## Phases

### Phase 1: zigbox 试点替换 🧪 ✅
- [x] 读取 zigbox 现有 test skills（tests + zigbox-outbound-dev）
- [x] 读取 zigbox CLAUDE.md 了解现有结构
- [x] 将 VM TUN 调试警告 — 已在 CLAUDE.md 中，无需迁移
- [x] 将 TUN 透明代理原则 — 已在 CLAUDE.md 中，无需迁移
- [x] 将测试归属边界表迁移到 zigbox CLAUDE.md § 测试方法
- [x] `.claude/skills/tests/SKILL.md` → 精简为 stub（2.2K，-81%）
- [x] 删除 `.claude/skills/zigbox-outbound-dev/SKILL.md`
- [x] 试点验证：`zigtester run zigbox` 通过 ✓
- [x] 打磨：stub 模板已提炼，TUN 警告保留在 stub 中确认为正确做法
- **Status:** complete

### Phase 2: zigtester.yaml 全覆盖（先决条件）
- [x] 为 zigtun 生成 `zigtester.yaml`（用 create-tester skill + 手动微调）
- [x] 为 zigproxy 生成 `zigtester.yaml`
- [x] 为 zigdns 生成 `zigtester.yaml`
- [x] 验证：`zigtester scan` 可发现 zigfoundation/zigbox/zigtun/zigproxy/zigdns
- **Status:** complete

### Phase 3: 简单项目接入
- [x] zigfoundation CLAUDE.md 添加 `## 测试` 节 → 指向 zigtester MCP
- [x] zigtun CLAUDE.md 添加 `## 测试` 节
- [x] zigproxy CLAUDE.md 添加 `## 测试` 节
- [x] zigdns CLAUDE.md 添加 `## 测试` 节
- **Status:** complete

### Phase 4: zigoutbounds skill 替换（等重构完成后）
- [x] 等待 zigoutbounds 重构稳定
- [x] 为 zigoutbounds 生成 `zigtester.yaml`（11 套件：unit×1 + functional×7 + performance×3）
- [x] 将 KEY=VALUE 输出格式约定迁移到 zigoutbounds CLAUDE.md
- [x] 将协议开发流程（含可行性评估 5 问 + UDP 架构 + 常见陷阱）迁移到 CLAUDE.md
- [x] 将黄金法则"Go 先通，Zig 后写"保留在 CLAUDE.md（已存在）
- [x] `.claude/skills/tests/SKILL.md` → 精简为 stub（14K → 1.5K，-89%）
- [x] `.claude/skills/outbound-dev/SKILL.md` → **删除**（内容全部迁入 CLAUDE.md）
- **Status:** complete

### Phase 5: 最终验证 ✅
- [x] `zigtester scan` 发现全部 6 项目（zigfoundation/zigbox/zigtun/zigproxy/zigdns/zigoutbounds）
- [x] `zigtester run --all --level unit` 全部通过
- [x] `zigtester run zigoutbounds --level all` 全部通过（11/11，2026-08-07 修复后）
- [x] Token 加载量实际测量：tests skill 14K→1.5K（-89%），outbound-dev 删除（4K→0），合计 -93%
- **Status:** complete

### Phase 6: 测试生命周期管理 ✅
> **P6.1-P6.4 全部完成 (2026-08-07)**
> 根因分析见 `findings.md § 测试流畅性根因分析`。

#### P6.1: Suite 级 setup/teardown 生命周期钩子 ✅
- [x] `ReadyOn` / `LifecycleHook` 数据类
- [x] `SuiteConfig` 新增 `setup` / `teardown` 字段
- [x] `SuiteResult` 新增 `setup_error` / `teardown_error` 字段
- [x] `TestExecutor.execute()` 五阶段生命周期：setup → test → teardown（finally 保证）
- [x] setup/teardown 各自独立 timeout（shell 模式，支持管道/内建命令）
- [x] setup 失败（exit_code≠0 或 ready_on 超时）→ suite=ERROR，teardown 仍执行
- [x] teardown 失败 → 仅记录 teardown_error，不影响 suite 状态
- [x] 向后兼容：现有配置行为不变（全部 6 项目 unit 测试通过）

#### P6.2: 内置 readiness probe ✅
- [x] `_wait_tcp_ready(host, port, timeout, interval)` — socket.create_connection 轮询
- [x] `_wait_process_ready(name, timeout, interval)` — pgrep -f 轮询
- [x] `ready_on` 字段支持 `tcp` / `process` 两种类型
- [x] 无 ready_on 时：等待命令完成 + 检查 exit_code（一次性命令语义）

#### P6.3: 插件体系 ✅
- [x] `plugin.py` — PluginConfig/PluginBuild/PluginLifecycle 数据模型
- [x] `discover_plugins()` / `parse_plugin_config()` / `build_plugin()` / `start_plugin()` / `stop_plugin()`
- [x] `ProjectConfig.plugins` 字段 + YAML 解析
- [x] `run_project()` 集成插件生命周期（project 级，跨 suites 复用，finally 保证停止）
- [x] 插件目录约定：`zigtester/plugins/<name>/plugin.yaml`

#### P6.4: 首个插件 — local-echo 从 zigbox 迁移 ✅
- [x] ~~将 `zigbox/src/local-echo.zig` 抽离为独立 Zig 项目~~ → 改用 Python + asyncio（用户决策）
- [x] 编写 `plugin.yaml`（build: `true`，ready_on: tcp:13333）
- [x] `echo_server.py` — ~450 行 Python 异步 IO 实现，功能完整：
  - TCP 协议自动检测（SOCKS5/HTTP CONNECT/HTTP）
  - UDP DNS 代理（FakeIP 198.18.x.x + 上游转发）
  - 双栈 IPv4/IPv6 监听
  - DNS 默认端口 15353（避免 macOS mDNSResponder 冲突）
- [x] 清理 zigbox 生产代码：
  - `src/main.zig` — 删除 `--local-echo` CLI flag 和帮助文本
  - `src/engine.zig` — 删除 local-echo 初始化/启动/停止/deinit（~160 行）
  - `src/types.zig` — 删除 `local_echo` 字段
  - `src/local-echo.zig` — 删除（1285 行）
- [x] 清理 zigbox 测试脚本：
  - `tests/lib/config.py` — 新增 `ECHO_SERVER_PATH`、`ECHO_DNS_PORT`
  - `tests/lib/zigbox.py` — 新增 `ensure_echo_server()` / `is_echo_server_running()`
  - `tests/test_protocols.py` — `local_echo=True` → `ensure_echo_server()`
  - `tests/test_bench.py` — 移除 `local_echo` 参数
  - `tests/test_tun.py` — 移除 `local_echo=True`
  - `tests/test_all.py` — 移除 `--local-echo` flag
- [x] zigbox `zigtester.yaml` 引用 local-echo 插件 ✅
- [x] 验证：`zig build` ✅ | `zig build test` ✅ | 8/8 协议测试 ✅ | bench socks5 40/40 ✅ | test_all.py 3/3 ✅
- [x] 验证：`zigtester run zigbox --level functional` → 3/4 通过（tun 需 sudo 预期失败）

- **Status:** complete ✅

## Key Questions

1. ~~zigoutbounds 的 `outbound-dev` skill 中 6 步流程和黄金法则是否需要单独保留为 skill？~~ → 已迁入 CLAUDE.md
2. zigtun/zigproxy/zigdns 目前没有测试脚本，zigtester.yaml 是否只配 `zig build test` 即可？→ 当前已满足需求，未来有测试脚本再加
3. stub skill 中 TUN 操作警告的粒度 — zigbox 试点中验证，再推广
4. ~~试点后 stub skill 模板需要哪些调整？~~ → 已确定
5. **新增**：setup/teardown 是否应支持 level 级（per-level）而不仅是 suite 级？
6. **新增**：插件是独立 git 仓库还是放在 zigtester 的 `plugins/` 目录下？
7. **新增**：local-echo 从 zigbox 抽离后，是否需要同时支持作为独立二进制运行（方便手动调试）？

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| 薄 skill + CLAUDE.md + zigtester.yaml 三层架构 | 分离"怎么跑"（MCP）、"为什么"（CLAUDE.md）、"跑什么"（zigtester.yaml） |
| zigbox 先行试点，zigoutbounds 暂缓 | zigoutbounds 正剧烈重构，先打磨流程再推广 |
| 先 Phase 1（zigbox 试点）而非全覆盖 | zigbox 已有 zigtester.yaml，可直接开始；用试点经验指导后续 |
| stub skill 保留项目特有操作警告 | TUN 调试警告无法自动化，必须保留人类可读知识 |
| zigbox-outbound-dev skill 完全删除 | 内容已被 zigbox tests skill 和 zigtester.yaml 覆盖 |
| 不修改 zig/zig-async 语言技能 | 那是 Zig 语言技能，与测试框架职责正交 |
| 不删除现有测试脚本 | zigtester 是包装层，不是替换层 |
| **Phase 6 用生命周期钩子替代逐个修补** | 端口冲突、僵尸进程、超时不匹配等 5 个问题根因都是缺少 setup/teardown。逐个修补解决不了架构缺陷 |
| **UDP 就绪检测不纳入框架** | 这是测试脚本自身职责，框架不应越界 |
| **插件体系用于分离测试基础设施和生产代码** | local-echo.zig 寄生在 zigbox 生产代码中是反模式——测试沙箱应作为 zigtester 插件独立管理 |

## 评估背景

### 可替换 vs 不可替换

**MCP 可替换（"怎么跑"）：**

| Skill 内容 | MCP 工具 |
|-----------|---------|
| 测试命令速查 | `zigtester_list` |
| 运行测试 | `zigtester_run` |
| 输出格式（Markdown/JSON） | `zigtester_run --report-format` |
| 测试分层 | `zigtester_list` 按 level 分组 |
| 依赖排序 | `depends_on` + 拓扑排序 |
| 历史回归 | `zigtester_history`（新增能力） |
| 跨项目扫描 | `zigtester_scan`（新增能力） |

**不可替换（"为什么这样设计"）→ 迁移到 CLAUDE.md：**

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

### 当前接入状态

| 项目 | zigtester.yaml | test skill | 本次 |
|------|---------------|-----------|------|
| zigfoundation | ✅ | 无 | Phase 3 |
| zigtun | ❌ | 无 | Phase 2 + 3 |
| zigproxy | ❌ | 无 | Phase 2 + 3 |
| zigdns | ❌ | 无 | Phase 2 + 3 |
| zigoutbounds | ❌ | tests + outbound-dev | **暂缓**（重构中） |
| zigbox | ✅ | tests + zigbox-outbound-dev | **Phase 1 试点** |
| zigtester | — | create-tester | 不涉及 |

### Token 节省预估

| Skill | 替换前 | 替换后 | 节省 |
|-------|-------|-------|------|
| zigbox tests | ~8K | ~2K | -75% |
| zigbox-outbound-dev | ~1K | 删除 | -100% |
| zigoutbounds tests | ~5K | ~1.5K | -70% |
| zigoutbounds outbound-dev | ~4K | ~2K | -50% |
| **合计** | **~18K** | **~5.5K** | **-69%** |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| 尚无 | — | — |

## Notes

- 目标架构：`每个项目的测试设施 = 薄 skill（~30行 MCP 速查）+ CLAUDE.md（领域知识）+ zigtester.yaml（执行定义）`
- zigbox 试点是关键里程碑 — 验证整个三层架构的可行性
- 试点打磨后的经验写入 findings.md，指导 Phase 2-4
- 不做：不删除测试脚本、不修改语言技能、不强制统一测试工具
