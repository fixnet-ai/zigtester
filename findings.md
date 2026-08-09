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

### 测试流畅性根因分析（2026-08-07）

zigoutbounds 全量测试（11 套件）中暴露了 5 个问题，根因不在被测代码，而在于 **zigtester 缺少测试环境生命周期管理**——没有 setup/teardown 钩子，没有 pre-flight 验证，没有 guaranteed cleanup。

| # | 症状 | 表面修复 | 根因 |
|---|------|---------|------|
| 1 | 端口冲突 | 加端口检查 | 没有 pre-flight 验证 |
| 2 | 僵尸进程 | 加清理脚本 | 没有 guaranteed teardown |
| 3 | 超时不匹配 | 加参数覆盖 | setup/teardown 和 test 共用一个 timeout |
| 4 | ~~UDP 就绪检测~~ | ~~写文档~~ | 不应由框架解决（测试脚本自身职责） |
| 5 | 清理逻辑激进 | 写准则 | 没有结构化的 cleanup contract |

**结论**：5 个问题指向同一个架构缺陷。真正的解决方案不是逐个修补，而是为 zigtester 增加 **生命周期钩子（setup/teardown）+ 插件体系**。

### 测试基础设施寄生问题（2026-08-07）

zigbox 的 `local-echo.zig`（1285 行）是一个 TCP echo + DNS echo 服务器，作为 zigbox 代理链测试的沙箱目标。它目前嵌在 zigbox 生产代码中，通过 `--local-echo` CLI flag 激活。

这违反了关注点分离：测试基础设施不应寄生在生产代码中。local-echo.zig 的启动/停止/就绪检测完全符合 zigtester 插件的生命周期模型——它应该作为 zigtester 插件，由框架在测试前构建、启动，在测试后停止。

**解决方案**：用 Python asyncio 重写为 `zigtester/plugins/local-echo/echo_server.py`（~450行），功能完整，零外部依赖。

**关键设计决策**：
- **Python 而非 Zig**：用户决策。跨平台更简单（无需编译），asyncio 高性能对性能测试结果影响最小
- **DNS 端口 53 + SO_REUSEADDR**：绑定 `127.0.0.1:53` 而非 `0.0.0.0`，用 `SO_REUSEADDR` 与 macOS mDNSResponder（`*:53`）共存。sudo 必需
- **非 root 自适应**：`ensure_echo_server()` 检测 euid，非 root 自动传 `--no-dns`（NOTUN 测试不需要 DNS）
- **Python 3.14 asyncio 兼容**：`asyncio.start_server` 强制 Stream API → 用 `loop.create_server()` for Protocol API；双栈需分别绑定 `0.0.0.0` 和 `::`

**变更规模**：
| 仓库 | 新增 | 删除 | 净变化 |
|------|------|------|--------|
| zigbox | 96 行 | 1,483 行 | -1,387 行 (-93%) |
| zigtester | 1,456 行 | 107 行 | +1,349 行 |

**DNS 端口演变**：
1. 初始：15353（避免 mDNSResponder 冲突）→ 用户指出应直接用 53
2. 最终：53 + SO_REUSEADDR + 绑定 127.0.0.1（标准端口，无冲突）

### macOS DNS 端口绑定发现（2026-08-07）

macOS mDNSResponder 绑定 `*:53`（UDP + TCP，IPv4/IPv6）。即使 `sudo`，直接 `bind('127.0.0.1', 53)` 也会报 `EADDRINUSE`。

**解决方案**：`SO_REUSEADDR` socket 选项。设置后即使 `*:53` 已被占用，仍可成功绑定 `127.0.0.1:53`。

```python
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(('127.0.0.1', 53))
```

### zigoutbounds 测试脚本优化总结（2026-08-07）

已在上游测试脚本中修复的问题（非 zigtester 本身）：
- `SingboxProcess._wait_ready` 始终检查 8388 端口，对 Trojan(9443)/Hysteria2(10443) 无效 → 已加 `ready_port` 参数
- 共享配置加载全部 5 个 inbound，无需的端口也被绑定 → 已加 `protocol_type` 参数按需过滤
- benchmark.py Hysteria2 就绪检测为 `sleep(1.0)` → 改为 lsof UDP 检测
- benchmark.py JSON 导出 hysteria2 误写 `trojan_port` key → 已修正
- bench-hysteria2 超时 → zigtester.yaml 加 `--count 10`

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

## sing-box 使用现状调研（2026-08-07）

### 现有实现

zigoutbounds 有 **3 个独立的 SingboxProcess 实现**，大量重复代码：

| 文件 | 行号 | 特点 |
|------|------|------|
| `test_protocols.py` | 596-872 | 最完整：动态/共享双模式，UDP 检测，stderr dump |
| `test_all_protocols.py` | 206-280 | 服务器 + 多客户端（每协议一个 SingboxProcess） |
| `benchmark.py` | 265-471 | 三协议（SS2022/Trojan/Hy2），自签证书生成 |

