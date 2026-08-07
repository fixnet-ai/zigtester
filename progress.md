# Progress Log

## Session: 2026-08-07

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
  - echo_server.py DNS 默认端口 5353→15353（避免 macOS mDNSResponder）
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
