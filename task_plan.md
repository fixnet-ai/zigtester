# Task Plan: 修复 MCP 长任务超时（SSE 流 + progress 心跳）

> **创建**: 2026-08-21
> **状态**: ✅ 完成（待重启常驻 9020 server 生效）
> **关联**: server.py / mcp SDK json_response 语义

## Goal

「Performance 全量超过 MCP 调用超时上限」——根因不是 per-suite 超时，而是 `server.py` 用 `json_response=True`，mcp SDK 在该模式下**吞掉所有 progress 通知、只在最终 response 才返回单 JSON**，客户端（Claude Code）60s per-request 首字节超时。

## 方案

1. `main()`：`json_response=True` → `False`（切 SSE 流，priming event 立即发首字节）
2. `zigtester_run` 改 `async` + `ctx: Context`，`run_project` 用 `asyncio.to_thread` 跑，事件循环每 10s 发 `ctx.report_progress` 心跳（progress 单调递增 + message 已运行秒数）

## 步骤

- [x] 改 `server.py` `main()`：json_response=False
- [x] 改 `zigtester_run`：async + ctx + to_thread + 心跳
- [x] 验证：启动 server，带 progressToken 的 tools/call 确认 SSE + progress
- [x] 回归：跑 `tests/` 现有测试（test_report_history 15/15）

## 验证结果

- `content-type: text/event-stream` + `Transfer-Encoding: chunked`（SSE 流生效）
- 心跳 progress 经 HTTP 下发（`(1.0, None, 'tests running (10s elapsed)')`）
- result 正确返回（slow-test PASS）
- test_report_history.py 15/15 通过

---

# Task Plan: zigtester MCP 替换兄弟项目 test skills

> **创建**: 2026-08-07
> **更新**: 2026-08-18 — Phase 9 启动：测试环境统一治理（防绕过）+ zigtester pre-flight 自检
> **关联**: DESIGN.md § MCP 优先架构

## Goal

用 zigtester MCP 替换兄弟项目测试 skill 中 ~70% 的命令执行/结果解析内容，将不可替代的领域知识迁移到各项目 CLAUDE.md，实现每次加载 token 从 ~18K 降至 ~5.5K（-69%）。

## Current Phase

Phase 1-7 ✅ | Phase 8 🔄 | Phase 9 ✅ | Phase 10 ✅ | Phase 11 ✅（2026-08-18 workflow） | **Phase 12 ✅（2026-08-21 #69-④ 插件 host 化）**

### Phase 12: 插件 host 化（#69-④ 远程插件模式）✅
> **创建**: 2026-08-21 | **完成**: 2026-08-21（配合 zigbox #69-④ set_system_proxy 三 VM 实测）

**背景**: zigbox 三 VM 实测要求 VM 内不部署 local-echo/sing-box/xray-core，插件服务统一跑 host（本机），
VM 经可配置服务器 IP（默认 127.0.0.1，VM 场景 = host 网关 IP，如 192.168.64.1）连接。zigtester 侧落地 L1/L2。

#### P12.1 远程插件模式（L1）✅
- [x] 数据模型：`PluginConfig.host`（plugin.yaml 顶层）+ `PluginRef.host`（zigtester.yaml plugins 条目）
- [x] 优先级：`plugin_ref.host` > plugin.yaml `host:` > `ZIGTESTER_PLUGIN_HOST` env > `"127.0.0.1"`
- [x] `_is_local_host` 判定；非本机 host 远程分支：prepare 跳 build/start/cleanup 改远端 ready-on 就绪探测、
  verify 跳进程存活/端口归属、heal 不重启（仅 re-verify）、stop/stop_all 跳过（不杀远端进程）、
  port_conflict 跳系统占用段（保留跨插件重复声明）
- [x] 提交 `500cd7e`（4 文件：src/zigtester/config.py / plugin.py + plugins/local-echo/main.go / plugin.yaml）

#### P12.2 local-echo 绑定地址参数（L2）✅
- [x] `--host` flag（默认 127.0.0.1；TCP/UDP 全部绑定改 host）+ `--real-dns-answer-ip`（默认 127.0.0.1；
  real-DNS 应答 IP = echo 出站目标地址，host 侧 VM 服务传 $GW_IP）
- [x] plugin.yaml start command 显式 `--host 127.0.0.1 --real-dns-answer-ip 127.0.0.1`（本机默认不变）

#### 验证
- [x] 本机零回归：sysproxy PASS（本地模式）+ 远程 fast-fail 报错正确
- [x] 三 VM 实测全通过（结果登记在 zigbox task_plan/progress § #69-④）：linuxvm scenarios 20.4s +
  双身份 sysproxy、windowsvm sysproxy 4.5s、macvm sysproxy 19.0s

