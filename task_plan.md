# Task Plan: zigtester MCP 替换兄弟项目 test skills

> **创建**: 2026-08-07
> **更新**: 2026-08-08 — 插件 config 解析修复 + suite 过滤实现 + MCP Server stdio→HTTP 迁移完成
> **关联**: DESIGN.md § MCP 优先架构

## Goal

用 zigtester MCP 替换兄弟项目测试 skill 中 ~70% 的命令执行/结果解析内容，将不可替代的领域知识迁移到各项目 CLAUDE.md，实现每次加载 token 从 ~18K 降至 ~5.5K（-69%）。

## Current Phase

Phase 1 ✅ | Phase 2 ✅ | Phase 3 ✅ | Phase 4 ✅ | Phase 5 ✅ | Phase 6 ✅ | Phase 7 ✅ | Phase 8 🔄

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
  - DNS 端口 53（SO_REUSEADDR 绑定 127.0.0.1，与 mDNSResponder *:53 共存，需 sudo）
  - 非 root 自动跳过 DNS（--no-dns），仅 TCP echo；root 启用完整 TCP+DNS
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

### Phase 7: sing-box 插件 — 统一配置 + 进程管理 ✅
> **创建**: 2026-08-07 | **完成**: 2026-08-07 22:11
> **背景**: zigoutbounds 有 3 个独立 SingboxProcess 实现，大量重复代码。sing-box 内建 Clash REST API 支持配置热重载，可一次启动、配置随意切换。
> 详细调研见 `findings.md § sing-box 使用现状调研`。
> 完整实施记录见 `progress.md § Phase 7`。

#### P7.1: singbox_ctl.py 控制器 ✅
- [x] `SingboxController` 类 — 封装 subprocess 生命周期 + REST API 客户端
- [x] CLI `serve` 子命令 — 启动阻塞模式，直接用 test_server.json 启动
- [x] 跨平台 UDP 检测：macOS `lsof -iUDP`，Linux `ss -uln`
- [x] 失败诊断：超时后 drain stderr，输出最后 5 行
- [x] 配置生成辅助方法：generate_ss_config / build_client_config / build_server_config

#### P7.2: 统一配置设计 ✅
- [x] 调研 6 项目 sing-box 使用（仅 zigoutbounds 实际使用，zigbox 不涉及）
- [x] 合并 3 套配置为 `configs/test_server.json`（12 inbound + clash_api + direct outbound）
- [x] 解决唯一端口冲突：hysteria2 10443 vs 16802（双端口共存）
- [x] TLS 证书迁移：`zigoutbounds → plugins/sing-box/certs/`（插件自包含）
- [x] `configs/base.json` 修正 `experimental.clash_api` 嵌套格式
- [x] 验证：13 端口全部就绪（TCP: 2080/2081/2082/8388/9443/16800/16801, UDP: 5354/8388/10443/16802/16803/16804）

#### P7.3: 项目配置支持 ✅
- [x] `PluginRef` 数据模型（name + config dict），支持 `str` 和 `dict` 双格式
- [x] `PLUGIN_<KEY>` 环境变量注入插件子进程
- [x] runner.py 适配 PluginRef，合并默认配置和项目覆盖配置
- [x] 校验：现有 `plugins: ["local-echo"]` 字符串格式向后兼容

#### 关键发现
- **Clash API 限制**：`PUT /configs` 仅接受 Clash 格式，不支持原生格式热重载 → serve 直接启动完整配置
- **Hysteria2/TUIC = UDP**：QUIC 协议端口需 `lsof -iUDP` 检测，非 TCP

#### P7.5: zigoutbounds 测试脚本迁移（后续）
- [ ] 更新 `zigoutbounds/zigtester.yaml` 引用 sing-box 插件
- [ ] 测试脚本迁移到 `SingboxController`（渐进，不破坏现有测试）

### Phase 8: xray-core 插件 + 跨参考实现互测 🔄
> **创建**: 2026-08-10
> **背景**: sing-box 插件（Phase 7）验证了"统一测试参考实现 + 插件热插拔"模型可行。xray-core 是 sing-box 之外的另一主流参考实现，两个参考实现的协议握手细节不完全兼容（特别是 VLESS Reality）。同时启用两个参考实现，可对 zigoutbounds 客户端做**跨参考实现互测**，证明 zigoutbounds 协议实现与两个主流实现都兼容。
> **设计原则**: xray-core 插件与 sing-box 插件**完全独立**——结构同构（plugin.yaml + 控制脚本 + 配置模板）、端口 +100 错开、凭证（UUID/密码/Reality 私钥）独立。即便两个插件同时启动也不冲突。

