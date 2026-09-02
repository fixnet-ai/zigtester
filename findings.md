# 有效技术结论

> 只保留仍然有效的技术结论；过程流水账已删除。与 [DESIGN.md](DESIGN.md) 冲突时以 DESIGN.md 为准。
> 瘦身（第 2 轮 08-23 + 第 4 轮 08-27 + 第 5 轮 09-01）：技术定论均已下沉到对应代码头部/函数注释或
> 插件 README，此处只留「定论位置表」指针；Zig 0.16 语言经验交 zig-codegen；久远历史/被推翻结论已删。
> 保留章节号（§X）供 task_plan 历史总表跳转与 git 追溯。

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
- **pkill/pgrep 自匹配陷阱**：`pkill -f "local-echo --tcp-port"` 时外层 shell 命令行自身含该串 → 杀了自己。规避 `pgrep -f "[l]ocal-echo --tcp-port"`（字符类正则使模式不匹配字面自身）。
- **echo 连接生命周期语义是下游协议隐式契约**：local-echo bench :13337 改为响应后 10ms idle 主动 FIN → 暴露 zo 潜伏 UAF（relay/deinit 双 tun.close 竞态，zo 已修 tun_relayed）。此类行为变更落地后应主动触发下游项目全量压测回归。
- **单请求 / 全量铁律**：全链路 loopback 单请求 ≤100ms，超过当失败；全部功能+性能 1 分钟内跑完，超过 = 失败止损修根因，禁止降级/排除。
- **MCP 服务改动需重启**：server.py/runner.py 改动后 launchctl kickstart 重启才生效——忘记重启 = 改动「不生效」假象。

## 错误方向记录（勿再追）

- **sing-box Clash API 热重载不可行** → serve 直接完整配置启动（`PUT /configs` 只接受 Clash 格式）。
- **xray Reality 凭证不可移植** → 与 sing-box Reality 私钥格式不兼容，各插件独立凭证（命名前缀区分）。
- **MCP stdio transport 多实例僵尸进程** → 切 HTTP transport（端口绑定天然互斥）。
- **local-echo python 实现（echo_server.py）** → 2026-08-17 被 Go 单程序统一重写。
- **route.final 兜底非 direct 出站有数据面 bug（08-25 撤销）** → 源码逐层核查无缺陷；真实状态 = socks fd 耗尽 + 重量出站挂起，根因归出站侧（见 §10 指针）。

## §11 local-cf-dev 插件调研（2026-08-28，Phase 13 进行中）

> 任务：查证 `local-cf-dev.md` 文档 + 设计 zigtester 新插件（本地 CF Workers/Pages 代理，配合 zigbox/zigoutbounds 做 VLESS/Trojan 协议 E2E）。参考 `vendor/Cloudflare-vless-trojan`（yonggekkk，commit 25a9017，作者声明暂停维护）。完整事实见插件 README + `cfdev_ctl.py` docstring。

- **文档查证**：基本属实，但它是「通用 CF 本地开发教程」非本项目 E2E 方案；缺两点——未提 `cloudflare:sockets connect()`（worker 核心能力，本地 workerd **完整支持**，这是离线 E2E 的根基）、完全没提 ECH。
- **worker 数据路径**：fetch 收 WS Upgrade → `webSocket.accept()` → 解析 VLESS/Trojan 头 → `connect({hostname,port})` 连目标 → 双向 relay；失败 retry 兜底 `proxyIP`。VLESS 默认 uuid `86c50e3a-5b87-49dd-bd20-03c7f2735e40`，Trojan 默认密码 `trojan`。
- **关键技术事实**：
  1. **本地 workerd 支持 connect() 且不拦回环** → worker 可连 127.0.0.1 local-echo（生产 CF 会拦 127.x/10.x）。
  2. **一律 `127.0.0.1`，别用 `localhost`**（Node 可能解析 `::1`，workerd 解析 `127.0.0.1`，PR #12913）。
  3. **`--local-protocol https`**（或 `[dev] local_protocol`）启用本地 HTTPS；**复用生态 localhost 证书**（`plugins/local-echo/certs/`，经 `--https-cert-path/key-path` 注入，实测 wrangler 4.127 支持）。
  4. **worker 会把 IPv4 目标重写成 `www.<ip>.sslip.io`** → E2E 客户端 VLESS 目标必须用域名（atyp=0x02，如 `localhost`），worker 才直连本机。
  5. **ECH 本地不可测（定论）**：workerd 不做 ECH 终止（ECH 是 CF 边缘 TLS 终止特性）；插件 E2E 只覆盖 VLESS/Trojan + WS + TLS（非 ECH）。
  6. **wrangler dev 三层验证全绿（08-28）**：render → 冒烟（`Ready on 127.0.0.1:18787`）→ relay（VLESS 头 32 字节解析 + payload 原样回显）；**workerd 已缓存 `~/.npm/_npx/`，首跑无需网络**。
- **插件设计决策**：复用 serve 模式（`cfdev_ctl.py`）；端口 workers 18787 / pages 18788；worker 源经 config 引用 vendor（不复制混淆 JS）；**协议覆盖边界** = 只 VLESS/Trojan（s5http 不接入，socks5/http 已由 sing-box 插件覆盖）。
- **接入结论（调研 zigoutbounds）**：VLESS/Trojan-over-WS **已完整实现**（`transport/ws.zig` 2245 行 + `vless.zig:1287`/`trojan.zig:267` ws_enabled + 配置映射已通），**缺的是测试用例而非协议**。**27.1 标准形态已落地**（4 处接线：functional×2 + bench-tcp/stream/sweep×6，全 PASS）；**27.2 CF 形态（后续）**：用 local-cf-dev 测 CF 兼容性（early data + 2 字节响应头 `[version,0]`），先实测 zigoutbounds vless-ws ↔ CF worker 直通性，必要时加 CF 兼容开关（非新协议）。

## 已结案/已完成（指针 → task_plan）

- **§9 性能测试架构重构调研 → A/B/C/D 已完成（08-25）**：sing-box/xray 入站端口真相源 = `plugins/*/plugin.yaml`（sing-box 19 入站 2080~16812、xray 10 入站 2180~16909；xray socks:2180 无认证）；框架约束 = 4 层级 + per_suite_only + analyze_leak 显式。
- **§10 bench-standard-outbound 诊断已结案（08-25）**：redirect cell = Linux 平台不支持（已加守卫）；重量出站 30s 挂起 = EOF 语义（read_by_cl 后全 PASS）；ss-xray 剔除（2022-blake3 握手不兼容，非修复）；**socks/socks-xray fd 耗尽 = zo `pollSessions()` 缺失（`481ed07` 修复，peak_fd=16）**。
- **§6 MCP 长任务超时根因**：`json_response=True` 吞掉所有 progress 通知 → 客户端 60s 首字节超时；修复 = `json_response=False`（SSE）+ 每 10s 心跳（progress MUST 单调递增）。凡长任务 MCP 工具必须 SSE + 心跳。
- **§7 per_suite_only**：zigoutbounds 25 套件全量 321.6s → `--level` 全量 SKIP + `--suite` 单跑（schema 无字段禁止 level 全量的根因）。
- **§8 target 字段**：只采目标被测程序，未匹配跳过采样；**sudo 双重坑**（命令自带 sudo → 命令进程 = sudo 进程 rss≈0）。
- **zt-6/zt-7/zt-8/zt-9 已闭环**（详见 task_plan「开放待办」）：zt-6 进程组兜底 + zigbox 治本；zt-7 Windows taskkill 分支；zt-8 stdout 尾部透传；zt-9 launchd ProcessType Interactive。
