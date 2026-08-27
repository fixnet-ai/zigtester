# 进度摘要

> 架构唯一来源：[DESIGN.md](DESIGN.md)。本文件记录**仍有效的进度/门禁/基线数字**；
> 历史阶段完成记录一律以 task_plan.md「历史完成阶段总表」+ git log 为准，本文件不重复。
> **二次瘦身（2026-08-23）**：2026-08-18 及更早历史流水已删（task_plan 总表承接）；
> 技术定论见 findings.md「定论位置表」（已下沉代码注释）。
> **第四轮瘦身（2026-08-27）**：08-25 A/B/C/D 详情与 task_plan「✅ 已完成」去重（此处留指针）；
> 08-25 条目中「route.final 兜底 bug」为已撤销推断，修正为最终定论（socks fd 泄漏 → zigoutbounds）。

## 当前状态

- 分支 `main`。全部 Phase 1-12 完成（2026-08-07 → 08-21），最近收尾：插件 host 化（P12）、
  MCP SSE+progress、per_suite_only、target 资源采集。
- **性能测试架构重构 A/B/C/D 完成**（08-25）：参数透传（--args）+ run 自动回归对比（MCP-only）
  + history 固定报表 + performance-scenarios 层级 + zigbox 迁移。见 task_plan「✅ 已完成」节。
- 兄弟项目全部接入：zigfoundation / zigbox / zigtun / zigproxy / zigdns / zigoutbounds + zigroute / zigunicfg。
- zigtester 自身单测全绿：test_args_passthrough 6 + test_env_guard 15 + test_per_suite_only 4 +
  test_report_history 32 + test_runner_env + test_plugin_ports + test_target_monitor。

## 近期定论（2026-08-18 → 08-26）

- **08-26 回归检测基线三重过滤修复**（zigdns 审查发现）：bench 吞吐回归被历史基线污染误报 ↓73%~98%。
  三重根因 + 修复（`history.py` `check_regression`）：
  ① 短-duration 启动瞬态 → `_is_short_duration`（剔除 duration_ms < 2s）；
  ② 单轮→多轮测量方式变更的旧数据不可比 → `_is_stale`（剔除 > 7 天陈旧记录）；
  ③ 延迟亚毫秒噪声 → `_is_latency_metric` guard（绝对差 < 1ms 不报）。
  test_report_history 27→32 全绿；zigdns 全量 regressions 空（修复前稳定误报）。
- **08-25 回归检测 duration 归一化修复**：bench-long 类套件总量指标（success_count/total_requests/
  error_count）随压测时长线性增长，`--duration` 默认值 40s→10s 调整被误判为吞吐回归（实证：
  hy2 success_count 266471@40s → 65594@10s 误报 -40%，req/s 稳定 ~6.6k 无退化）。修复 =
  `check_regression` 对总量指标按 `duration_s` 归一化到每秒速率再对比（`_DURATION_SCALED_METRICS`
  + `_duration_of`）；无 duration 的旧记录不归一化（向后兼容）。test_report_history 24→27 全绿。
- **08-25 性能测试架构重构落地 + 端到端验证**：见 task_plan「✅ 已完成」。**bench-standard-outbound
  遗留 = socks/socks-xray fd 耗尽（zigbox socks 出站连接泄漏）**，已移交 zigoutbounds 跟踪；
  redirect 为 Linux 平台不支持（已加守卫）；重量出站挂起经 CL 语义（read_by_cl）修复。
  history.py `_is_metric_regression` 修复（吞吐关键词补 req_s，修 req_s 上升 136.8% 误标回归）。
- **08-23 target 字段**：只采目标被测程序（bench-long-direct peak 8.2 / avg 8.1MB 与 psutil 探针吻合，
  launchd.log 无 fallback warning）；5 项目 48 处 target 补齐（zigbox/zigdns/zigproxy/zigtun/zigunicfg）。
- **08-22 per_suite_only**：zigoutbounds 25 套件全量 321.6s → `--level` 全量 25 SKIP 2.3s；`--suite` 单跑 PASS。
- **08-21 MCP SSE+progress**：SSE 流生效；端到端收到 10s 心跳 progress + result PASS；test_report_history 15/15。
- **08-21 插件 host 化（P12）**：远程插件模式（host 优先级 plugin_ref > plugin.yaml > env > 127.0.0.1）；
  三 VM（linux/windows/mac sysproxy）实测全过，本机零回归。
- **08-19 echo FIN 暴露 zo 潜伏 UAF（跨项目排查）**：bb48c8c 无辜；真凶 = local-echo bench :13337
  改响应后 10ms idle 主动 FIN → zo relay/deinit 双 tun.close UAF（zo 已修 tun_relayed，7cbc9ae）。
  框架启示见 findings.md § 测试方法。
- **08-18 Phase 9-11**：环境统一治理（PluginManager 三层校验 + 自愈 + fast fail + 生态铁律）；
  测试流畅性（failure_lines / flaky 检测 / 分组并行 / scenarios fast fail）；flaky 根因修复
  （zigproxy 客户端超时缺陷 + zigdns FakeIP 期望）+ 插件管道排空（/tmp/zigtester-plugin-<name>.log）。
- **08-08 / 08-10**：MCP stdio→HTTP（端口绑定互斥）；suite 过滤递归依赖；插件 config 解析修复；
  xray-core 插件（8 入站端口 LISTEN + 冲突检测）。

## 仍有效基线/门禁

- **MCP Server 常驻 9020**（HTTP transport）：改 server.py/runner.py 后需重启（launchctl kickstart -k）才生效——
  忘记重启 = 改动"不生效"假象。
- **插件日志**：`/tmp/zigtester-plugin-<name>.log`（插件启动/ready 失败排查第一手证据）。
- **单请求铁律**：全链路 loopback 单请求 ≤100ms，超过当失败。
- **全量铁律**：全部功能+性能 1 分钟内跑完，超过 = 失败止损修根因，禁止降级/排除。
- **端口契约**：local-echo 13333/5533/15353/13335/13336/18080/18443/13337/13338（见 CLAUDE.md）；
  sing-box / xray 端口表见 plugins/*/plugin.yaml。
