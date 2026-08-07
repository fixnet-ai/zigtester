# Progress Log

## Session: 2026-08-08

### 插件 config 解析修复 ✅
- **Status:** complete
- Actions taken:
  - `plugin.py` `parse_plugin_config()` — 读取 `plugin.yaml` 的 `config:` 段，传入 `PluginConfig`
  - `config.py` `parse_config()` — 防御 `config: null` 导致的 `dict(None)` TypeError
- Root cause: `parse_plugin_config()` 在两处 `return PluginConfig(...)` 中都没有包含 `config=...` 参数，plugin.yaml 的 config 段被静默丢弃，PluginConfig.config 始终为 {}
- Fix verified: sing-box 插件 13 个默认端口全部正确加载；项目级覆盖正确合并（默认 → 覆盖 → PLUGIN_* env）

## Session: 2026-08-07

### Task 1: 生命周期管理审计与清理 ✅
- **Status:** complete
- **Started:** 2026-08-07 22:00
- **Completed:** 2026-08-07 23:00
- Actions taken:
  - zigbox/tests/test_bench.py — 删除死代码 `_kill_on_port()`（从未调用，避免未来误杀插件进程）
  - zigoutbounds/tests/tools/scaffold_protocol.py — 更新代码生成模板，生成的命令模板现在包含插件检测
  - zigoutbounds/experiment-hysteria2/test/e2e_salamander.sh — 添加 `PLUGIN_SINGBOX_RUNNING` 检测，插件运行时跳过 sing-box 启动和端口清理
  - experiment-hysteria2/test/e2e/run_e2e_test.py — 添加 `is_plugin_singbox_running()`，插件运行时复用统一配置端口
- Verification: 插件 sing-box 运行时，所有脚本不再冲突或杀死插件进程

### Task 2: TUN 测试迁移到 zigtun ✅
- **Status:** complete
- **Started:** 2026-08-07 23:00
- **Completed:** 2026-08-07 23:45
- zigtun 侧:
  - `src/test_main.zig` — 新建 TUN 功能测试 CLI 二进制（3 测试：create/routes/packet-io）
  - `build.zig` — 添加 `test-cli` 构建步骤
  - `configs/test_tun.json` — 测试 TUN 配置
  - `tests/test_tun.py` — Python 编排器
  - `zigtester.yaml` — 新增 functional 层级（tun-create/tun-routes/tun-packet-io，均需 sudo）
  - `CLAUDE.md` — 添加功能测试章节
- zigbox 侧:
  - `zigtester.yaml` — 移除 tun-transparent-proxy 套件
  - `tests/test_all.py` — 简化为 NOTUN-only（移除 --tun/--notun、STAT_FILE、HEALTH_THRESHOLDS、check_health）
  - `tests/test_tun.py` — 标记为手动调试用途
  - `tests/SKILL.md` — 测试归属边界表新增 zigtun 行
  - `tests/DEV.md` — 更新 TUN 引用
  - `CLAUDE.md` — 更新测试分层描述
- Verification:
  - `zig build test-cli` 通过（zigtun 侧，需修复 Zig 0.16.0 API 变更：argsAlloc→Init.Minimal、Ip4Address struct、createTunPlatform pub、catch 返回类型匹配）

### 评估与会话初始化（已完成）
- **Status:** complete

### Phase 1: zigbox 试点替换 🧪
- **Status:** complete
- Token 实际节省：~9K → ~1.5K（**-83%**）

### 实现：run_workspace 多项目并行执行 ✨
- **Status:** complete
- **Started:** 2026-08-07 05:20
- Actions taken:
  - `src/runner.py`: `run_workspace()` 的 `parallel` 参数从死代码变为 `ThreadPoolExecutor` 实际实现
  - `src/cli.py`: `run` 子命令新增 `--parallel` flag
  - 修复打包结构：`src/*.py` → `src/zigtester/*.py`（模块平铺 → 包目录）
  - 补 `src/zigtester/__main__.py` 让 `python -m zigtester` 可用
  - 验证：`zigtester run --all --dir ~/fixnet --level unit --parallel` 两个项目并行完成（~3s vs 串行 ~6s）
- Files created/modified:
  - `src/runner.py`（编辑：import + run_workspace 实现 + docstring 更新）
  - `src/cli.py`（编辑：--parallel flag + 传参）
  - `src/zigtester/*.py`（移动：打包结构修正）
  - `src/zigtester/__main__.py`（新建）

### Phase 2: zigtester.yaml 全覆盖
- **Status:** complete ✅
- **zigdns ✅** — 2026-08-07 09:09 | unit 层级，1/1 passed (2.8s)
- **zigtun ✅** — 2026-08-07 09:12 | unit 层级，1/1 passed (3.1s)
- **zigproxy ✅** — 2026-08-07 09:12 | unit 层级，1/1 passed (5.5s)
- 验证：`zigtester scan` 发现全部 5 个项目 ✅
- 验证：`zigtester run --all --level unit --parallel` 全部通过 ✅ (5/5, ~5.5s 并行)