#### P8.1: xray-core 插件本体 ✅
- [x] `plugins/xray-core/plugin.yaml` — 镜像 sing-box 插件结构（config/build/lifecycle/ports）
- [x] `plugins/xray-core/configs/test_server.json` — 8 个协议入站模板：
  - socks（2180）/ shadowsocks（8488）/ trojan（9543）
  - vmess-WS（16900）/ vless+tls（16901）/ vless+ws（16905）
  - **vless+reality（16906）/ vless+grpc（16907）** — xray 特有，预留入站（zigoutbounds 客户端未实现 Reality/grpc，本阶段不接入测试）
- [x] `plugins/xray-core/xray_ctl.py` — 镜像 singbox_ctl.py：
  - `XrayController` 类（start/stop/is_running）
  - 模板渲染（`__KEY__` 占位符 → `PLUGIN_*` env）
  - **证书路径绝对化**（避免 xray 进程 cwd 不同导致找不到证书）
  - **启动前端口冲突检测**（与框架级检测互补）
  - **轻量 readiness 服务**（`:9190` 裸 TCP）— xray 不暴露 REST API，需自建探针
  - CLI: `serve` / `render` 子命令
- [x] `plugins/xray-core/certs/` — 复用 sing-box 的 localhost 测试证书
- [x] 验证：8 个入站端口全部 LISTEN（Xray 26.3.27 实测）

#### P8.2: 跨插件端口冲突检测 ✅
- [x] `PluginConfig.ports` 字段 + `parse_plugin_config()` 解析 `ports:` 段
- [x] `check_port_conflicts()` — 检测两类冲突：
  - 跨插件重复端口（不同插件声明同一端口）
  - 系统已占用端口（残留进程）
- [x] `runner.py` — 启动插件前两遍扫描预检，冲突时**阻止所有插件启动**（避免半启动状态）
- [x] `plugins/sing-box/plugin.yaml` + `plugins/xray-core/plugin.yaml` — 显式声明 `ports:` 字段
- [x] 端口错开约定：xray-core = sing-box + 100（便于人工记忆）

#### P8.3: zigoutbounds 接入（规划中）
- [ ] `zigoutbounds/tests/lib/xray.py` — 镜像 `lib/singbox.py` 接口
- [ ] `test_protocols.py` — `SingboxProcess.start()` 加 xray 复用分支（与 singbox 并列互斥）
- [ ] `benchmark.py` — 同上
- [ ] `zigoutbounds/zigtester.yaml` — `plugins:` 加 `- xray-core`，新增 functional/performance 套件
  - functional: `xray-ss2022` / `xray-trojan` / `xray-vless-tls` / `xray-vless-ws`
  - functional: `cross-{proto}` — `test_protocols.py {proto} --cross-impl`
  - performance: `bench-xray-ss2022` / `bench-xray-trojan` / `bench-xray-vless`
- [ ] 范围决策：**跳过 VMess** / **跳过 Reality+grpc**（zigoutbounds 未实现客户端）
- [ ] 范围决策：**不加 test_all_protocols.py 的 xray 路径**（保持单插件职责清晰）

#### 关键发现
- **xray-core 无 REST API**：与 sing-box 内置 Clash API 不同，xray 进程不暴露运行时 API。需要插件自建轻量 readiness 服务（裸 TCP）供 zigtester ready_on 探针使用
- **Reality 协议不兼容**：xray Reality 用 x25519 私钥格式，sing-box Reality 私钥格式不同 → 必须各自独立凭证（不能用同一组 UUID/私钥）
- **证书路径**：xray 进程的 cwd 不一定是插件目录（macOS brew 安装在 /usr/local/bin），配置中 cert/key 必须用绝对路径
- **进程 cwd 风险**：`subprocess.Popen` 的 cwd 是 `plugin.path`（插件目录），但 xray 二进制如果是 PATH 中的，OS 解析路径后用 `/usr/local/bin` 作为 cwd。**配置路径绝对化是必须**

#### 决策记录
| Decision | Rationale |
|----------|-----------|
| xray 端口与 sing-box +100 错开 | 简单可预测，便于人工记忆；同一项目同时启用两个插件不冲突 |
| VLESS Reality 用独立私钥 | xray 与 sing-box Reality 私钥格式不兼容，无法共用 |
| 跳过 VMess + Reality/grpc 测试接入 | zigoutbounds 客户端未实现 Reality/grpc（仅 config 层定义），E2E 无意义 |
| 不在 test_all_protocols.py 加 xray 路径 | 单插件职责清晰；cross-impl 通过独立 `--cross-impl` flag 实现 |
| readiness 探针用裸 TCP 而非 HTTP | xray 不暴露 REST API，自建 HTTP 服务需要 30+ 行代码；裸 TCP accept 即可判定进程存活 |

## Key Questions

8. **新增**：插件 config 字段是静态声明还是支持 suite 级覆盖？（先做静态声明）
9. **新增**：base.json 是否需要 `cache_file` 持久化？（先不加，避免状态污染）

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