### Phase 11: flaky 根因修复 + 插件管道排空 ✅
> **创建**: 2026-08-18 | **完成**: 2026-08-18（workflow wf_95e67554-b2a：2 agent，118K tokens / 9.6 分钟）

#### P11.1 zigproxy burst flaky ✅
- [x] 根因 = **测试客户端超时设计缺陷**（非 relay 退化）：`_burst_forward_request` 设 8s 后被 `_read_http_response` 内部 `settimeout(5)` 静默覆盖；create_connection 3s（同文件其他场景均 10s）
- [x] 修复：连接超时 3→10s + 显式传 timeout=8；11 个 CPU spinner 满载下连续 5 次 16/16 PASS + 安静 1 次 PASS

#### P11.2 zigdns「多域名 miss」✅
- [x] 根因 = **测试期望自创建起即错**：5533 是 local-echo FakeIP 模式端口，echo.* 按设计返回确定性 FakeIP（real-map 是 15353 行为）；zigdns src 无 bug
- [x] 修复：期望改为 FakeIP + 新增三域名 FakeIP 互不相同断言（固化哈希分散语义）；连续 5 次 3/3 PASS

#### P11.3 插件 PIPE drain ✅
- [x] `_start_plugin_process` 增 plugin_name 参数 + stdout/stderr 双 daemon 线程逐行写 `/tmp/zigtester-plugin-<name>.log`（wb truncate）；`plugin_log_path()` 辅助；env_spec 排查行加日志路径
- [x] 单测 +1（chatty 200KB 不阻塞）→ 15/15 × 3 连绿
- [x] 实测 drain 生效：zigbox run 后三插件日志齐全（local-echo 含 RESULT=READY）

#### P11.4 主线程统一验证 ✅
- [x] 单测 30/30（env_guard 15 + report_history 15）；zigbox 3/3；zigproxy 5/5；zigdns 3/3（后两者首次长期全绿）

#### 新观察（AB 报告，未修待后续）
- **极端满载（10 核 spinners）下 local-echo 插件启动偶发失败**（4 次中 1 次 ERROR）——失败阶段未定位（go build 慢 or ready 超时），真实场景罕见；若复现先查 /tmp/zigtester-plugin-local-echo.log
- AB 曾观察到 plugin.py `_drain` 中间态崩溃（logf.write bytes vs text 模式）——C 并行开发时的临时态，最终版已修复并 3 连绿验证

#### 仍遗留（本轮不做）
- 端口真相源五处收敛（中工程量，涉及多项目脚本改造）
- Go 工具 HTTP_PROXY 隐患验证
- Phase 8 P8.3 xray 套件接入（规划滞后需核实重写）

### Phase 10: 测试流畅性改进 ✅
> **创建**: 2026-08-18（Phase 9 验证过程中踩到的摩擦点，用户裁定并行实施）
> **实施**: workflow 4 agent 并行（Run wf_9faabef5-5e8，238K tokens / 83 tool calls / 4.9 分钟）+ 主线程统一验证。

#### P10.1 报告与历史改进（agent A）✅
- [x] `extract_failure_lines` — FAIL/ERROR suite 的终端输出（红色行）、`--json-output`、MCP `zigtester_run` 响应均含 failure_lines（主线程端到端实测：终端+JSON 均正确输出 ✗ 行）
- [x] CLI `run --no-history`；`check_regression` 基线只统计 PASS（PASS 过滤原本已存在，补无 status 字段兼容）
- [x] `detect_flaky(window=8, 翻转≥2)` → `zigtester_history` 响应 flaky 字段 + 终端标注
- [x] `tests/test_report_history.py` 15/15

#### P10.2 运行时鲁棒性（agent B）✅
- [x] `_heal` 延迟复检（`_HEAL_STABILITY_DELAY=2.5s`，防死亡窗口假阳性）
- [x] `run_workspace` 分组并行 — 无插件组 ThreadPool 并行（结果按传入顺序输出）+ 有插件组串行；fail_fast 语义保持
- [x] `tests/test_env_guard.py` +2 用例（unstable 插件自愈 fast fail / 分组执行）→ 14/14

#### P10.3 zigbox scenarios fast fail（agent C）✅
- [x] 每组合结束后重探 echo 四端口；失联 → FATAL + 指引 zigtester + 兼容 parser 的总计行 + exit 1
- [x] 实测：失联注入 5.4s 退出（原 238s）；正常路径 10.6s 全绿；无残留进程

