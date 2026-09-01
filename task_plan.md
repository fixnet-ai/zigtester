# zigtester 项目规划

> **架构设计唯一来源：[DESIGN.md](DESIGN.md)。** 本文档只做执行计划，不重复设计。
>
> **规划瘦身（2026-08-23 起多轮）**：Phase 1-12 详细执行记录已压缩为「历史完成阶段总表」（每阶段一行）；
> 历史决策完整推导见 git log + DESIGN.md + 代码头部注释（findings 只留「定论位置表」指针）。
> **已删章节锚点一律以 git log 为准。** 本文档只承载仍在生效的决策；未完成任务已移交
> zigbox 统一规划（zigbox task_plan.md『跨项目统一待办』）——本文件开放待办仅保留不在统一待办中的项。

## 当前状态（2026-09-01）

- 分支 `main`，全部 Phase 1-12 完成（2026-08-07 → 08-21）+ A/B/C/D 性能架构重构完成（08-25）。
- zigtester 自身单测全绿：test_args_passthrough 6 + test_env_guard 15 + test_per_suite_only 4 +
  test_report_history 32 + test_runner_cleanup 2 + test_runner_env/test_plugin_ports/test_target_monitor。
- 兄弟项目全部接入 zigtester（zigfoundation/zigbox/zigtun/zigproxy/zigdns/zigoutbounds + zigroute/zigunicfg）。
- **zt-6/zt-7/zt-8/zt-9 全部闭环**（见「开放待办（历史）」）；**进行中：local-cf-dev 插件（Phase 13）**。

## ✅ 已完成：性能测试架构重构（A/B/C/D，2026-08-25）

> 背景：zigbox 默认 `--level performance` 只跑 4 个轻量 direct 长连接，覆盖不足。用户裁定：默认跑
> 2 标准场景，压力/疲劳/分流移独立 `performance-scenarios` 层级。调研见 findings §9（端口真相源 =
> `plugins/*/plugin.yaml`）。与「压测禁止 `--level` 全量」不冲突（per_suite_only 约束默认 performance 层）。

- **A 参数透传**：`--args` 追加命令尾部 + 非标准参数不存历史（决策 D1 互斥）；test_args_passthrough 6/6
- **B run 自动回归对比**（MCP-only）：save 前对 performance/performance-scenarios PASS+metrics 套件算
  check_regression；过滤泄漏键（D2）；bench-long-socks5 端到端渲染 4 条红字回归
- **C history 固定报表**：compact_history 三态 + print_history markdown 分支 + 组视图按成员预计算；
  test_report_history 27→32 全绿
- **D performance-scenarios 层级**：VALID_LEVELS/schema/list 3 处；zigbox 12 旧套件迁入 +
  analyze_leak 显式生效；**跨层级基线保留**（runs 按 (project,suite) 键不丢）

**验收**：`zigtester_list zigbox` 4 层级 ✓；bench-standard-inbound PASS（641 req/s, p99 2.8ms）✓；
bench-long-socks5 PASS（7922 req/s, p99 6.4ms）+ analyze_leak 显式生效 + 旧 performance 层基线回归 ✓；
MCP run 返回 4 层级 + regressions 字段、history 单套件/组视图 ✓。

**附带修复**：`_is_metric_regression` 吞吐关键词补 `req_s`；回归检测 duration 归一化（08-25，总量指标
按 duration_s 归一为每秒速率再对比，防 `--duration` 调整误判）；回归检测基线三重过滤（08-26，
短 duration 瞬态/陈旧窗口/延迟噪声 → `_is_short_duration`/`_is_stale`/`_is_latency_metric`，27→32 全绿）。

## ✅ 已结案：bench-standard-outbound socks/socks-xray 连接泄漏（2026-08-25）

> 根因/修复在 zigoutbounds commit `481ed07`（zo `socks5.zig` `workerMain` 内联 `sess.drive()` 从不调
> `pollSessions()` → closed session 永驻 map，每请求泄漏 2-3 fd → ~679 请求后耗尽）。完整诊断见 findings §10。

- **EOF 语义定案**：重量出站 30s 挂起 = 上游 FIN 不转发；CL 语义（`test_bench.read_by_cl` 默认开）后全 PASS。
- **ss-xray 剔除（用户裁定）**：zigbox SS 客户端 ↔ xray 2022-blake3 握手不兼容，剔除非修复。
- **redirect 平台守卫**：`bench_outbound_cells()` 非 Linux 不枚举（macOS 平台不支持）。
- **fd 耗尽已修（zo `481ed07` → `pollSessions()`）**：验证 `bench-standard-outbound PASS peak_fd=16`。

