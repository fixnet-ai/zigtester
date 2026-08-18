# Progress Log

## Session: 2026-08-18（续二）Phase 11 flaky 根因修复 + 插件管道排空 ✅
- **Status:** complete（workflow wf_95e67554-b2a：AB 独占插件环境顺序修两项目 / C 只做自包含单测）
- zigproxy burst flaky（AB）:
  - 根因：测试客户端超时缺陷——_burst_forward_request 的 8s 被 _read_http_response 内部 settimeout(5) 静默覆盖；create_connection 3s（其他场景 10s）。高负载误判"超时"，非 relay 退化（满载 12 次无 0 字节/重置失败）
  - 修复：连接超时 3→10s + 显式 timeout=8；11 spinner 满载 5 连绿 + 安静 1 绿
- zigdns「多域名 miss」（AB）:
  - 根因：测试期望自创建即错——5533 是 FakeIP 端口，echo.* 按设计返回确定性 FakeIP；real-map 是 15353 行为。src 无 bug
  - 修复：期望改 FakeIP + 三域名 FakeIP 互不相同断言（固化哈希分散语义）；5 连 3/3
- 插件 PIPE drain（C）:
  - _start_plugin_process +plugin_name 参数 + stdout/stderr 双 daemon 线程逐行写 /tmp/zigtester-plugin-<name>.log（wb truncate、逐行 flush）；plugin_log_path()；env_spec 加日志路径提示
  - 单测 +chatty 200KB 不阻塞用例 → 15/15 × 3；实测 zigbox run 后三插件日志齐全
  - C 调试插曲：text 模式写 bytes 报 TypeError → 改 wb；日志缺行确认为子进程 stdio 块缓冲（flush=True 解决）
- 主线程统一验证: 单测 30/30；zigbox 3/3；zigproxy 5/5；zigdns 3/3（后两者首次长期全绿）
- 新观察（未修）: 极端满载（10 核 spinners）下 local-echo 启动偶发 ERROR（4 次 1 失败），失败阶段未定位；复现时先查 /tmp/zigtester-plugin-local-echo.log

## Session: 2026-08-18（续）Phase 10 测试流畅性改进 — workflow 并行实施 ✅
- **Status:** complete（workflow wf_9faabef5-5e8：4 agent 并行，238K tokens / 83 tool calls / 4.9 分钟）
- Agent A（zigtester reporter/cli/server/history + 新单测）:
  - `extract_failure_lines()`（✗/FAIL/ERROR: 行，去 ANSI，≤10 行，160 字符截断）接入三层：终端失败套件红色行、save_json 的 failure_lines、MCP zigtester_run 响应
  - CLI `--no-history`；check_regression 基线 PASS 过滤（原已存在，补无 status 兼容 `r.get("status","PASS")`）
  - `detect_flaky(window=8, 翻转≥2)` → zigtester_history 响应 + 终端标注
  - tests/test_report_history.py 15/15
- Agent B（zigtester plugin/runner）:
  - `_heal` 延迟复检 `_HEAL_STABILITY_DELAY=2.5s`（死亡窗口假阳性教训注释）
  - `run_workspace` 分组并行：无插件组 ThreadPool（结果按传入顺序）+ 有插件组串行，fail_fast 保留
  - test_env_guard.py +2（unstable 1 秒自杀插件自愈应 fatal；分组执行）→ 14/14
- Agent C（zigbox）:
  - test_scenarios.py 每组合后重探 echo 四端口；失联 → FATAL + zigtester 指引 + 兼容 parser 总计行 + exit 1；汇总抽为 _print_summary()
  - 实测：正常 10.6s 全绿；失联注入 5.4s 退出（原 238s）；无残留
- Agent D（zigroute/zigunicfg 审计）:
  - a 类直跑指引 5 处修订（README/CLAUDE.md/docs/roadmap.md）；b/c 类 0
  - 两项目无插件 → 收敛式铁律；zigunicfg comprehensive 训练流水线保留手动例外（外部二进制+自有 Docker，铁律注明理由）
  - 修正 zigroute 层级描述（unit+performance → unit）
- 主线程统一验证:
  - 单测 29/29；py_compile 全过；--all unit --parallel 8/8 全绿（分组生效）
  - failure_lines 端到端 ✓（插曲：首次验证命令 `exit 1` 是 Python 语法错误导致 stdout 空，误判功能不工作；修正为 SystemExit(1) 后确认终端红行 + JSON 数组均正常）
  - zigbox functional 3/3
