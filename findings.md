# 有效技术结论

> 只保留仍然有效的技术结论；过程流水账已删除。与 [DESIGN.md](DESIGN.md) 冲突时以 DESIGN.md 为准。
> 瘦身（第 2 轮 08-23 + 第 4 轮 08-27 + 第 5 轮 09-01）：技术定论均已下沉到对应代码头部/函数注释或
> 插件 README，此处只留「定论位置表」指针；Zig 0.16 语言经验交 zig-codegen；久远历史/被推翻结论已删。
> 保留章节号（§X）供 task_plan 历史总表跳转与 git 追溯。
> **代码自 09-02 冻结（95d36b2 = HEAD）**：本仓 tag v0.34/v0.35/v0.36 均指该 commit，生态 tag 已推进至 v0.37.0
> （09-06，同 commit，无代码影响）；下文行内计数是快照，入站端口/数量一律以 `plugins/*/plugin.yaml` 现行值复核。

## 定论位置表（技术定论已下沉代码注释/插件 README，不在此重复正文）

| 机制/定论 | 代码位置 |
|-----------|---------|
| MCP 长任务 SSE + progress 心跳（`json_response=False`） | `src/zigtester/server.py` `_run_with_progress` + `main()` |
| HTTP transport（端口绑定互斥，杜绝多实例） | `src/zigtester/server.py` 模块头 |
| 资源采集 `target` 字段（只采目标被测程序） | `src/zigtester/monitor.py` 模块头 + `_collect` |
| per_suite_only（禁止 --level 全量压测） | `src/zigtester/runner.py` `run_project` + `config.py` |
| performance-scenarios 独立层级（4 层） | `src/zigtester/config.py` `VALID_LEVELS` |
| PluginManager 三层校验（进程 + 端口 + 端口归属） | `src/zigtester/plugin.py` `verify_plugin` |
| 自愈稳定期（死亡窗口假阳性） | `src/zigtester/plugin.py` `_HEAL_STABILITY_DELAY` |
| 插件管道排空（防子进程 write 阻塞假死） | `src/zigtester/plugin.py` `_start_plugin_process` |
| 插件 config 合并语义（默认 → 覆盖 → PLUGIN_* env） | `src/zigtester/plugin.py` `parse_plugin_config` |
| 端口冲突两层防御（跨插件 + 系统占用，全阻塞） | `src/zigtester/plugin.py` `check_port_conflicts` |
| HTTP_PROXY 劫持 localhost（ProxyHandler({})） | `plugins/sing-box/singbox_ctl.py` `_opener` |
| xray 证书路径绝对化（cwd 错位） | `plugins/xray-core/xray_ctl.py` `_render_config_to_path` |
| xray 裸 TCP readiness 探针（无 REST API） | `plugins/xray-core/xray_ctl.py` |
| Clash API 不支持原生格式热重载 | `plugins/sing-box/singbox_ctl.py` `reload`（L457 注释） |
| local-echo 统一 Go 实现 + 端口契约 | `plugins/local-echo/main.go` 头部 |
| 回归检测总量指标 duration 归一化（防 --duration 调整误报） | `src/zigtester/history.py` `_DURATION_SCALED_METRICS` + `check_regression` |
| 回归检测基线三重过滤（08-26：短 duration 瞬态/陈旧窗口/延迟噪声 guard） | `src/zigtester/history.py` `_is_short_duration` + `_is_stale` + `_is_latency_metric` |
| zt-6 进程组兜底清理（start_new_session + killpg） | `src/zigtester/runner.py` `_kill_test_proc_tree` |
| zt-7 Windows taskkill 分支 + os.kill(pid,0) 陷阱 | `src/zigtester/runner.py:315-356` |
| zt-8 FAIL 透传 test 脚本 stdout 尾部 | `src/zigtester/reporter.py` `compact_markdown` |
| zt-9 launchd ProcessType Background→Interactive（编译被杀根因） | `src/zigtester/autostart.py:93` 注释 |
| local-cf-dev 插件关键事实（ECH 不可测/域名目标/127.0.0.1/证书） | `plugins/local-cf-dev/README.md` + `cfdev_ctl.py` docstring |
| masque-echo CONNECT-IP echo 语义 + PLUGIN_HOST（CONNECT-IP :authority 严格匹配，host 化场景须覆盖） | `plugins/masque-echo/plugin.yaml` config 段 + `main.go` 头部 |
| masque-echo stateless TCP 反射器（SYN/数据/FIN 无状态推算，仅 IPv4+TCP；UDP 32.5g 翻转 src/dst） | `plugins/masque-echo/tcp_echo.go` 头部 |
| masque-echo-mtls mTLS 变体（复用 masque-echo 源码，client_ca 校验；独立端口 13410/13411） | `plugins/masque-echo-mtls/plugin.yaml` config 段 |
| zig_test parser 构建失败误报修复（多 step 编译错误 → `build_failure`，不喂 detect_flaky，09-02 950e8a1） | `src/zigtester/metrics.py` `_parse_zig_test` |

## 测试方法（仍有效，非代码）