## Phase 13：local-cf-dev 插件（2026-08-28，进行中）

> 目标：本地部署 CF Workers/Pages 代理（yonggekkk/Cloudflare-vless-trojan 的 VLESS/Trojan-over-WS worker），
> 经 `wrangler dev`（workerd 运行时）离线跑 zigbox/zigoutbounds 的 VLESS/Trojan+WS(+TLS) 协议 E2E。
> 调研定论（ECH 本地不可测 / IPv4 目标 sslip.io 重写 / 127.0.0.1 非 localhost / workerd 支持 connect()）见
> findings §11 + `plugins/local-cf-dev/README.md`。

**已落地（离线验证通过，08-28）**：`plugin.yaml`（端口/凭证真相源）+ `cfdev_ctl.py`（serve/render）+ `README.md`
+ `plugin_ports.py` 增 `cfdev_*` 派生函数。py_compile 通过；render 正确渲染 wrangler.toml + .dev.vars + 拷贝
worker（vendor `Cloudflare-vless-trojan`）；wrangler dev 三层验证全绿；workerd 已缓存 npx，**首跑无需网络**。

**设计**（复用 serve 模式）：
```
zigtester/plugins/local-cf-dev/
  plugin.yaml   # workers_port 18787 / pages_port 18788 / uuid / trojan_password / local_protocol / vendor_worker_dir
  cfdev_ctl.py  # 拷贝 worker → 渲染 wrangler.toml+.dev.vars → Popen("npx wrangler dev") → TCP 探活 18787
  README.md     # 用法 + ECH 局限
```
`lifecycle.build = "npx wrangler --version"`；`ready_on: tcp 127.0.0.1:18787`。
E2E 数据路径：zigbox vless+ws → 127.0.0.1:18787(workerd) → worker 解析 VLESS 头 →
`connect({hostname:"localhost"})` → 127.0.0.1:13333(local-echo) → 回程。

**开放待办（本文件唯一开放项，勿在本次混入）**：
- **消费方接入**：zigbox/zigoutbounds `zigtester.yaml` 加 `plugins: [local-cf-dev]` + 相应 VLESS/Trojan+WS
  套件（客户端目标地址须用**域名 `localhost`**，见 findings §11 C.4）。
- 实测 `wrangler dev` 启动（端口 18787 就绪）已由三层验证覆盖；ECH 本地不可测，文档化于 README
  （真连 CF 边缘测 ECH 属另一类场景，非本插件范围）。
- 后续 27.2 CF 形态：测 CF 兼容性（early data + 2 字节响应头 `[version,0]`），先实测 zigoutbounds
  vless-ws ↔ CF worker 直通性（zigoutbounds WS 实现已完整，缺测试用例）。

## 开放待办（历史，均已闭环）

### zt-6：long 套件后 zigbox 残留 → 环境自愈假失败（2026-08-31 修复）

> 现象：long 套件结束后 zigbox 仍 listen 12080 → 下套件 self-heal 检测端口冲突 SIGTERM local-echo
> → 后续套件假失败（一度误判代码回归）。来源：2026-08-30 全协议性能重测（zigbox findings commit b042651）。

- **zigtester 侧（08-31，方案 1）**：runner.py 进程组兜底——`_start_process` 加 `start_new_session=True`；
  `execute()` finally 末尾 `_kill_test_proc_tree`（SIGTERM → 2s → SIGKILL killpg 整组，插件独立组不误伤）；
  单测 `test_runner_cleanup` 2 passed；集成 `bench-socks5`/`bench-long-socks5` PASS 且 12080 FREE。
- **zigbox 侧治本（08-31）**：`test_bench.py` `_zigbox_ensure_running` 返回 `ZigboxProcess` 对象 + main()/
  run_standard_inbound try/finally `zb.stop()`（zigtester 兜底保留防第三方残留）。
- **教训**：FAIL 排查先查环境自愈残留/SIGTERM/端口占用，再归因代码；A/B 必须环境隔离。

### zt-9：launchd ProcessType Background → Interactive（09-01 收尾）