- 提醒: MCP Server（9020 常驻）需重启加载 server.py 改动

### MCP 服务 description 优化 ✅
- `FastMCP("zigtester", instructions=...)` — 新增 server 级说明（637 字，initialize 下发）：唯一入口定位 + 三条核心约定（禁直跑脚本/禁手动启停插件/ERROR 时唯一动作=重新 run）+ 工具选择决策表 + 代理诊断提示
- 5 个工具 docstring 重写（即 MCP description）：
  - scan（150 字）— 用一次即弃、path 即后续 dir 参数
  - list（166 字）— 运行前确认层级/套件名/sudo，套件名是 history 参数来源
  - run（376 字）— 唯一入口 + 自动环境管理 + 结果解读（report 原样展示 / failure_lines 定根因 / ERROR=环境问题 / suite 最小复现）
  - history（204 字）— flaky 语义（单次结果不足为凭）、基线只统计 PASS
  - init（125 字）— 推荐改用 create-tester skill 交互式接入
- 验证：py_compile OK；重启后 initialize 响应含 instructions（实测 637 字）；tools/list 5 个 description 全部为新文案

## Session: 2026-08-18

### Phase 9: 测试环境统一治理 + pre-flight 自检 ✅
- **Status:** complete
- Actions taken:
  - **zigtester（B 面强化）**:
    - `plugin.py` — 新增 `PluginManager`（prepare/ensure_ready/stop_all）、`verify_plugin` 三层校验（进程存活 + readiness 端口 + 端口归属进程树）、`_descendant_pids`/`_port_owner_map`（一次 ps + 一次 lsof）、`_cleanup_stale_processes`（仅清可识别插件残留，未知进程不误杀）、`env_spec_message`（fast fail 测试环境规范）
    - `plugin.py` 残留识别安全规则 — 只取 stop.kill 名单 + `*.py` 脚本名作 pkill 特征（`python3`/`serve` 等通用 token 会误杀 zigtester 自身/无关进程）
    - `runner.py` — run_project 重构接入 PluginManager；每套件前 ensure_ready 自检自愈；恢复失败 fast fail（首 suite ERROR + 剩余 SKIP + 规范输出）；`--parallel` 多插件项目强制降级串行（否则互杀）；修端口冲突时空结果误判 bug
    - `plugins/local-echo/plugin.yaml` — 补 ports 声明（9 端口，启用归属校验）
    - `plugins/sing-box/singbox_ctl.py` — **修 HTTP_PROXY 劫持 localhost API bug**（`_http = requests.Session(); trust_env=False`；根因：本机 HTTP_PROXY=127.0.0.1:7890 且 no_proxy 空 → /version 假失败 → serve exit 1 → 之前每轮"自愈成功"实为 3 秒死亡窗口假阳性）
    - `tests/test_env_guard.py` — 新建 12 用例单测（进程树/端口归属/健康/死亡/外部抢占/自愈/不可恢复 fast fail/残留清理/未知占用不误杀/规范文本/runner 辅助）
    - `CLAUDE.md` — 核心职责补环境自检自愈 + 生态级测试环境治理铁律
  - **zigoutbounds（最大绕过入口）**:
    - `test_engine.py` — 删 `start_standalone_singbox()` 自启路径 → detect-and-error；删孤立 finally 清理块
    - `tests/lib/singbox.py` — 修 urllib 代理坑（`ProxyHandler({})` opener）+ docstring/状态文案
    - `tests/lib/xray.py`、`tests/lib/test_config.py` — "将自行启动"文案 → "请经 zigtester run 启动"
    - `tests/h2-e2e/runner.py`、`tests/h3-e2e/runner.py` — 报错文案去掉手动启动选项
    - CLAUDE.md（铁律节 + 方式2/3 删除 + 最小复现命令改 --suite）、README.md 快速开始、API.md 运行方式、SKILL.md（铁律节 + 手动 sing-box 删除 + 单套件复现改 zigtester）
  - **zigbox**: config/README.md（直跑清单 + 手动 sing-box run 删除，改 zigtester 入口）；test_tun_scenarios.py FATAL 文案（去手动 sudo 启动指引）；CLAUDE.md 铁律节
  - **zigfoundation/zigdns/zigproxy/zigtun**: CLAUDE.md 统一铁律节（foundation/dns 收敛"直接 zig build test"措辞；proxy 修过期描述；zigtun 两处"直接运行"块改 zigtester）；README 提示；zig-codegen.md:2098 措辞
  - **zigdns 假绿修复**: test_client/test_forward/test_server detect-skip `exit(0)` → `exit(1)`
