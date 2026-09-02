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
- **zt-6/zt-7/zt-8/zt-9 全部闭环**（见「开放待办（历史）」）；**Phase 13 local-cf-dev 插件已收尾关闭（09-01，用户裁定）**，见下方专段。

## ✅ 已完成：性能测试架构重构（A/B/C/D，2026-08-25）

- 结论：新增第 4 层级 `performance-scenarios`（压测/疲劳/分流独立层）→ VALID_LEVELS/schema/list；
  参数透传 + run 自动回归对比（MCP-only）+ history 固定报表；附带修复 `req_s` 关键词 / duration
  归一化 / 基线三重过滤。过程与验收见 findings §9 + git log。

## ✅ 已结案：bench-standard-outbound socks/socks-xray 连接泄漏（2026-08-25）

- 结论：fd 耗尽根因 = zo `socks5.zig` 缺 `pollSessions()`（commit `481ed07` 修复，验证 peak_fd=16）；
  EOF 语义定案 + ss-xray 剔除 + redirect 平台守卫等完整诊断见 findings §10 + git log。

## Phase 13：local-cf-dev 插件（2026-08-28 → 09-01 已收尾关闭）

- 结论：本地 CF Workers/Pages VLESS/Trojan-over-WS E2E 插件已落地并收尾（09-01 用户裁定关闭，
  `local-cf-dev` 保留为 zo 专属插件）。设计/调研定论（ECH 本地不可测 / workerd 支持 connect() 等）
  见 findings §11 + `plugins/local-cf-dev/README.md`；收尾裁决 + 依据见 zigbox `findings.md`
  「Phase 13 local-cf-dev 收尾调研」段。
- **遗留（非阻塞，文档化后续项）**：27.2 CF 形态 early data（WS 0-RTT）验证——客户端当前不发
  early data、zo 侧未验证；如未来补 zigbox ws 全链路，优先复用 sing-box/xray 对端（成本远低于 CF worker）。

## 开放待办（历史，均已闭环）

- **zt-6（08-31 修复）**：long 套件后 zigbox 残留致环境自愈假失败 → zigtester runner.py 进程组兜底
  （`start_new_session` + `_kill_test_proc_tree`）+ zigbox 侧 try/finally stop 治本。结论见 findings 定论表 zt-6 行。
- **zt-7（已闭环）**：Windows taskkill 分支 + `os.kill(pid,0)` 陷阱。见 findings 定论表 zt-7 行。
- **zt-8（已闭环）**：FAIL 透传 test 脚本 stdout 尾部。见 findings 定论表 zt-8 行。
- **zt-9（09-01 收尾）**：launchd `ProcessType: Background` → Interactive（编译被杀根因，autostart.py:93 注释）。见 findings 定论表 zt-9 行。

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