#### P10.4 zigroute + zigunicfg 审计（agent D）✅
- [x] 发现 a 类直跑指引 5 处（README/CLAUDE.md/roadmap.md）全部修订；b 类 0；c 类假绿 0（exit(1) 均正确）
- [x] 两项目无插件依赖 → 铁律节用收敛式写法（zig build test 仅限纯单元快速迭代）
- [x] zigunicfg comprehensive 训练流水线保留手动例外（外部二进制+自有 Docker，与共享插件无关，铁律节注明例外理由）
- [x] 顺带修正 zigroute CLAUDE.md 层级描述（unit+performance → unit，与 yaml 一致）

#### P10.5 主线程统一验证 ✅
- [x] 单测 29/29（env_guard 14 + report_history 15）；全模块 py_compile OK
- [x] `--all unit --parallel` 8 项目全绿（分组并行生效）
- [x] failure_lines 端到端（终端红行 + JSON 数组）✓；zigbox functional 3/3 ✓
- 注意：MCP Server 常驻进程需重启才加载 server.py 的 failure_lines 改动

#### 本次不做（记录遗留）
- 端口真相源五处收敛（zigtester CLAUDE.md / plugin.yaml ports / 四项目 tests/lib/config.py → plugin.yaml 单源）— 后续独立任务，涉及多项目脚本改造
- Go 测试工具 HTTP_PROXY 隐患（grpc-verify 等若用 net/http 默认 ProxyFromEnvironment）— 遇「端口在听却连不上」先查此项

### Phase 9: 测试环境统一治理 + pre-flight 自检 ✅
> **创建**: 2026-08-18 | **完成**: 2026-08-18
> **背景**: 兄弟项目（AI agent 会话）反复绕过 zigtester 直接跑测试脚本，导致 local-echo/sing-box/xray 插件被反复启停/误杀，测试环境被破坏、结果误判。2026-08-17 已把测试脚本从"自启动 echo"改为 detect-and-error，但文档层和部分入口仍有漏洞，且 zigtester 自身在执行每个 suite 前缺少环境自检与自愈。

#### P9.1: 全面调研 ✅
- [x] 各兄弟项目文档+脚本+skill 绕过入口清点（结论见 findings.md § Phase 9 调研）
- [x] zigtester 现有链路分析（check_port_conflicts 只在启动前做一次；中途被外杀不检测 = 误判根源之一）

#### P9.2: zigtester pre-flight 自检 + 自愈 + fast fail ✅
- [x] `plugin.py` `PluginManager`（prepare 预检+残留清理 / ensure_ready 每套件自检自愈 / stop_all）
- [x] `verify_plugin` 三层校验：进程存活 + readiness 端口 + **端口归属进程树**（lsof TCP LISTEN+UDP bound 占用者 ⊆ 插件进程树；"可连但 lsof 不可见"= root 残留）
- [x] 残留识别安全规则：只 pkill stop.kill 名单 + `*.py` 脚本名特征（绝不取解释器/子命令 token）
- [x] 恢复失败 fast fail：首个 suite ERROR + 剩余 SKIP + 「测试环境规范」输出（`env_spec_message`）
- [x] 顺带修复：HTTP_PROXY 劫持 localhost API（singbox_ctl `_http.trust_env=False`）；local-echo plugin.yaml 补 ports 声明；run_project 端口冲突时空结果误判 bug；**--parallel 多插件项目强制降级串行**（否则互杀）
- [x] 单测 `tests/test_env_guard.py` 12/12

#### P9.3: 兄弟项目文档统一修订 ✅
- [x] zigoutbounds：test_engine.py 删 standalone 自启 sing-box → detect-and-error；CLAUDE.md 方式2/3 删除+铁律节；README/API.md/SKILL.md 同步；singbox.py 修 urllib 代理坑+文案；h2/h3 runner 报错文案
- [x] zigbox：config/README.md 直跑清单+手动 sing-box run 删除；test_tun_scenarios.py FATAL 文案；CLAUDE.md 铁律节
- [x] zigfoundation/zigdns/zigproxy/zigtun：CLAUDE.md 铁律节（zigfoundation 收敛"直接 zig build test"措辞）；zigtun 两处"直接运行"块改 zigtester；zigproxy 过期描述修正；zig-codegen.md 措辞；各 README 提示
- [x] zigdns 假绿修复：test_client/test_forward/test_server detect-skip `exit(0)` → `exit(1)`