### 共同模式

1. **启动**: 清理残留 → `sing-box run -c <tempfile>` → 轮询端口
2. **配置**: 共享 JSON 文件（13 个）或代码 `json.dumps` 动态生成
3. **就绪检测**: TCP `socket.create_connection`；UDP `lsof -iUDP`（macOS）/ `ss -uln`（Linux）
4. **失败诊断**: 超时 drain stderr 最后 5 行
5. **停止**: SIGTERM → wait 3-5s → SIGKILL → unlink 临时文件

### 关键端口

| 协议 | 端口 | 配置来源 |
|------|------|---------|
| mixed | 2080 | singbox_all_inbounds.json, singbox_test.json |
| shadowsocks 2022 | 8388 | 动态生成 / singbox_test.json |
| trojan | 9443 | singbox_test.json |
| hysteria2 | 10443 | singbox_test.json / benchmark.py |
| vmess | 16800 | singbox_all_inbounds.json |
| vless | 16801 | singbox_all_inbounds.json |
| hysteria2 (alt) | 16802 | singbox_all_inbounds.json |
| tuic | 16803 | singbox_all_inbounds.json |

### 遗留文件

`notun_ss2022_test.json`、`tun_ss2022_udp_test.json`、`singbox_ss2022_udp_server.json` — 未被任何代码引用，仅用于手动 `sing-box run -c` 调试。

### 设计决策

| 决策 | 理由 |
|------|------|
| 用 REST API 热重载而非反复启停进程 | 一次启动，配置随意切换，消除端口冲突 |
| 最小 base.json（仅 API + empty inbounds） | 不预设协议端口，由 suite 按需推送 |
| 插件 config 字段声明默认端口 | 各项目定制独立端口，不硬编码 |
| 先实现插件核心，后迁移测试脚本 | 渐进式，不破坏现有测试 |

### sing-box REST API 关键信息

- 配置 `experimental.clash_api.external_controller` 开启（**嵌套格式**，非扁平 `experimental.external_controller`）
- `PUT /configs` 热重载（需完整配置，不是 patch）
- `GET /version` 健康检查
- 认证：`Authorization: Bearer <secret>`（可选）

### sing-box 统一配置实现发现（2026-08-07）

#### Clash API 不支持原生格式热重载

`PUT /configs` 返回 HTTP 200/204 但实际**只接受 Clash 格式配置**，sing-box 原生格式（如 `shadowsocks`、`hysteria2`、`tuic`）通过 API 推送后端口不会出现。这是 sing-box Clash API 的设计限制，非 bug。

**结论**：热重载不可行。serve 模式直接用 `test_server.json` 完整配置启动，需要切换配置时直接重启进程。

#### 统一配置端口表

合并了原 `singbox_test.json`（6 inbound）和 `singbox_all_inbounds.json`（6 inbound）：
- 共享端口：mixed(2080), ss(8388), trojan(9443), vmess(16800), vless(16801), tuic(16803) — 6 个
- singbox_test.json 独有：hysteria2(10443), dns-direct(5354) — 2 个
- singbox_all_inbounds.json 独有：socks(2081), http(2082), hysteria2-alt(16802) — 3 个
- experiment-hysteria2（已删除）：hysteria2-salamander(16804) — 1 个
- 合计 12 inbound，0 端口冲突
	- **后续简化 (2026-08-07)**：移除 socks(2081) 和 http(2082) — mixed:2080 已同时支持 SOCKS5+HTTP 代理。统一配置现为 10 inbound 双栈（`"listen": "::"`）。

#### Hysteria2/TUIC 使用 UDP

Hysteria2 和 TUIC 基于 QUIC 协议，使用 UDP 传输。端口检测时必须用 `lsof -iUDP`（macOS）或 `ss -uln`（Linux），`lsof -iTCP` 查不到这些端口。

#### TLS 证书迁移

证书从 zigoutbounds 迁移到 `plugins/sing-box/certs/`，使 sing-box 插件完全自包含，不依赖其他项目的文件路径。这避免了跨项目的隐式依赖。

#### 无关项目分析结果

- **zigbox**：测试直接连 echo server，不经过 sing-box。只有 zigoutbounds 用 sing-box。
- **zigtun**：无 sing-box 依赖。
- **experiment-hysteria2（已删除）**：手动测试脚本使用 sing-box，但无自动化测试。

### TUN 测试迁移：zigtun vs zigbox 职责分离（2026-08-07）

TUN 功能测试从 zigbox 迁移到 zigtun（TUN 组件库），实现关注点分离：

- **zigtun**（TUN 库）：TUN 设备创建/路由/数据包 I/O 的功能验证 → `tests/test_tun.py` → `zig-out/bin/zigtun-test`
- **zigbox**（编排层）：仅 NOTUN 模式自动化测试，TUN 手动调试保留在 `test_tun.py`

**决策理由**：zigtun 是 TUN 设备的库实现，TUN 设备级别的功能测试（创建、路由、I/O）应在其自身项目中验证。zigbox 作为编排层，不需要重复验证 TUN 设备本身。

