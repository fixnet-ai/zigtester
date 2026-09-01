# zigtester 项目规划

> **架构设计唯一来源：[DESIGN.md](DESIGN.md)。** 本文档只做执行计划，不重复设计。
>
> **规划瘦身（2026-08-23）**：Phase 1-12 详细执行记录已压缩为「历史完成阶段总表」——每阶段一行
> （编号｜标题｜一句话结果｜锚点）。历史决策完整推导见 git log + DESIGN.md；
> 同步二次瘦身：findings.md 440→72 行、progress.md 405→46 行（久远流水/错误方向已删，
> 技术定论已在代码头部注释，findings 只留指针表）。**已删章节锚点一律以 git log 为准。**
> 本文档只承载仍在生效的决策；未完成任务已移交 zigbox 统一规划（zigbox task_plan.md『跨项目统一待办』）。
>
> **第四轮瘦身（2026-08-27）**：「当前状态」与「A/B/C/D 已完成」合并去重；遗留节压缩为
> 跨项目指针；findings §9 端口清单改为指向 plugins/*/plugin.yaml（唯一真相源）、
> §10 诊断长文凝练为最终定论 + 被推翻假设移入「错误方向记录」。

## ✅ 已完成：性能测试架构重构（A/B/C/D，2026-08-25）

> **背景**：zigoutbounds 完工后，zigbox 默认性能测试（`--level performance`）仍只跑 4 个轻量协议
> direct 长连接，覆盖不足。用户裁定：默认跑 2 标准场景，压力/疲劳/分流移独立 `performance-scenarios`
> 层级；本改造归 zigtester 项目推进（改动的 zigbox 文件由本项目分支负责）。调研见 findings §9。
> 与「关键生效决策 · 压测禁止 `--level` 全量」不冲突：per_suite_only 约束的是默认 performance 层，
> 独立层级内全量可跑是本次新裁定。

- **A 参数透传**：`--args` 追加命令尾部 + 非标准参数不存历史（决策 D1 互斥）；test_args_passthrough 6/6
- **B run 自动回归对比**（MCP-only）：save 前对 performance/performance-scenarios PASS+metrics 套件算
  check_regression；过滤泄漏键（D2）；bench-long-socks5 端到端正确渲染 4 条红字回归
- **C history 固定报表**：compact_history 三态 + print_history markdown 分支 + 组视图按成员预计算；
  test_report_history 24/24
- **D performance-scenarios 层级**：VALID_LEVELS/schema/list 3 处；zigbox 12 旧套件迁入 +
  analyze_leak 显式生效（bench-long-socks5 metrics 含 rss_growth/fd_growth）；
  **跨层级基线保留**（旧 performance 历史仍作回归基线，runs 按 (project,suite) 键不丢）

**验收结果**：
- `zigtester_list zigbox` 确认 4 层级（unit/functional/performance/performance-scenarios）✓
- `bench-standard-inbound` PASS（641 req/s, p99 2.8ms）✓
- `bench-long-socks5`（performance-scenarios）PASS：7922 req/s, p99 6.4ms, 0 错误；
  analyze_leak 显式生效 + 跨层级基线（旧 performance 层历史 10781 req/s 作回归基线）✓
- MCP 端到端：run 返回 4 层级 + regressions 字段 + report 回归节；history 单套件/组视图 report + 空态 ✓

**附带修复**：
- `_is_metric_regression` 吞吐关键词补 `req_s`（req_s 上升 136.8% 曾被误标回归）
- 回归检测 duration 归一化（08-25）：总量指标（success_count/total_requests/error_count）按
  duration_s 归一化到每秒速率再对比，防 `--duration` 40s→10s 被误判为吞吐回归
  （hy2 success_count 266471@40s → 65594@10s 误报 -40%，实际 req/s 稳定 ~6.6k 无退化）。
  test_report_history 24→27 全绿。
- 回归检测基线三重过滤（08-26，zigdns 审查发现）：短 duration 启动瞬态 / 陈旧记录时间窗口 /
  延迟亚毫秒噪声 guard → `_is_short_duration`/`_is_stale`/`_is_latency_metric`；test_report_history
  27→32 全绿。详见 progress.md 近期定论。

## ✅ 已结案：bench-standard-outbound socks/socks-xray 连接泄漏（2026-08-25 修复）

> 处置进展（08-25，根因/修复见 zigoutbounds commit `481ed07`）：
> - **EOF 语义定案**：重量出站 30s 挂起 = HTTP 客户端等 EOF 而 sing-box 不转发上游 FIN →
>   CL 语义（`test_bench.read_by_cl` 默认开）后全部重量出站 PASS ✅
> - **ss-xray 剔除（用户裁定）**：zigbox SS 客户端 ↔ xray 2022-blake3 握手不兼容，剔除非修复 ✅
> - **redirect 平台守卫已落地**：`bench_outbound_cells()` 非 Linux 不枚举（macOS 为平台不支持）✅
> - **socks/socks-xray fd 耗尽 ✅ 已修复（zo `481ed07`）**：根因 = zo `socks5.zig` `workerMain`
>   内联 `sess.drive()` 从不调 `pollSessions()`（唯一回收器）→ closed session 永驻 map，
>   srv fd + tun fd 每请求泄漏 2-3 个 → ~679 请求后 fd 耗尽。修复 = 改 `pollSessions()`。
>   验证：`bench-standard-outbound PASS peak_fd=16`（修复前 fd-exhausted）。完整诊断见 findings §10。

## 当前状态（2026-08-28）

- 分支 `main`，全部 Phase 1-12 完成（2026-08-07 → 08-21）+ A/B/C/D 性能架构重构完成（08-25）。
- zigtester 自身单测全绿：test_args_passthrough 6 + test_env_guard 15 + test_per_suite_only 4 +
  test_report_history 32 + test_runner_env + test_plugin_ports + test_target_monitor。
- 兄弟项目全部接入 zigtester：zigfoundation/zigbox/zigtun/zigproxy/zigdns/zigoutbounds +
  zigroute/zigunicfg。
- **进行中：local-cf-dev 插件（Phase 13）**——见下节。

## Phase 13：local-cf-dev 插件（2026-08-28，进行中）

> 目标：本地部署 CF Workers/Pages 代理（yonggekkk/Cloudflare-vless-trojan 的 VLESS/Trojan-over-WS
> worker），经 `wrangler dev`（workerd 运行时）离线跑 zigbox/zigoutbounds 的 VLESS/Trojan+WS(+TLS)
> 协议 E2E。调研定论见 findings §11（含 ECH 本地不可测 + IPv4 目标 sslip.io 重写等关键事实）。

### 落地状态（2026-08-28）

**已落地（离线验证通过）**：
- `plugins/local-cf-dev/plugin.yaml`（config 端口/凭证真相源 + build + lifecycle）
- `plugins/local-cf-dev/cfdev_ctl.py`（serve/render 模式，同 singbox_ctl/xray_ctl 惯式）
- `plugins/local-cf-dev/README.md`（用法 + ECH 局限 + 离线数据路径）
- `plugins/plugin_ports.py` 增 `cfdev_*` 派生函数（`cfdev_workers_port`/`cfdev_pages_port`/
  `cfdev_uuid`/`cfdev_trojan_password`/`cfdev_local_protocol`）

**已验证**：py_compile 通过；`parse_plugin_config` + `discover_plugins` 正确解析/发现；
`render` 模式正确渲染 wrangler.toml + .dev.vars + 拷贝 worker（vendor 路径解析到
`fixnet/vendor/Cloudflare-vless-trojan/Vless_workers_pages/_worker明.js`）；plugin_ports 6 派生值正确。

**剩余（待办，勿在本次混入）**：
- 实测 `wrangler dev` 启动（首跑需网络拉 workerd 二进制，npx 缓存后离线）——需人工跑一次确认端口 18787 就绪。
- 消费方接入：zigbox/zigoutbounds `zigtester.yaml` 加 `plugins: [local-cf-dev]` + 相应 VLESS/Trojan+WS 套件
  （客户端目标地址须用域名 `localhost`，见 findings §11 C.4）。
- ECH：本地不可测，文档化于 README；如需测 ECH 属「真连 CF 边缘」另一类场景，非本插件范围。

### 设计

**插件形态**（复用 sing-box/xray 的 `serve` ctl 模式）：
```
zigtester/plugins/local-cf-dev/
  plugin.yaml        # name/config/ports/build/lifecycle
  cfdev_ctl.py       # 渲染 wrangler 项目 + 启 wrangler dev（serve 模式）
  README.md          # 用法 + ECH 局限说明
```

**plugin.yaml config 段（端口/凭证唯一真相源）**：
- `workers_port: 18787` / `pages_port: 18788`（避开官方 8787/8788）
- `uuid: 86c50e3a-5b87-49dd-bd20-03c7f2735e40`（与 worker 默认一致）
- `trojan_password: trojan`
- `local_protocol: http|https`（TLS 模式切 https）
- `vendor_worker_dir`（worker 源路径，默认 `../vendor/Cloudflare-vless-trojan`）
- `echo_host: 127.0.0.1`（VLESS 目标域名 `localhost` 解析到的本地 echo）

**lifecycle**：`build.command = "npx wrangler --version"`（存在性检查，同 sing-box/xray 模式）；
`start = python3 cfdev_ctl.py serve`；`ready_on: tcp 127.0.0.1:18787`。

**cfdev_ctl.py 职责**：
1. 把 `vendor/.../Vless_workers_pages/_worker明.js` 拷到临时 workdir（`/tmp/zigtester-cfdev/`）
2. 渲染 `wrangler.toml`（`main`/`compatibility_date`/`[dev] port+local_protocol`）+ `.dev.vars`（uuid/proxyip）
3. `subprocess.Popen("npx wrangler dev ...")` 阻塞直到停止信号（同 singbox_ctl serve）
4. 探活：`wrangler dev` 就绪后 TCP 探测 workers_port

**E2E 数据路径**（离线闭环）：
zigbox vless+ws 客户端 → 127.0.0.1:18787（workerd）→ worker 解析 VLESS 头 →
`connect({hostname:"localhost"})` → 127.0.0.1:13333（local-echo echo）→ 回程。

**边界/待办**：
- ECH 本地不可测（findings §11 C.5）——插件覆盖 WS+TLS（非 ECH）；ECH 文档化。
- ~~首跑需一次网络拉 workerd 二进制~~ → **已实测（08-28）无需网络**：workerd 已在 `~/.npm/_npx/`
  缓存（`npx wrangler --version` 时已拉取）。wrangler dev 三层验证全绿（render/冒烟/relay），
  详见 findings §11 C.7。
- 消费方接入（剩余）：zigbox/zigoutbounds `zigtester.yaml` 加 `plugins: [local-cf-dev]` + 相应套件；
  plugin_ports.py 已增 `cfdev_*` 派生函数（`cfdev_workers_port()` 等 5 个）。

## 开放待办（2026-08-30 起，zt-6）

### zt-6：long 套件后 zigbox 残留未清理 → 环境自愈假失败（2026-08-31 修复落地）

> **来源**：2026-08-30 zigbox 全协议性能重测发现（zigbox findings「全协议性能重测与 zigtester 环境自愈 bug」段，commit b042651）。该 bug 属 zigtester 项目，由本项目跟踪。

**现象**：每次 long 套件（bench-long-*，62-120s）结束后，被测 zigbox 进程在 zigtester 生命周期结束后**仍残留并 listen 12080 端口** → 下一个套件执行前环境 self-heal 检测到端口冲突 → **SIGTERM local-echo**（exit=-15）→ local-echo 恢复不完整（partial recovery）→ 后续套件**假失败**。

**实际影响（08-30 实测）**：bench-long-socks4 FAIL ×2（1677-2595 req/s、err=40、cpu_tail=0、fd_growth 19-21），一度误判为代码回归并做了 revert A/B（早前 PASS 为环境恢复巧合）；干净环境重测 PASS。

**修复落地（2026-08-31，方案 1）**：runner.py 进程组兜底清理——
- `_start_process` 给 test command 加 `start_new_session=True`（独立进程组，组 id = 命令进程 pid）；
- `execute()` finally 末尾（teardown 之后）新增 `_kill_test_proc_tree(test_proc)`：SIGTERM → 2s → SIGKILL
  覆盖整组。命令进程退出后残留后代变孤儿（ppid=1）仍属原进程组 → killpg 按组 id 兜底，无需追踪 ppid；
  插件进程独立进程组不受影响（不误伤 local-echo）。
- **单测**：`tests/test_runner_cleanup.py` 2 用例（spawn 孤儿被清理 + 普通命令无副作用），2 passed。
- **集成验证**：重启服务（launchctl kickstart）后经 zigtester 跑 `bench-socks5` PASS（2825 req/s）+
  `bench-long-socks5` PASS（73.3s, 7021 req/s, p99 6.2ms, rss_growth 0.1MB, fd_growth -1，无回归）；
  两套件结束后 `lsof 12080` 均 FREE、无 zigbox 进程——**zt-6 假失败同类 long 套件闭环**。

**zigbox 侧治本（08-31 已落地，test_bench.py）**：根因 = main() 普通 bench 路径与 run_standard_inbound
从不 stop zigbox（L1769「供后续测试复用」注释与 `_zigbox_ensure_running` 每次强制重启的实际行为矛盾）。
修复 = `_zigbox_ensure_running` 返回 `ZigboxProcess` 对象（不再丢句柄）+ main() 外层 try/finally 统一
`zb.stop()`（含 concurrency sweep 分支）+ run_standard_inbound try/finally `zb.stop()`。验证：
bench-standard-inbound（run_standard_inbound 路径）PASS 740 req/s + bench-socks5（main 路径）PASS
2796 req/s，两套件结束后 12080 FREE、无 zigbox 进程。zigtester 兜底保留（防第三方脚本残留）。

**教训已入 zigbox findings**：FAIL 排查先查环境自愈残留 / SIGTERM / 端口占用，再归因代码；A/B 必须保证环境隔离。

### zt-9：launchd ProcessType Background → Interactive（zigoutbounds unit 编译被杀 806M，09-01 收尾）

> **来源**：zigoutbounds unit 持续 FAIL exit=1（08-29 起历史 10 连），详见 zigbox task_plan.md「跨项目统一待办」zigoutbounds B-① 附带发现 + zigbox task_plan zt-9 行。

**现象**：`zigtester_run zigoutbounds --level unit` stderr `1m MaxRSS:806M`~`923M` = zigoutbounds-tests **编译**被杀（pool_tests 35M 正常）。**对照实验（非代码 bug）**：本地直跑 `zig build test` 437/437 + 20s + 编译 MaxRSS:1G 正常完成——编译主测试二进制本就需 ~1GB 峰值；zigtester MCP 服务以 launchd `ProcessType: Background`（autostart.py:93 硬编码）运行，该进程及子进程继承 Background **jetsam 内存配额 + 降调度**（同源 `1m` vs `17s`），编译至 ~900M 即被杀（系统内存 48% free、rlimit 无限、无 jetsam 压力记录）。

**修复（09-01）**：`autostart.py` `ProcessType: Background` → `Interactive`（MCP 是交互式测试入口，Background 类型不匹配）+ 注释说明根因 + `zigtester install --dir fixnet` 重装（launchd kickstart 重启 MCP 服务）。

**验证**：`zigtester_run zigoutbounds --level unit` **435/435 PASS ×2**（11s/16s，此前 1m + exit=1）。**教训**：MCP/测试服务进程类型必须匹配其工作负载（派生大编译），Background 隐含内存/调度上限。

## 历史完成阶段总表（Phase 1-12，全部完成）

> 执行细节与决策推导见 git log + DESIGN.md + 代码头部注释（findings 二次瘦身后只留指针表）。

| 阶段 | 标题 | 一句话结果 | 锚点 |
|------|------|-----------|------|
| 1 | zigbox 试点替换 | tests skill → stub（-81%）、zigbox-outbound-dev 删除；token -83% | git log |
| 2 | zigtester.yaml 全覆盖 | 为 zigtun/zigproxy/zigdns 生成配置；scan 可发现全部项目 | git log |
| 3 | 简单项目接入 | 各项目 CLAUDE.md 补「测试」节指向 zigtester MCP | git log |
| 4 | zigoutbounds skill 替换 | tests → stub（-89%）、outbound-dev 删除迁 CLAUDE.md | git log |
| 5 | 最终验证 | scan 全 6 项目 + `--all --level unit` 全绿；token 合计 -93% | git log |
| 6 | 测试生命周期管理 | setup/teardown + readiness probe + 插件体系 + local-echo 迁移（Python→Go） | git log |
| 7 | sing-box 插件 | 统一配置 test_server.json + serve 直启（Clash API 热重载不可行）+ 端口表 | git log + singbox_ctl.py |
| 8 | xray-core 插件 | 插件本体 + 跨插件端口冲突检测；P8.3 zigoutbounds 接入见 zigbox task_plan.md『跨项目统一待办』 | git log + xray_ctl.py |
| 9 | 环境统一治理 + pre-flight | PluginManager 三层校验 + 自愈 + fast fail + 生态铁律 | git log + plugin.py |
| 10 | 测试流畅性改进 | failure_lines / flaky 检测 / 分组并行 / scenarios fast fail | git log |
| 11 | flaky 根因修复 + 插件管道排空 | zigproxy 客户端超时缺陷 + zigdns FakeIP 期望 + PIPE drain | git log |
| 12 | 插件 host 化 | 远程插件模式（host 优先级：plugin_ref > plugin.yaml > env > 127.0.0.1） | git log + plugin.py |

**关键生效决策（最高优先级，勿改）**：

- **测试环境治理铁律（生态级，用户裁定）**：所有 zig* 项目测试一律经 zigtester 执行；兄弟项目测试脚本只探测依赖（detect-and-error），绝不自启 / 手动启停 echo/sing-box/xray；环境异常时唯一正确动作 = 重新 `zigtester run`（自愈恢复）。
- **访问 127.0.0.1 的 HTTP 客户端必须禁用代理**（requests `trust_env=False` / urllib `ProxyHandler({})`）——本机 HTTP_PROXY 会劫持 localhost 请求导致假判「插件未运行」。
- **MCP 长任务 = SSE 流 + progress 心跳**（`json_response=False`）；**HTTP transport 端口绑定天然互斥** 杜绝多实例僵尸进程。
- **压测禁止 `--level` 全量**：`per_suite_only` 套件只允许 `--suite` 单独运行。
- **资源采集 `target` 字段**：只采目标被测程序，未匹配跳过采样（防 python 包装进程污染）。

## 测试门禁

`zig build test` 全绿 → 集成 E2E（sing-box / xray）→ 性能（按需）。单请求 ≤100ms、全量 1 分钟内跑完。
详见 DESIGN.md 与用户级 CLAUDE.md § 测试脚本可用性。
