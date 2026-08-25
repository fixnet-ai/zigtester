# zigtester 项目规划

> **架构设计唯一来源：[DESIGN.md](DESIGN.md)。** 本文档只做执行计划，不重复设计。
>
> **规划瘦身（2026-08-23）**：Phase 1-12 详细执行记录已压缩为「历史完成阶段总表」——每阶段一行
> （编号｜标题｜一句话结果｜锚点）。历史决策完整推导见 git log + DESIGN.md；
> 同步二次瘦身：findings.md 440→72 行、progress.md 405→46 行（久远流水/错误方向已删，
> 技术定论已在代码头部注释，findings 只留指针表）。**已删章节锚点一律以 git log 为准。**
> 本文档只承载仍在生效的决策；未完成任务已移交 zigbox 统一规划（zigbox task_plan.md『跨项目统一待办』）。

## 当前状态（2026-08-25）

- **性能测试架构重构（A/B/C/D）全部完成 + 端到端验证**（本会话，workflow wf_34457971-32e）：
  - **A 参数透传**：`--args` 追加命令尾部 + 非标准参数不存历史（决策 D1 互斥）；test_args_passthrough 6/6
  - **B run 自动回归对比**（MCP-only）：save 前对 performance/performance-scenarios PASS+metrics 套件算 check_regression；
    过滤泄漏键（D2）；bench-long-socks5 端到端正确渲染 4 条红字回归
  - **C history 固定报表**：compact_history 三态 + print_history markdown 分支 + 组视图按成员预计算；test_report_history 24/24
  - **D performance-scenarios 层级**：VALID_LEVELS/schema/list 3 处；zigbox 12 旧套件迁入 + analyze_leak 显式生效
    （bench-long-socks5 metrics 含 rss_growth/fd_growth）；**跨层级基线保留**（旧 performance 历史仍作回归基线）
  - **history.py `_is_metric_regression` 修复**：吞吐关键词补 `req_s`（req_s 上升 136.8% 曾被误标回归）
- **history.py 回归检测 duration 归一化（08-25）**：bench-long 总量指标（success_count/total_requests/
  error_count）按 duration_s 归一化到每秒速率再对比，修复 --duration 40s→10s 被误判为吞吐回归
  （hy2 success_count 266471@40s → 65594@10s 误报 -40%，实际 req/s 稳定无退化）。test_report_history
  24→27 全绿。
- **zigtester 自身单测全绿**：test_args_passthrough 6 + test_env_guard 15 + test_per_suite_only 4 + test_report_history 27 + test_runner_env + test_plugin_ports + test_target_monitor。
- **兄弟项目全部接入 zigtester**：zigfoundation/zigbox/zigtun/zigproxy/zigdns/zigoutbounds + zigroute/zigunicfg。

## ✅ 已完成：性能测试架构重构（A/B/C/D，2026-08-25）

> **背景**：zigoutbounds 完工后，zigbox 默认性能测试（`--level performance`）仍只跑 4 个轻量协议
> direct 长连接，覆盖不足。用户裁定：默认跑 2 标准场景，压力/疲劳/分流移独立 `performance-scenarios`
> 层级；本改造归 zigtester 项目推进（改动的 zigbox 文件由本项目分支负责）。调研见 findings §9。
> 与「关键生效决策 · 压测禁止 `--level` 全量」不冲突：per_suite_only 约束的是默认 performance 层，
> 独立层级内全量可跑是本次新裁定。

**验收结果**：
- `zigtester_list zigbox` 确认 4 层级（unit/functional/performance/performance-scenarios）✓
- `bench-standard-inbound` PASS（641 req/s, p99 2.8ms）✓
- `bench-long-socks5`（performance-scenarios）PASS：7922 req/s, p99 6.4ms, 0 错误；metrics 含
  `rss_growth_mb/fd_growth/cpu_head_pct/cpu_tail_pct` → **analyze_leak 显式生效**（level 迁移后）✓
- **跨层级基线保留**：regressions 基线取旧 performance 层历史（10781 req/s）→ runs 按 (project,suite)
  键，迁移不丢基线 ✓
- MCP 端到端：zigtester_run 返回 4 层级 + regressions 字段 + report 回归节；zigtester_history 单套件/
  组视图 report + 空态 ✓
- zigtester 自身单测全绿（见当前状态节）✓

## ⚠️ 遗留：bench-standard-outbound 仅剩 socks/socks-xray 连接泄漏（交回 zigbox）

> **处置进展（08-25，详见 zigbox task_plan #89）**：
> - **EOF 语义定案**：重量出站 30s 挂起 = HTTP 客户端等 EOF 而 sing-box 不转发上游 FIN → CL 语义
>   （`read_by_cl` 默认开）后全部重量出站 PASS ✅
> - **ss-xray 剔除（用户裁定）**：zigbox SS 客户端 ↔ xray 2022-blake3 握手不兼容，剔除非修复 ✅
> - **redirect 平台守卫已落地**：`bench_outbound_cells()` 非 Linux 不枚举 ✅
> - **剩余**：socks/socks-xray 确定性 fd 耗尽（24593 `accept failed: ProcessFdQuotaExceeded`，
>   679 成功请求 ×3 fd ≈ 2048 不释放）→ zigbox socks 出站连接泄漏，**已移交 zigoutbounds 跟踪**
>   （zigoutbounds task_plan『⚠️ 遗留』+ findings §52）。套件修复前持续 FAIL，
>   勿改 zigtester 侧掩盖。完整诊断见 findings §10。

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