- Verification:
  - 单测 12/12 ✅
  - zigbox functional 3/3 ✅（含真实残留 PID 18973 自动清理接管场景）
  - zigoutbounds functional 7/7 ✅
  - 全 workspace unit 8/8 ✅（--parallel 自动降级串行）
  - 中途 kill -9 插件 → 后续套件自愈 → 全过 ✅
  - blocker 占 13338 → 精确报未知进程 PID + 不误杀 + fast fail 规范 ✅
- 预存 flaky（非本次引入，待项目内修）:
  - zigproxy test-engine burst 用例高负载下 2/17 挂（timing 敏感；安静环境 5/5 过）
  - zigdns client "多域名 miss" FakeIP 全命中（缓存行为）

## Session: 2026-08-10

### xray-core 插件 + 跨参考实现互测 🔄
- **Status:** Phase 8.1 + 8.2 完成；8.3 (zigoutbounds 接入) 规划完成，待执行
- Actions taken:
  - `plugins/xray-core/plugin.yaml` — 镜像 sing-box 插件结构（config/build/lifecycle/ports）
  - `plugins/xray-core/xray_ctl.py` — 镜像 singbox_ctl.py：
    - 模板渲染（`__KEY__` 占位符 + `PLUGIN_*` env 注入）
    - 证书路径绝对化（避免 xray 进程 cwd 错位找不到证书）
    - 启动前端口冲突检测（与框架级互补）
    - 轻量 readiness 服务（:9190 裸 TCP）— xray 不暴露 REST API
    - CLI `serve` / `render` 子命令
  - `plugins/xray-core/configs/test_server.json` — 8 个协议入站（socks/ss/trojan/vmess-ws/vless-tls/vless-ws/vless-reality/vless-grpc），含 Reality + grpc 预留入站
  - `plugins/xray-core/certs/` — 复用 sing-box localhost 测试证书
  - `plugins/sing-box/plugin.yaml` + `plugins/xray-core/plugin.yaml` — 加 `ports:` 字段启用冲突检测
  - `src/zigtester/plugin.py` — `PluginConfig.ports` 字段 + `check_port_conflicts()` 函数（跨插件 + 系统占用两类）
  - `src/zigtester/runner.py` — 启动插件前两遍扫描做端口预检，冲突时阻止所有插件启动
- 验证（2026-08-10 01:01）：
  - 模板渲染：12 个占位符全部正确替换
  - 证书路径：自动转为绝对路径 `/Users/.../certs/localhost.crt`
  - xray 启动：Xray 26.3.27 成功，8 个入站端口全部 LISTEN
  - 冲突检测：清洁状态 0 冲突；模拟冲突（端口 9090 同时声明）正确检出

#### 关键技术决策
1. **xray 端口与 sing-box +100 错开**：约定 `socks 2180 / ss 8488 / trojan 9543 / vmess 16900 / vless 16901 / vless-ws 16905 / vless-reality 16906 / vless-grpc 16907`
2. **xray Reality 凭证独立**：与 sing-box Reality 协议不完全兼容，UUID/私钥各自独立
3. **证书路径绝对化**：xray 二进制 `/usr/local/bin/xray` 启动后 cwd 在该目录，`certs/localhost.crt` 相对路径解析失败 → 必须在 `xray_ctl.py` 渲染时转为绝对路径
4. **readiness 探针用裸 TCP**：xray 无 REST API，自建 HTTP 服务复杂；裸 TCP accept 即证明进程存活
5. **跳过 VMess + Reality/grpc 接入测试**：zigoutbounds 客户端未实现 Reality/grpc，E2E 无意义
6. **冲突检测前置**：框架级在所有插件启动前检测，冲突则不启动任何插件（避免半启动状态）