- **HTTP_PROXY 劫持 localhost**：任何访问 127.0.0.1 的 HTTP 客户端必须禁用代理（requests `trust_env=False` / urllib `ProxyHandler({})`），否则在有代理的开发机上必挂。
- **pkill/pgrep 自匹配陷阱**（已下沉）→ `src/zigtester/plugin.py` `_pkill_names` 头注：`pkill -f "local-echo --tcp-port"` 时外层 shell 命令行自身含该串会误杀自己，规避 = `pgrep -f "[l]ocal-echo..."` 字符类正则。
- **echo 连接生命周期语义 = 下游协议隐式契约**（已下沉）→ `plugins/local-echo/main.go` 头部「跨仓数据面契约」：bench :13337 响应后 10ms idle 主动 FIN 曾暴露 zo 潜伏 UAF（relay/deinit 双 tun.close，zo 已修 tun_relayed）；echo 行为变更落地后须触发下游全量压测回归。
- **单请求 / 全量铁律**：全链路 loopback 单请求 ≤100ms，超过当失败；全部功能+性能 1 分钟内跑完，超过 = 失败止损修根因，禁止降级/排除。
- **MCP 服务改动需重启**：server.py/runner.py 改动后 launchctl kickstart 重启才生效——忘记重启 = 改动「不生效」假象。

## 错误方向记录（勿再追）

- **sing-box Clash API 热重载不可行** → serve 直接完整配置启动（`PUT /configs` 只接受 Clash 格式）。
- **xray Reality 凭证不可移植** → 与 sing-box Reality 私钥格式不兼容，各插件独立凭证（命名前缀区分）。
- **MCP stdio transport 多实例僵尸进程** → 切 HTTP transport（端口绑定天然互斥）。
- **local-echo python 实现（echo_server.py）** → 2026-08-17 被 Go 单程序统一重写。
- **route.final 兜底非 direct 出站有数据面 bug（08-25 撤销）** → 源码逐层核查无缺陷；真实状态 = socks fd 耗尽 + 重量出站挂起，根因归出站侧（见 §10 指针）。

## §11 local-cf-dev 插件调研（2026-08-28，Phase 13 已收尾）

> 任务与本调研完整事实已下沉，不在此复述正文：ECH 本地不可测 / workerd `connect()` 支持 / 127.0.0.1 vs localhost / `--local-protocol https` 证书复用 / sslip.io 域名重写 / wrangler 三层验证等，**完整事实见 `plugins/local-cf-dev/README.md` + `cfdev_ctl.py` docstring + 定论位置表 local-cf-dev 行**。

- **插件形态（09-01 收尾；de4d628 双 worker 先于收尾落地）**：端口/凭证真相源 = `plugins/local-cf-dev/plugin.yaml` config 段——**VLESS worker 18787 + Trojan worker 18789 双常驻**（per-worker nodejs_compat），**Pages 18788 预留未接线**；协议覆盖边界只 VLESS/Trojan（s5http 不接入）。
- **接入结论（zigoutbounds）**：VLESS/Trojan-over-WS 协议侧已实现（transport/ws.zig），**27.1 标准形态已落地**（functional×2 + bench-tcp/stream/sweep×6，全 PASS）；**27.2 CF 形态 early data（WS 0-RTT）验证仍开放** → 已移交 **zigbox 统一待办 zo 组**（09-07 第 8 次瘦身登记），见其 zo 表。

## 已结案/已完成（指针 → task_plan）

- **§9 性能测试架构重构调研 → A/B/C/D 已完成（08-25）**：sing-box/xray 入站端口**唯一真相源** = `plugins/*/plugin.yaml`（随新协议演进，勿固化计数；09-02 冻结快照——sing-box 22 协议 inbound 键 2080~16812，渲染 22 inbound 由 `test_plugin_ports.py:87` assert 固化，bc089e8「18→22」同步；xray 22 协议 inbound 键 2180~16931，含 16924-16931 xhttp 族 + 16930 reality + 16931 h3，3ccae14/53e3e11/f546de8 加入；xray socks:2180 无认证）；框架约束 = 4 层级 + per_suite_only + analyze_leak 显式。
- **§10 bench-standard-outbound 诊断已结案（08-25）**：redirect cell = Linux 平台不支持（已加守卫）；重量出站 30s 挂起 = EOF 语义（read_by_cl 后全 PASS）；ss-xray 剔除（2022-blake3 握手不兼容，非修复）；**socks/socks-xray fd 耗尽 = zo `pollSessions()` 缺失（`481ed07` 修复，peak_fd=16）**。
- **§6 MCP 长任务超时根因**：`json_response=True` 吞掉所有 progress 通知 → 客户端 60s 首字节超时；修复 = `json_response=False`（SSE）+ 每 10s 心跳（progress MUST 单调递增）。凡长任务 MCP 工具必须 SSE + 心跳。
- **§7 per_suite_only**：zigoutbounds 25 套件全量 321.6s → `--level` 全量 SKIP + `--suite` 单跑（schema 无字段禁止 level 全量的根因）。
- **§8 target 字段**：只采目标被测程序，未匹配跳过采样；**sudo 双重坑**（命令自带 sudo → 命令进程 = sudo 进程 rss≈0）。
- **zt-6/zt-7/zt-8/zt-9 已闭环**（详见 task_plan「开放待办」）：zt-6 进程组兜底 + zigbox 治本；zt-7 Windows taskkill 分支；zt-8 stdout 尾部透传；zt-9 launchd ProcessType Interactive。