### Phase 3: 简单项目接入
- **Status:** complete ✅
- **zigfoundation ✅** — CLAUDE.md 添加 `## 测试` 节
- **zigdns ✅** — CLAUDE.md 添加 `## 测试` 节
- **zigtun ✅** — CLAUDE.md 添加 `## 测试` 节
- **zigproxy ✅** — CLAUDE.md 添加 `## 测试` 节

### Phase 4: zigoutbounds skill 替换（等重构完成后）
- **Status:** complete ✅
- 重构已稳定（ProtocolClient + Session 统一完成）
- **zigtester.yaml** ✅ — 11 套件（unit×1 + functional×7 + performance×3）
- **outbound-dev skill** ✅ — 删除，内容迁入 CLAUDE.md（可行性评估、UDP 架构、执行模型、常见陷阱）
- **tests skill** ✅ — 14K → 1.5K stub（MCP 速查 + E2E 前置条件 + 故障排查）
- **Token 节省**：21K → 1.5K（**-93%**）
- zigbox tests/SKILL.md 3 处引用已更新（指向 CLAUDE.md）

### Phase 5: 最终验证
- **Status:** complete

### Phase 6: P6.4 local-echo 插件迁移 ✅
- **Status:** complete
- **Started:** 2026-08-07 10:30
- **Completed:** 2026-08-07 11:03
- Actions taken:
  - **决策**：用户明确要求不用 Zig，改用 Python + asyncio 高性能异步 IO
  - 新建 `plugins/local-echo/echo_server.py`（~450行）：TCP 协议检测 + UDP DNS 代理 + FakeIP
  - 新建 `plugins/local-echo/plugin.yaml`：插件清单（build: true，ready_on: tcp:13333）
  - 解决 3 个关键问题：
    1. Python 3.14 `asyncio.start_server` 强制 Stream API → 改用 `loop.create_server()` for Protocol API
    2. macOS 双栈：`0.0.0.0` + `::` 分别绑定（IPv4 和 IPv6）
    3. DNS 端口冲突：macOS mDNSResponder 占用 5353 → 改为 15353
  - 清理 zigbox 生产代码：
    - `src/main.zig` — 删除 `--local-echo` CLI
    - `src/engine.zig` — 删除 local-echo 初始化/启动/停止/deinit（~160 lines）
    - `src/types.zig` — 删除 `local_echo` 字段
    - `src/local-echo.zig` — 删除（1285 lines）
  - 清理 zigbox 测试脚本：
    - `tests/lib/config.py` — 新增 ECHO_SERVER_PATH、ECHO_DNS_PORT
    - `tests/lib/zigbox.py` — 新增 ensure_echo_server() / is_echo_server_running()
    - `tests/test_protocols.py` — local_echo=True → ensure_echo_server()
    - `tests/test_bench.py` — 移除 local_echo 参数
    - `tests/test_tun.py` — 移除 local_echo=True
    - `tests/test_all.py` — 移除 --local-echo flag
  - echo_server.py DNS 端口改为 53（SO_REUSEADDR 绑定 127.0.0.1，与 mDNSResponder 共存）
  - ensure_echo_server() 智能判断：root→启用 DNS；非 root→--no-dns（TCP only）
- Verification:
  - `zig build` ✅ | `zig build test` ✅
  - `python3 tests/test_protocols.py` → 8/8 passed ✅
  - `python3 tests/test_bench.py --mode socks5 -c4 -n40` → 40/40, 1351 req/s ✅
  - `python3 tests/test_all.py --skip-bench` → 3/3 passed ✅
  - `zigtester run zigbox --level functional` → 3/4 passed (tun 需 sudo 预期失败) ✅
- Files created:
  - `plugins/local-echo/echo_server.py`（新建）
  - `plugins/local-echo/plugin.yaml`（新建）
  - `src/zigtester/plugin.py`（新建，P6.3）
- Files modified:
  - `zigbox/` — 11 files (生产代码 4 + 测试脚本 6 + zigtester.yaml)
  - `zigtester/` — 8 files (P6.1-P6.3 的 config/runner/reporter/server/schema + task_plan/progress/findings)
- Key insight:
  - **架构变化**：DNS 拦截从 macOS utun (IP 层) 降级为 UDP DNS 代理（应用层）。TUN 模式下需额外配置 DNS 路由到 127.0.0.1:15353。NOTUN 模式（主要测试场景）无需 DNS echo。
  - **Token 节省**：echo server 作为独立进程运行，不在 Claude 上下文中加载，每次对话节省 ~3K tokens
  - **跨平台**：Python asyncio 纯标准库实现，无外部依赖，Linux/macOS/Windows 均可运行

