# 进度摘要

> 架构唯一来源：[DESIGN.md](DESIGN.md)。本文件记录**仍有效的进度/门禁/基线数字**；
> 历史阶段完成记录一律以 task_plan.md「历史完成阶段总表」+ git log 为准，本文件不重复。
> 技术定论见 findings.md「定论位置表」（已下沉代码注释/插件 README）。

## 当前状态（2026-09-01 内容基线；代码自 09-02 冻结 95d36b2）

- **代码自 09-02 冻结（95d36b2 = HEAD）**：本仓 tag v0.34/v0.35/v0.36 均指该 commit；生态 tag 已推进至 v0.37.0（09-06，同 commit，期间生态开发对 zigtester 无代码影响）。
- 分支 `main`。全部 Phase 1-12 完成（2026-08-07 → 08-21）+ A/B/C/D 性能架构重构完成（08-25）。
- 兄弟项目全部接入：zigfoundation / zigbox / zigtun / zigproxy / zigdns / zigoutbounds + zigroute / zigunicfg。
- zigtester 自身单测全绿：test_args_passthrough 6 + test_env_guard 15 + test_per_suite_only 4 +
  test_report_history 32 + test_runner_cleanup 2 + test_runner_env + test_plugin_ports + test_target_monitor。
- **zt-6/zt-7/zt-8/zt-9 全部闭环**（进程组兜底 / Windows taskkill 分支 / FAIL stdout 尾部透传 / launchd Interactive；结论与锚点见 findings 定论表 zt-6..zt-9 行 + zigbox 统一待办 zigtester 表）。**Phase 13 local-cf-dev 插件已收尾关闭（09-01，用户裁定）**——zo 接入闭环、zigbox 裁决不接（纯编排层冗余）；插件保留为 zo 专属。见 task_plan「Phase 13」专段。

## 近期定论（2026-08-18 → 09-01，指针 → findings 定论位置表 / task_plan，细节不重复）

- **09-01 zt-9 launchd Interactive**：`autostart.py` ProcessType Background→Interactive 后 zigoutbounds unit **435/435 PASS ×2**（11s/16s，此前 1m + 编译被杀 806M）。教训 = MCP/测试服务进程类型须匹配工作负载（Background 隐含 jetsam 上限）。→ findings 定论表 zt-9 行。
- **08-31 zt-6 进程组兜底**：`start_new_session` + finally `_kill_test_proc_tree`（SIGTERM→2s→SIGKILL）；zigbox test_bench 侧治本；long 套件后 **12080 FREE**。→ findings 定论表 zt-6 行。
- **08-28 local-cf-dev（Phase 13）**：wrangler dev 三层验证全绿；workerd 已缓存 npx 首跑无需网络；localhost 证书系统信任双向 200。→ findings §11 指针 + local-cf-dev README。
- **08-26 回归基线三重过滤**：短 duration / >7 天陈旧 / 延迟 <1ms 噪声 → `_is_short_duration`/`_is_stale`/`_is_latency_metric`；**test_report_history 27→32**，zigdns 误报清零。→ findings 定论表三重过滤行。
- **08-25 回归 duration 归一化**：总量按 duration_s 归一为每秒速率（`_DURATION_SCALED_METRICS`），hy2 -40% 误报消除；**test_report_history 24→27**。→ findings 定论表行。
- **08-25 A/B/C/D 重构 + bench-standard-outbound 结案 / 08-23 target / 08-22 per_suite_only / 08-21 SSE+插件 host 化 / 08-18 Phase 9-11**：均见 task_plan「已完成/已结案」段 + findings 定论表（如 fd 耗尽 = zo `pollSessions()` 缺失 481ed07），不再近况重述。
- **08-19 echo FIN 暴露 zo 潜伏 UAF**：local-echo :13337 改 10ms idle 主动 FIN → zo relay/deinit 双 tun.close UAF（已修 tun_relayed）。→ 已下沉 `local-echo/main.go` 头注，见 findings「测试方法」指针行。

## 仍有效基线/门禁

- **MCP Server 常驻 9020**（HTTP transport）：改 server.py/runner.py 后需 `launchctl kickstart -k` 重启才生效。
- **插件日志**：`/tmp/zigtester-plugin-<name>.log`（启动/ready 失败排查第一手证据）。
- **单请求铁律**：全链路 loopback 单请求 ≤100ms，超过当失败。
- **全量铁律**：全部功能+性能 1 分钟内跑完，超过 = 失败止损修根因，禁止降级/排除。
- **端口契约**：local-echo 13333/5533/15353/13335/13336/18080/18443/13337/13338（见 CLAUDE.md）；
  sing-box / xray 端口表见 plugins/*/plugin.yaml；local-cf-dev = **VLESS worker 18787 + Trojan worker 18789 双常驻**，**Pages 18788 预留未接线**（真相源 plugins/local-cf-dev/plugin.yaml）。
