# 进度摘要

> 架构唯一来源：[DESIGN.md](DESIGN.md)。本文件记录**仍有效的进度/门禁/基线数字**；
> 历史阶段完成记录一律以 task_plan.md「历史完成阶段总表」+ git log 为准，本文件不重复。
> 技术定论见 findings.md「定论位置表」（已下沉代码注释/插件 README）。

## 当前状态（2026-09-01）

- 分支 `main`。全部 Phase 1-12 完成（2026-08-07 → 08-21）+ A/B/C/D 性能架构重构完成（08-25）。
- 兄弟项目全部接入：zigfoundation / zigbox / zigtun / zigproxy / zigdns / zigoutbounds + zigroute / zigunicfg。
- zigtester 自身单测全绿：test_args_passthrough 6 + test_env_guard 15 + test_per_suite_only 4 +
  test_report_history 32 + test_runner_cleanup 2 + test_runner_env + test_plugin_ports + test_target_monitor。
- **zt-6（进程组兜底）/zt-9（launchd Interactive）已闭环**；进行中：local-cf-dev 插件（Phase 13）——
  剩余消费方接入（zigbox/zigoutbounds `zigtester.yaml` 加 plugins + VLESS/Trojan+WS 套件，域名 `localhost`）。

## 近期定论（2026-08-18 → 09-01，指针 → findings/task_plan）

- **09-01 zt-9 launchd Interactive**：`autostart.py` ProcessType Background→Interactive 后
  `zigtester_run zigoutbounds --level unit` **435/435 PASS ×2**（11s/16s，此前 1m + 编译被杀 806M）。
  教训：MCP/测试服务进程类型必须匹配其工作负载（派生大编译），Background 隐含 jetsam 内存/调度上限。
- **08-31 zt-6 进程组兜底清理**：runner.py `start_new_session=True` + finally `_kill_test_proc_tree`
  （SIGTERM→2s→SIGKILL killpg）；zigbox `test_bench.py` 侧治本（`ZigboxProcess` 句柄 + try/finally
  `zb.stop()`）；集成验证 long 套件后 12080 FREE。教训：FAIL 先查环境残留/SIGTERM/端口占用。
- **08-28 local-cf-dev 插件（Phase 13）落地**：wrangler dev 三层验证全绿；**workerd 已缓存 npx，首跑无需网络**
  （修正此前「首跑需一次网络」结论）；localhost 证书系统信任已落地（系统 Keychain + Homebrew curl 经
  ~/.curlrc 合并副本双向 200）。关键定论见 findings §11。
- **08-26 回归检测基线三重过滤**（zigdns 审查发现）：短 duration 瞬态 / >7 天陈旧 / 延迟 <1ms 噪声
  → `_is_short_duration`/`_is_stale`/`_is_latency_metric`；test_report_history 27→32 全绿，zigdns 误报清零。
- **08-25 回归检测 duration 归一化**：总量指标按 duration_s 归一为每秒速率再对比（`_DURATION_SCALED_METRICS`），
  防 `--duration` 40s→10s 被误判吞吐回归（hy2 实证 -40% 误报，req/s 稳定）；test_report_history 24→27。
- **08-25 A/B/C/D 重构 + bench-standard-outbound 结案**：见 task_plan「✅ 已完成/已结案」。
  socks/socks-xray fd 耗尽 = zo `pollSessions()` 缺失（481ed07，peak_fd=16）；redirect 非 Linux 守卫。
- **08-23 target 字段**：只采目标被测程序（bench-long-direct peak 8.2/avg 8.1MB 与 psutil 探针吻合；5 项目 48 处补齐）。
- **08-22 per_suite_only**：zigoutbounds 25 套件全量 321.6s → `--level` 全量 SKIP 2.3s；`--suite` 单跑 PASS。
- **08-21 MCP SSE+progress + 插件 host 化（P12）**：SSE 流生效，10s 心跳 progress + PASS；三 VM 全过。
- **08-19 echo FIN 暴露 zo 潜伏 UAF（跨项目）**：真凶 = local-echo :13337 改 10ms idle 主动 FIN →
  zo relay/deinit 双 tun.close UAF（zo 已修 tun_relayed，7cbc9ae）。框架启示见 findings「测试方法」。
- **08-18 Phase 9-11**：环境统一治理（PluginManager 三层校验 + 自愈 + fast fail）；流畅性（failure_lines /
  flaky / 分组并行）；flaky 根因修复（zigproxy 客户端超时 + zigdns FakeIP 期望）+ 插件管道排空。

## 仍有效基线/门禁

- **MCP Server 常驻 9020**（HTTP transport）：改 server.py/runner.py 后需 `launchctl kickstart -k` 重启才生效。
- **插件日志**：`/tmp/zigtester-plugin-<name>.log`（启动/ready 失败排查第一手证据）。
- **单请求铁律**：全链路 loopback 单请求 ≤100ms，超过当失败。
- **全量铁律**：全部功能+性能 1 分钟内跑完，超过 = 失败止损修根因，禁止降级/排除。
- **端口契约**：local-echo 13333/5533/15353/13335/13336/18080/18443/13337/13338（见 CLAUDE.md）；
  sing-box / xray 端口表见 plugins/*/plugin.yaml；cfdev workers 18787 / pages 18788。