### zigbox test_all.py 简化（NOTUN-only）

移除内容：
- `--tun`/`--notun` CLI 参数
- `STAT_FILE`、`HEALTH_THRESHOLDS`、`STAT_MAX_AGE_SEC` 常量
- `check_health()` 函数（仅 TUN 模式使用 zigbox.stat）
- `enable_tun` 参数从 `start_zigbox()` 移除

步骤从 1-6 简化为 1-5（移除健康检查步骤）。

### Zig 0.16.0 编译陷阱（zigtun test_main.zig）

在编写 `zigtun/src/test_main.zig` 时遇到的 Zig 0.16.0 编译问题：

| 问题 | 原因 | 修复 |
|------|------|------|
| `var opts` should be const | 0.16.0 强制不可变变量为 const | `var` → `const` |
| `std.process.argsAlloc` 不存在 | 0.16.0 IO 重构移除 | `std.process.Init.Minimal` + `init.args.toSlice(allocator)` |
| `std.fs.cwd()` 不存在 | 0.16.0 IO 重构移除 | 移除文件 I/O，使用硬编码默认值 |
| `.ipv4`/`.ipv6` 不存在 | 0.16.0 改为 `.ip4`/`.ip6` | 更新 switch 分支 |
| `Ip4Address` 不是 `[4]u8` | 0.16.0 改为结构体 | 使用 `.bytes` 字段提取原始字节 |
| `tun.createTun()` 是 stub | 实际实现在 `createTunPlatform()`（mod.zig 私有函数） | 将 `createTunPlatform` 改为 `pub`，同时导入 `tun.zig`（类型）和 `mod.zig`（函数） |
| `catch \|err\| { }` 块返回 void | catch 块返回类型必须匹配成功负载类型 | 改用 `if/else` 解构 error union |

### plugin.yaml `config:` 段被静默忽略 (2026-08-08)

**问题**：`parse_plugin_config()` 解析 `plugin.yaml` 时没有读取 `config:` 段，导致 `PluginConfig.config` 始终为 `{}`。这意味着 `start_plugin()` 不会设置任何 `PLUGIN_*` 环境变量，插件的启动脚本只能靠自身硬编码默认值。

**根因**：`parse_plugin_config()` 在两处 `return PluginConfig(...)` 中都没有包含 `config=...` 参数。`plugin.yaml` 中定义的 `config:` 段（如 sing-box 的 13 个默认端口配置）被 yaml.safe_load 解析后直接丢弃。

**影响**：sing-box 插件启动时缺少 `PLUGIN_API_LISTEN`、`PLUGIN_ECHO_PORT`、`PLUGIN_SS_PORT` 等环境变量。虽然 singbox_ctl.py 有自身默认值，但 zigtester 完全失去了对插件端口配置的控制力。项目级 `zigtester.yaml` 中的插件配置覆盖也无法与默认配置合并。

**修复**（`plugin.py` + `config.py`）：
1. `parse_plugin_config()` — 读取 `config:` 段，通过 `config=config` 传入 `PluginConfig`
2. `parse_config()` — `dict(p.get("config", {}))` → `dict(overrides) if isinstance(overrides, dict) else {}`，防御 YAML 中 `config: null` 导致的 TypeError

**合并语义**：插件默认配置（`plugin.yaml` `config:`）→ 项目覆盖配置（zigtester.yaml 中 dict 格式插件的 `config:`）→ `PLUGIN_<KEY>` 环境变量

### MCP stdio transport 多实例问题 (2026-08-08)

**现象**：多次"重启"MCP Server 后，`ps aux | grep zigtester.server` 发现 3 个进程同时运行（不同 PID，不同启动时间）。旧进程没有被终止，每次重启只是增加新进程。

**根因**：stdio transport 下 MCP Server 的生命周期由 Claude Code 管理——启动新进程和终止旧进程之间没有原子性保证。旧进程的 stdin 未关闭，继续阻塞在 `mcp.run()` 上等待输入，成为僵尸进程。

**影响**：
- MCP 调用可能连接到旧进程（未加载最新代码），导致 bug 修复看似"不生效"
- 资源泄漏（每个 Python 进程 ~30MB）
- 难以排查——错误行为取决于 MCP 路由到哪个进程

**解决方案**：切换到 HTTP transport（`mcp.run(transport="http", host="127.0.0.1", port=9020)`）。端口绑定天然互斥——第二个实例启动时直接报 `Address already in use`，物理上不可能出现多实例。同时添加 PID 文件（`~/.zigtester/server.pid`）用于健康检查和优雅关闭追踪。

**配置变更**（`~/.claude.json` 的 `mcpServers.zigtester`）：
- 旧：`{"type":"stdio","command":"python3","args":["-m","zigtester.server"],"env":{...}}`
- 新：`{"type":"http","url":"http://127.0.0.1:9020/mcp"}`

## Visual/Browser Findings

-