> 来源：zigoutbounds unit 持续 FAIL exit=1（08-29 起历史 10 连）。现象：`1m MaxRSS:806M~923M` =
> zigoutbounds-tests **编译**被杀（编译主测试二进制需 ~1GB 峰值）；MCP 服务以 launchd `ProcessType: Background`
> 运行（autostart.py:93 硬编码）→ 继承 jetsam 内存配额 + 降调度（同源 `1m` vs 本地直跑 `17s`）。

- **修复（09-01）**：`autostart.py` Background → **Interactive** + 注释根因 + `zigtester install --dir fixnet`
  重装重启 MCP 服务。**验证**：`zigtester_run zigoutbounds --level unit` **435/435 PASS ×2**（11s/16s）。
- **教训**：MCP/测试服务进程类型必须匹配其工作负载（派生大编译），Background 隐含内存/调度上限。

## 历史完成阶段总表（Phase 1-12，全部完成）

> 执行细节与决策推导见 git log + DESIGN.md + 代码头部注释（findings 只留指针表）。

| 阶段 | 标题 | 一句话结果 | 锚点 |
|------|------|-----------|------|
| 1 | zigbox 试点替换 | tests skill → stub（-81%）、zigbox-outbound-dev 删除；token -83% | git log |
| 2 | zigtester.yaml 全覆盖 | 为 zigtun/zigproxy/zigdns 生成配置；scan 可发现全部项目 | git log |
| 3 | 简单项目接入 | 各项目 CLAUDE.md 补「测试」节指向 zigtester MCP | git log |
| 4 | zigoutbounds skill 替换 | tests → stub（-89%）、outbound-dev 删除迁 CLAUDE.md | git log |
| 5 | 最终验证 | scan 全 6 项目 + `--all --level unit` 全绿；token 合计 -93% | git log |
| 6 | 测试生命周期管理 | setup/teardown + readiness probe + 插件体系 + local-echo 迁移（Python→Go） | git log |
| 7 | sing-box 插件 | 统一配置 test_server.json + serve 直启（Clash API 热重载不可行）+ 端口表 | git log + singbox_ctl.py |
| 8 | xray-core 插件 | 插件本体 + 跨插件端口冲突检测；zigoutbounds 接入见 zigbox 统一待办 | git log + xray_ctl.py |
| 9 | 环境统一治理 + pre-flight | PluginManager 三层校验 + 自愈 + fast fail + 生态铁律 | git log + plugin.py |
| 10 | 测试流畅性改进 | failure_lines / flaky 检测 / 分组并行 / scenarios fast fail | git log |
| 11 | flaky 根因修复 + 插件管道排空 | zigproxy 客户端超时缺陷 + zigdns FakeIP 期望 + PIPE drain | git log |
| 12 | 插件 host 化 | 远程插件模式（host 优先级：plugin_ref > plugin.yaml > env > 127.0.0.1） | git log + plugin.py |

## 关键生效决策（最高优先级，勿改）

- **测试环境治理铁律（生态级，用户裁定）**：所有 zig* 项目测试一律经 zigtester 执行；兄弟项目测试脚本只
  探测依赖（detect-and-error），绝不自启 / 手动启停 echo/sing-box/xray；环境异常时唯一正确动作 = 重新
  `zigtester run`（自愈恢复）。
- **访问 127.0.0.1 的 HTTP 客户端必须禁用代理**（requests `trust_env=False` / urllib `ProxyHandler({})`）——
  本机 HTTP_PROXY 会劫持 localhost 请求导致假判「插件未运行」。
- **MCP 长任务 = SSE 流 + progress 心跳**（`json_response=False`）；**HTTP transport 端口绑定天然互斥** 杜绝多实例僵尸进程。
- **压测禁止 `--level` 全量**：`per_suite_only` 套件只允许 `--suite` 单独运行。
- **资源采集 `target` 字段**：只采目标被测程序，未匹配跳过采样（防 python 包装进程污染）。
- **long 套件残留下代清理**：zigtester 进程组兜底 + 被测项目自身 try/finally stop（zt-6 教训）。

## 测试门禁

`zig build test` 全绿 → 集成 E2E（sing-box / xray）→ 性能（按需）。单请求 ≤100ms、全量 1 分钟内跑完。
详见 DESIGN.md 与用户级 CLAUDE.md § 测试脚本可用性。