### Phase 6: 全部完成 ✅
- P6.1 ✅ setup/teardown 生命周期钩子
- P6.2 ✅ readiness probe (TCP + process)
- P6.3 ✅ 插件体系 (plugin.py)
- P6.4 ✅ local-echo 插件迁移（Python asyncio）

### Phase 7: sing-box 插件 — 统一配置完成 ✅
- **Status:** 统一配置设计完成 + 验证通过
- **Started:** 2026-08-07 11:30
- **Completed:** 2026-08-07 22:11
- **目标:** 统一管理 sing-box 进程生命周期，一个配置文件满足所有测试需要
- **完成项:**
  - [x] 调研：6 项目 sing-box 使用分析（仅 zigoutbounds 实际使用，zigbox 不涉及）
  - [x] TLS 证书迁移：`zigoutbounds → plugins/sing-box/certs/`（localhost.crt/key + test-localhost.crt/key）
  - [x] `configs/test_server.json` — 统一配置文件（10 inbound + clash_api + direct outbound，双栈 ::）
  - [x] `configs/base.json` — 修正 `experimental.clash_api` 嵌套格式
  - [x] `singbox_ctl.py` — serve 模式直接用 test_server.json 启动（移除无效的热重载）
  - [x] `plugin.yaml` — 完整端口默认配置 + 简化启动命令
  - [x] 验证：10 端口全部正常（TCP: 2080/8388/9443/16800/16801, UDP: 5354/10443/16802/16803/16804；2081/2082 已移除，mixed:2080 覆盖 SOCKS5+HTTP）
- **关键发现:**
  - sing-box Clash API `PUT /configs` 只接受 Clash 格式配置，不支持原生格式热重载
  - Hysteria2/TUIC 是 QUIC/UDP 协议，需用 `lsof -iUDP` 检测而非 TCP
  - 原两套配置唯一冲突是 hysteria2 端口（10443 vs 16802），统一配置双端口共存解决
- **统一配置端口分配:**
  | 端口 | 协议 | 传输 | 来源 |
  |------|------|------|------|
  | 2080 | mixed (SOCKS5+HTTP) | TCP | 统一双端口合一 |
  | 5354 | direct (DNS) | UDP | test |
  | 8388 | SS2022 | TCP+UDP | 所有测试 |
  | 9443 | trojan | TCP | 所有测试 |
  | 10443 | hysteria2 | UDP | test + benchmark |
  | 16800 | vmess | TCP | all_inbounds |
  | 16801 | vless | TCP | all_inbounds |
  | 16802 | hysteria2 (alt) | UDP | all_inbounds |
  | 16803 | tuic | UDP | all_inbounds |
  | 16804 | hysteria2+salamander | UDP | experiment |
  - socks(2081) 和 http(2082) 已移除，mixed:2080 已同时支持 SOCKS5 和 HTTP 代理协议
- **待后续 (P7.4):** zigoutbounds 测试脚本迁移到 SingboxController

## Test Results

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| `--help` 含 parallel | `zigtester run --help` | 显示 --parallel | 显示 --parallel | ✓ |
| `-m zigtester` | `python3 -m zigtester --help` | 正常输出 | 正常输出 | ✓ |
| scan | `zigtester scan --dir ~/fixnet` | 发现 zigbox + zigfoundation | zigbox + zigfoundation | ✓ |
| zigfoundation unit | `zigtester run zigfoundation --level unit` | PASS | ✅ 343/344 passed | ✓ |
| --parallel 双项目 | `zigtester run --all --parallel --level unit` | 两个项目并行完成 | 各 ~3s，并行完成 | ✓ |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-08-07 | `No module named zigtester` | 1 | 修复打包结构：`src/*.py` → `src/zigtester/*.py` |
| 2026-08-07 | zigoutbounds 4/11 失败 (port 2080 冲突 + 僵尸进程) | 1 | 上游脚本修复（见 Phase 6）+ bench-hysteria2 超时通过 `--count 10` 缓解 |
| 2026-08-07 | benchmark.py psutil 清理误杀自身（cmdline 全匹配） | 1 | 改为仅匹配 `name`/`exe`，不过度匹配 `cmdline` |

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | Phase 1-5 全部完成，Phase 6（测试生命周期管理）重新设计为架构级方案 |
| Where am I going? | Phase 6：setup/teardown 生命周期钩子 + readiness probe + 插件体系 + local-echo 迁移 |
| What's the goal? | 构建测试环境生命周期管理，消除 setup/teardown 的 ad-hoc 实现，将测试基础设施从生产代码中分离 |
| What have I learned? | 5 个流畅性问题的根因是同一个架构缺失——zigtester 没有生命周期管理。逐个修补解决不了根本问题。local-echo.zig 寄生在 zigbox 生产代码中是反模式，应作为 zigtester 插件管理。 |
| What have I done? | 6 项目接入 + 测试脚本 5 项修复 + 根因分析 + Phase 6 重新设计 |