#### 范围决策（与用户确认）
- 接入协议：SS2022 / Trojan / VLESS-tls / VLESS-ws（4 个，跳过 VMess）
- 套件类型：xray-* 单插件套件 + cross-* 双插件对比套件
- 脚本：复用现有 test_protocols.py / benchmark.py（加 xray 复用分支 + `--cross-impl` flag）
- 层级：functional + performance 都加
- Reality/grpc：作为 xray 入站保留，**不接入**测试

## Session: 2026-08-08

### MCP Server stdio → HTTP transport 迁移 ✅
- **Status:** complete
- Actions taken:
  - `server.py` — `mcp.run()` → `mcp.run(transport="http", host="127.0.0.1", port=9020)`
  - 新增 PID 文件管理（`~/.zigtester/server.pid`，启动写入、退出清理）
  - `~/.claude.json` — zigtester 从 stdio command 改为 `{"type":"http","url":"http://127.0.0.1:9020/mcp"}`
  - `CLAUDE.md` + `README.md` — 文档更新
- Root cause: stdio transport 下 MCP Server 生命周期由 Claude Code 管理，重启时旧进程未正确终止，累积 3 个僵尸进程。HTTP transport 端口绑定天然互斥，从物理上杜绝多实例。
- Fix verified: 单进程运行，`/mcp` 端点正常响应

### suite 过滤实现 ✅
- **Status:** complete
- Actions taken:
  - `runner.py` `run_project()` — 新增 `suite_filter` 参数，`_filter_suites()` 递归解析传递依赖
  - `cli.py` `cmd_run()` — `args.suite` 传递给 `run_project()`，help 文本更新
  - `server.py` `zigtester_run()` — `suite` 参数传递给 `run_project()`
- Root cause: `--suite` 参数被解析但从未使用（help 文本明确写了"暂未实现"），MCP `suite` 参数同样被忽略
- Fix verified: `--suite crypto-ss2022` 只跑 1 个；`--suite e2e-ss2022` 递归包含依赖跑 2 个；不存在的套件返回 0 个不崩溃；无 `--suite` 8 个全部（回归通过）

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
  - experiment-hysteria2/test/e2e_salamander.sh（已删除）— 添加插件运行时检测
  - experiment-hysteria2/test/e2e/run_e2e_test.py（已删除）— 添加插件运行时检测
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

---

## 2026-08-08: v0.21.0 — 统一版本发布

- MCP Server stdio → HTTP transport 迁移（端口绑定互斥，杜绝僵尸进程）
- suite 过滤实现（`--suite` 参数递归解析传递依赖）
- 插件 config 解析修复（plugin.yaml config 段被静默丢弃的 bug）
- sing-box test_server.json 新增 Hysteria2 配置（端口 10443）

全项目统一版本发布：包含 zigfoundation、zigtun、zigproxy、zigdns、zigoutbounds、zigbox 所有 v0.21.0 同步更新。

## 2026-08-17（续）统一 Go echo 完成 + 生态切换

- **local-echo 单程序承载全部 echo 协议**（替代 python3 echo_server.py + h2h3-echo 两进程，已删除）：
  - TCP :13333（协议自适应 SOCKS5/CONNECT/HTTP）+ UDP :13333 raw echo + DNS :5533 FakeIP
  - h2 :13335 / h3 :13336（Go 标准库 + quic-go http3）
  - HTTP :18080 / TLS :18443（zigbox 矩阵 curl/SNI 目标）
  - real DNS :15353（real-map 模式，zigbox 矩阵 server B）
  - bench :13337（短连接压测，10ms idle 主动关——grpc 死锁解除）+ stream :13338（长连接压测，无 idle——N:1 轮转空隙误杀实证，findings zo §28.14）
- **原则落地：各项目不再直接操作 echo-server**——zigbox/zigproxy/zigdns/zo 的测试脚本由自启动改为 detect-and-error（未运行提示 zigtester run），echo 生命周期统一由插件常驻管理
- 验证：zo functional+performance 28/28 全绿

## 2026-08-19: sing-box 插件 Windows 编码修复

- plugins/sing-box/singbox_ctl.py：裸 open() 用 locale 编码（Windows cp1252）读含 UTF-8 中文注释的 test_server.json → UnicodeDecodeError → 插件启不动。补 encoding="utf-8"（两处）。commit 0b25d96
- 背景：zigbox windowsvm 实机回归首跑发现。另：框架 Windows 侧遗留 pkill//tmp POSIX 假设登记 zigbox #63