#### P9.4: 验证 ✅
- [x] 单测 12/12；zigbox functional 3/3；zigoutbounds functional 7/7；全 workspace unit 8/8（并行自动降级串行）
- [x] 真实残留接管：环境有 PID 18973 残留 local-echo → prepare 自动清理接管 → 3/3 过
- [x] 中途外杀插件（kill -9）→ 后续套件前自愈 → 剩余全过
- [x] 无关进程占插件端口（blocker 监听 13338）→ 精确报"未知进程 PID"+ 不误杀 + fast fail 规范输出
- [x] 预存 flaky 记录（非本次引入）：zigproxy test-engine burst 用例高负载下 2/17 挂（timing 敏感）；zigdns client"多域名 miss"FakeIP 全命中挂

#### 遗留（用户决策/后续项目内修）
- zigproxy burst 用例负载敏感 flaky → zigproxy 项目自修（阈值或负载自适应）
- zigdns client "多域名 miss" FakeIP 缓存行为 → zigdns 项目自修
- 根因记录见 findings.md

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
| ~~不修改 zig/zig-async 语言技能~~ → 已全局删除 | 语言技能与测试框架职责正交，后因冗余移除（2026-08-20） |
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
- ~~zig/zig-async-skill（语言技能）~~ → 已删除

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

---

# Task Plan: 压测治理 — per_suite_only 字段（禁止 --level 全量压测）

> **创建**: 2026-08-22
> **状态**: 🔄 进行中
> **关联**: zigoutbounds zigtester.yaml / runner.py / config.py / schema.json

## 背景

zigoutbounds 用户要求「不要一次做全量压测，只能逐个协议做压测」。根因：
`--level performance` 一次运行该 level 全部 25 个 per-protocol 套件（2026-08-22 实测
321.6s、6 FAIL）。schema 无任何字段可禁止 level 全量 —— 需在框架加套件级约束。

## 方案

`per_suite_only: true`（套件级布尔）：
- `--suite <name>` 显式指定 → 正常运行
- `--level X` 全量执行（suite_filter=None）→ 自动 SKIP + message 提示「仅允许 --suite 单独运行」

改动文件：schemas/zigtester.schema.json / config.py / runner.py / reporter.py / server.py + zigoutbounds zigtester.yaml

## 步骤

- [x] schema.json 加 per_suite_only
- [x] config.py SuiteConfig + _parse_suite
- [x] runner.py run_project 全量时跳过
- [x] reporter/server 列表标注
- [x] 单元测试 tests/test_per_suite_only.py（4/4）
- [x] zigoutbounds 25 套件应用（25/25，14 tcp + 11 stream）
- [x] CLI 验证（全量 25 SKIP 2.3s / --suite 单跑 PASS）+ 重启常驻 9020 server 生效

## 验证结果（2026-08-22）

- `zigtester run --level performance` → 25 SKIP，2.3s（原 321.6s），每条 message 提示「仅允许 --suite 单独运行」
- `zigtester run --level performance --suite bench-tcp-direct` → 单跑 1 套件 PASS（p99=11.4ms）
- zigtester 自身单测：test_per_suite_only 4/4 + test_report_history 15/15 + test_env_guard 15/15
- MCP 端（9020 server）待重启后 curl 验证 per_suite_only 字段下发

---

# Task Plan: 资源采集只采目标被测程序（target 字段）

> **创建**: 2026-08-23
> **状态**: ✅ 完成
> **触发**: zigoutbounds 全量测试复盘——用户指出当前 monitor 采「测试命令整棵进程树」（python 包装 + test-engine 之和），要求**只采目标被测程序**（test-engine 进程自己），不含包装进程/插件进程。
> **关联**: monitor.py / config.py / runner.py / 各项目 zigtester.yaml

## Goal

ResourceMonitor 的资源采集对象从「命令进程树（root + 全部递归子进程）」改为「**目标被测程序单进程**」。新增 suite 级 `target` 字段（进程名匹配），未声明时回退采命令进程（root 单进程）。

## 方案

1. `config.py`：SuiteConfig 加 `target: str | None = None`；`_parse_suite` 解析 `raw.get("target")`
2. `monitor.py`：`start(pid, target=None)`；`_collect()` 目标筛选——
   - `target` 已声明：进程树中按进程名（name/exe basename）匹配，**只采匹配进程**（全部匹配之和）
   - `target` 已声明但树中无匹配 → **跳过本次采样**（目标进程启动期/配错），不采命令进程；整个套件始终未匹配则 stop() 空快照 + warning
   - `target` 未声明 → 只采 root 单进程（命令进程本身）
