# 进度摘要

> 架构唯一来源：[DESIGN.md](DESIGN.md)。本文件记录**仍有效的进度/门禁/基线数字**；
> 历史阶段完成记录一律以 task_plan.md「历史完成阶段总表」+ git log 为准，本文件不重复。
> **二次瘦身（2026-08-23）**：2026-08-18 及更早历史流水已删（task_plan 总表承接）；
> 技术定论见 findings.md「定论位置表」（已下沉代码注释）。

## 当前状态

- 分支 `main`。**全部 Phase 1-12 完成**（2026-08-07 → 08-21），最近收尾：插件 host 化（P12）、
  MCP SSE+progress、per_suite_only、target 资源采集。
- **性能测试架构重构 A/B/C/D 完成**（08-25，本会话）：参数透传（--args）+ run 自动回归对比（MCP-only）
  + history 固定报表 + performance-scenarios 层级 + zigbox 迁移。见 task_plan「✅ 已完成」节。
- 兄弟项目全部接入：zigfoundation / zigbox / zigtun / zigproxy / zigdns / zigoutbounds + zigroute / zigunicfg。
- zigtester 自身单测全绿：test_args_passthrough 6 + test_env_guard 15 + test_per_suite_only 4 +
  test_report_history 24 + test_runner_env + test_plugin_ports + test_target_monitor。

## 近期定论（2026-08-18 → 08-25）

- **08-25 性能测试架构重构落地 + 端到端验证**：4 层级确认；bench-standard-inbound PASS（641 req/s）；
  bench-long-socks5 PASS 且 analyze_leak 显式生效（rss_growth/fd_growth 进 metrics）+ 跨层级基线保留
  （回归基线取旧 performance 历史）。**bench-standard-outbound FAIL = zigbox 数据路径 bug**（route.final
  兜底非 direct 出站未覆盖，fd 耗尽 + 重量出站请求 30s 挂起），诊断见 findings §10，交回 zigbox。
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