3. `runner.py`：`monitor.start(test_proc.pid, target=suite.target, ...)`
4. 各项目 yaml：test_engine 驱动类 suite（test-engine-e2e / bench-tcp-* / bench-stream-* / bench-long-* / sweep）加 `target: test-engine`；h2/h3 e2e 加对应二进制名；verify 类（go run，进程名随机）不设 target → 回退单进程

## 行为变化（历史基线影响）

- 现有 suite 资源值将整体**变小**（整树 → 单进程）；泄漏判定（analyze_leak 前后窗口 RSS 差分）基于趋势，仍有效，但绝对值变小
- zigoutbounds 的 test_engine 类 suite **必须加 target: test-engine**，否则采到的是 python 包装而非被测二进制

## 步骤

- [x] config.py SuiteConfig.target + _parse_suite
- [x] monitor.py start(target) + _collect 目标筛选（未匹配跳过采样）
- [x] runner.py 传 suite.target
- [x] zigoutbounds zigtester.yaml 加 target（49 处：test-engine 46 + h3-e2e 2 + h2-e2e 1）
- [x] zigtester 自身单测补 target 用例（test_target_monitor.py 5/5）
- [x] 验证：bench-long-direct 只采 test-engine（psutil 实证 + 采集值吻合）

## 验证结果（2026-08-23）

**实证（psutil 独立探针采样同窗口 test-engine 进程）**：
- test-engine 进程 RSS 全程 6.7→8.1MB（压测 10s 稳定 8.1MB，fd=24）
- python 包装进程（test_engine.py）RSS = 33.6-34MB
- monitor 首版（fallback root）报 peak=34MB → **实为 python 包装被误采**；avg=9.3（少数启动期样本污染）

**根因发现（launchd.log fallback warning 实锤）**：test_engine.py 启动后需先导入库/解析配置才 Popen 启动 test-engine，**启动期 1-2s 内 target='test-engine' 在树中不存在** → 旧 fallback 逻辑采命令进程 root（python 34MB）污染 peak。

**修复**：target 未匹配 → 跳过本次采样（`self._n_skipped=1` 推进启动期），不采命令进程；stop() 时 target 声明但 sample_count==0 → warning 暴露配置错误。单测 5/5（新增「目标延迟出现只采目标」用例）。

**修复后 bench-long-direct**：peak 8.2 / avg 8.1 / min 7.8MB，peak_fd 24，peak_cpu 162% —— 与 psutil 实测完全吻合，launchd.log 无 fallback warning。

## 遗留

- ~~`schemas/zigtester.schema.json` 补 `target` 字段~~ ✅ 已补（2026-08-23）

## 追加：兄弟项目 target 补齐（2026-08-23）

用户质询「其他兄弟项目也自动适配了吗」→ 结论分两层：框架自动生效（不再整树），但 yaml target 声明需逐项目补。已为 5 个项目补 48 处：

| 项目 | 套件 | target |
|------|------|--------|
| zigbox | bench-socks5/http/direct/long | `zigbox` |
| zigdns | bench-alloc-v4/lookup-v4/alloc-v6/pool | `zigdns-harness` |
| zigproxy | test-engine/-tun/-vtun | `test-engine` |
| zigproxy | bench-socks5/http/long-conn | `zigproxy` |
| zigtun | vtun-* 4 个 | `test-engine` |
| zigtun | tun-*/sys-*/lwip-*/edge-*/monitor-*/routes-*/auto-*/strict-*/bridge-*/mixed-*/transparent/perf-nat/bench-*（27 个）| `zigtun-test` |
| zigunicfg | test-engine-parse | `zigunicfg` |
| zigunicfg | test-engine-adapt | `zigbox`（跨项目产物）|

**关键坑**：命令经 `shlex.split` + 非 shell exec，suite.sudo=True 时前置 `sudo`；命令自带 `sudo` → 双重 sudo，**命令进程 = sudo**。zigtun 的 `sudo zig-out/.../zigtun-test` 直跑套件若不声明 target 会采 sudo 进程（rss≈0）——必须 target 匹配被测二进制。

**实证**：zigdns bench-alloc-v4（探针对照）——target 生效采 `zigdns-harness`：peak 8/avg 7.8/min 4MB，cpu 97% 密集，与 psutil 探针实测（harness 5-8MB 爬升稳定）完全吻合。顺带修了 min=0 瑕疵（target 首次出现时仍走首样本跳过，不再采启动瞬间 rss≈0）。

**未补 target 的 suite**（不启动独立被测二进制 或 未实证）：zigbox functional（scenarios 等）、zigproxy bench-direct（纯 python 直连）/protocols/doh、zigdns functional、zigunicfg ir-snapshots/gen。这些未声明 target → 采命令进程单进程（python），不混入插件。
