# 有效技术结论

> 只保留仍然有效的技术结论；过程流水账已删除。与 [DESIGN.md](DESIGN.md) 冲突时以 DESIGN.md 为准。
>
> **二次瘦身（2026-08-23）**：① 技术定论均已下沉到对应代码头部/函数注释，此处只留「定论位置表」指针；
> ② Zig 0.16 语言经验已移出（跨项目通用，交 zig-codegen 汇总，不在此重复）；③ 久远历史阶段
> （Phase 1-12 过程）、被推翻结论、纯实验过程已删除。**本文件保留章节号（§X）供 task_plan
> 历史总表跳转与 git 追溯。**
>
> **第四轮瘦身（2026-08-27）**：§9 sing-box/xray inbound 端口清单改为指针（唯一真相源 =
> `plugins/*/plugin.yaml`，此处只留定性结论）；§10 诊断长文凝练为最终定论 + 跨项目指针，
> 被推翻的 route.final 假设移入「错误方向记录」；框架约束补 performance-scenarios 层级
> （§9 原「3 层」已过时，现 4 层）。

## 定论位置表（技术定论已下沉代码注释，不在此重复正文）

| 机制/定论 | 代码位置 |
|-----------|---------|
| MCP 长任务 SSE + progress 心跳（`json_response=False`） | `src/zigtester/server.py` `_run_with_progress` + `main()` |
| HTTP transport（端口绑定互斥，杜绝多实例） | `src/zigtester/server.py` 模块头 |
| 资源采集 `target` 字段（只采目标被测程序） | `src/zigtester/monitor.py` 模块头 + `_collect` |
| per_suite_only（禁止 --level 全量压测） | `src/zigtester/runner.py` `run_project` + `config.py` |
| performance-scenarios 独立层级（4 层，A/B/C/D 新增） | `src/zigtester/config.py` `VALID_LEVELS` |
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
| 回归检测总量指标 duration 归一化（防 --duration 调整误报） | `src/zigtester/history.py` `_DURATION_SCALED_METRICS` + `_duration_of` + `check_regression` |
| 回归检测基线三重过滤（2026-08-26）：短 duration 启动瞬态 / 陈旧记录时间窗口 / 延迟亚毫秒噪声 guard | `src/zigtester/history.py` `_is_short_duration` + `_is_stale` + `_is_latency_metric` + `check_regression` |

## 最近关键定论（2026-08-18 → 08-25）

### §9 性能测试架构重构调研（2026-08-25，zigbox 会话，移交本项目推进）

- **sing-box / xray test_server 入站端口**：唯一真相源 = `plugins/sing-box/plugin.yaml` +
  `plugins/xray-core/plugin.yaml` config 段（sing-box 19 入站 2080~16812，final→local-echo；
  xray 10 入站 2180~16909，freedom→echo；**xray socks:2180 无认证**）。zigbox 已实现出站客户端：
  socks/http/ss/trojan/hy2/vless/tuic/anytls；可连 xray：socks/ss/trojan/vless-tls。
- **框架约束**：`VALID_LEVELS` 现 **4 层**（`config.py:16` 含 performance-scenarios；schema levels 仅
  4 键、`additionalProperties:false`）；parse_config 只遍历 VALID_LEVELS（config.py:335）。
  新层级需改 VALID_LEVELS + schema。`per_suite_only` = suite_filter is None 时跳过（runner.py:618-625）。
  `analyze_leak` 自动开 = level=="performance" and "long" in name（config.py:273-276），
  显式 `analyze_leak` 优先（迁入 performance-scenarios 的长时套件须显式设）。
- **复用点**：zigbox `tests/lib/matrix.py`（`_heavy_outbound` 单出站配置 / TAG 常量 / `_dns_servers`）、
  `singbox_pair.py`（ensure_pair 只探测不启停）、`tests/lib/zigbox.py`（ZigboxProcess 生命周期 +
  `_cleanup_leftover` 等锁）、zigoutbounds `tests/lib/xray.py`（plugin_ports 派生
  `xray_port`/`xray_credential`/`xray_readiness_port` 模式）。
- **缺口**：test_bench.py 无「短连接+时长」模式（`-n` 短连接计数 / `-d` keep-alive 时长二分）
  → 标准场景需新增。

### §10 bench-standard-outbound 数据路径诊断（2026-08-25，已结案移交）

**症状**：`--standard-outbound` 14 cell 无法通过（一次 `resource_limits.fd_count=500` 超限 FAIL
peak_fd=2048；一次 300s 超时被杀）。

**最终归因**：
- **redirect cell = Linux 专用透明重定向**：macOS socket create failed 是**平台不支持**
  （日志无 EMFILE，非 fd 泄漏、非数据路径 bug）→ `bench_outbound_cells()` 已加非 Linux 不枚举守卫 ✅
- **重量出站 30s 挂起 = EOF 语义**：HTTP 客户端等 EOF 而 sing-box 不转发上游 FIN → CL 语义
  （`test_bench.read_by_cl` 默认开）后全部重量出站 PASS（含 ss/trojan/hy2/vless/tuic/anytls +
  trojan-xray/vless-xray）✅
- **ss-xray 剔除（用户裁定）**：zigbox SS 客户端 ↔ xray 2022-blake3-aes-128-gcm 握手不兼容
  （connect EV_EOF，sing-box 对端正常），非修复；cell 已删（调研归 zigoutbounds findings §51）✅
- **socks + socks-xray fd 耗尽（剩余）**：24593 条 `[server] accept failed: ProcessFdQuotaExceeded`，
  679 成功请求 ×3 fd ≈ 2048 peak 连接不释放，两 socks cell 同症、与对端（sing-box/xray）无关 →
  **zigbox socks 出站连接泄漏**，已移交 zigoutbounds 跟踪（zigoutbounds task_plan『⚠️ 遗留』+
  findings §52），套件修复前勿改 zigtester 侧掩盖。

> 已排除项（无需再查）：凭证（经 plugin_ports 由 plugin.yaml 派生，非硬编码，与 zigoutbounds
> bench-tcp-shadowsocks PASS 同源）、配置字段（sing-box 风格 `{type,method,password}` 与 zo
> shadowsocks 读取字段匹配）、对端（scenarios 57/57 证明端口可达）、`_dns_servers()`（单 server
> 非 fd 源）。证据文件：`/tmp/zigbox-tests-dasimo/` 下 bench-*.json + zigbox-*.log +
> zigbox-pid-tracker.log。

**B 功能附带验证**：bench-long-socks5 迁移后 metrics 含 `rss_growth_mb=0.1/fd_growth=-1`（analyze_leak
显式生效）+ regressions 正确渲染 4 条（p99↑21%、吞吐↓26.5% 等，基线为旧 performance 层历史 → 跨层级
基线保留）。回归标记是基线可比性差异（当时 R 标定前数据），真实性归 zigbox 判断，B 功能正确标出差异。

### §8 target 字段资源采集（2026-08-23）

只采目标被测程序（进程名匹配），未匹配跳过采样（不采命令进程），stop() 空快照 + warning 暴露配置错误。
**sudo 双重坑**：命令自带 sudo → 双重 sudo，命令进程 = sudo 进程（rss≈0），必须 target 匹配真实
被测二进制（zigtun `sudo zigtun-test` 直跑套件实证）。target 生效后资源绝对值变小，但泄漏判定
（analyze_leak 趋势差分）仍有效。

### §7 per_suite_only 字段（2026-08-22）

套件级布尔禁止 `--level` 全量压测。zigoutbounds 25 套件全量 321.6s/6 FAIL → 全量 25 SKIP 2.3s +
`--suite` 单跑。根因：schema 无任何字段可禁止 level 全量。

### §6 MCP 长任务超时根因（2026-08-21）

mcp SDK `json_response=True` 吞掉所有 progress 通知、只在最终 response 返回单 JSON → 客户端 60s
per-request 首字节超时。修复 = `json_response=False`（SSE 流，priming event 立即发首字节）+
async 工具 + `asyncio.to_thread` + 每 10s `report_progress` 心跳（progress 值 MUST 单调递增）。
**教训**：凡长任务 MCP 工具必须 SSE + progress 心跳，不能靠客户端调大 timeout 硬扛。

## 测试方法（仍有效，非代码）

- **HTTP_PROXY 劫持 localhost**：任何访问 127.0.0.1 的 HTTP 客户端必须禁用代理（requests
  `trust_env=False` / urllib `ProxyHandler({})`），否则在有代理的开发机上必挂。
- **pkill/pgrep 自匹配陷阱**：`pkill -f "local-echo --tcp-port"` 时外层 shell 命令行自身含该串
  → 杀了自己。规避 `pgrep -f "[l]ocal-echo --tcp-port"`（字符类正则使模式不匹配字面自身）。
- **echo 连接生命周期语义是下游协议隐式契约**：local-echo bench :13337 改为响应后 10ms idle 主动 FIN
  → 暴露 zo 潜伏 UAF（relay/deinit 双 tun.close 竞态，zo 已修 tun_relayed）。此类行为变更落地后
  应主动触发下游项目全量压测回归。
- **单请求 / 全量铁律**：全链路 loopback 单请求 ≤100ms，超过当失败；全部功能+性能 1 分钟内跑完，
  超过 = 失败止损修根因，禁止降级/排除。

## 错误方向记录（勿再追）

- **sing-box Clash API 热重载不可行** → serve 直接完整配置启动（`PUT /configs` 只接受 Clash 格式，原生格式热重载端口不出现）。
- **xray Reality 凭证不可移植** → 与 sing-box Reality 私钥格式不兼容，各插件独立凭证（命名前缀区分）。
- **MCP stdio transport 多实例僵尸进程** → 切 HTTP transport（端口绑定天然互斥，物理杜绝）。
- **local-echo python 实现（echo_server.py）** → 2026-08-17 被 Go 单程序统一重写（替代 python + h2h3-echo 两进程）。
- **route.final 兜底非 direct 出站有数据面 bug（2026-08-25 撤销）** → 源码逐层核查
  （zigroute `Router.route` 空 rules 返 `final_tag=bench-out`；zigbox `engine/session.zig` L171-173
  final_tag 正确编译 + L248-252 bench-out recipe 正确登记 lazy 配方；dispatch → lazy getOrCreate
  标准分派）**无可见代码缺陷**；真实状态 = socks fd 耗尽 + 重量出站挂起，根因归出站侧（见 §10）。

## 遗留观察（已迁 zigbox findings「跨项目技术背景」区）

> 遗留观察项已迁 zigbox findings.md「跨项目技术背景」区。

---

## §11 local-cf-dev 插件调研（2026-08-28）

> 任务：查证 `local-cf-dev.md` 文档是否属实 + 设计 zigtester 新插件 `local-cf-dev`
> （本地部署 CF Workers/Pages 代理，配合 zigbox/zigoutbounds 做 VLESS/Trojan 协议 E2E）。
> 参考项目已克隆到 `vendor/Cloudflare-vless-trojan`（yonggekkk/Cloudflare-vless-trojan，
> commit 25a9017，2026-06-17 起作者声明暂停维护）。

### A. `local-cf-dev.md` 文档查证结论：**基本属实，用途是「通用 CF 本地开发教程」，非本项目 E2E 方案**

逐条核对（对照 Cloudflare 官方 docs + wrangler 4.127 实测）：

| 文档主张 | 结论 | 说明 |
|---|---|---|
| `npx wrangler dev` 本地启动 Workers（默认 8787） | ✅ 属实 | 基于 workerd + Miniflare 模拟器 |
| `npx wrangler pages dev <dir>` 本地 Pages（默认 8788） | ✅ 属实 | 含 functions 目录后端 |
| KV/D1/R2 本地模拟（SQLite / 临时目录） | ✅ 属实 | Miniflare 本地模拟，`.wrangler/state/` 持久化 |
| `.dev.vars` 读环境变量/密钥 | ✅ 属实 | 本地 secret，不随部署上传 |
| `wrangler d1 execute --local` / `--remote` | ✅ 属实 | `--local` 只写本地 SQLite |
| `.wrangler/` 隐藏目录存本地状态 | ✅ 属实 | 删除即清空重来 |
| `wrangler login` / `deploy` 部署云端 | ✅ 属实 | 与离线测试无关 |

**两处需修正/补充**（对"离线 E2E"目标关键）：
1. 文档通篇**未提 `cloudflare:sockets` `connect()`**——这是本项目 worker 的核心能力
   （`import { connect } from "cloudflare:sockets"`），本地 `wrangler dev` 用 workerd
   运行时**完整支持**，这是能在本地做 VLESS/Trojan E2E 的根基。
2. 文档**完全没提 ECH**。ECH（Encrypted Client Hello）是 CF 边缘的 TLS 终止特性，
   **本地 workerd 不做 ECH 终止，无法离线测 ECH**。用户目标「最好含 ech」在此不可行，
   详见 C 节结论。

### B. Cloudflare-vless-trojan 项目结构（协议面）

- 两套 worker（均为单文件 JS，明文版 `_worker明.js` ~2465/2664 行，混淆版 `_worker.js`）：
  - `Vless_workers_pages/` — VLESS over WebSocket（默认 uuid `86c50e3a-5b87-49dd-bd20-03c7f2735e40`）
  - `Trojan_workers_pages/` — Trojan over WebSocket（默认密码 `trojan`）
- 支持协议：**Workers** = vless+ws+tls / trojan+ws+tls / vless+ws / trojan+ws；**Pages** = vless+ws+tls / trojan+ws+tls
- worker 数据路径（`_worker明.js`）：
  1. `fetch` 收 `Upgrade: websocket` 请求（非 WS 请求走「查看配置/订阅」页面）
  2. `vlessOverWSHandler` 建 `WebSocketPair`、`webSocket.accept()`，读 `sec-websocket-protocol` 作 early data
  3. 首 chunk 过 `processcloudflareHeader` 解析 VLESS 头（version+uuid+optLen+command+port+atyp+addr）
  4. `handleTCPOutBound` → `connect({hostname, port})`（`cloudflare:sockets`）连目标，双向 relay
  5. 失败 retry 兜底走 `proxyIP || addressRemote`
- 环境变量：`uuid`/`proxyip`/`cdnip`/`ip1~ip13`/`pt1~pt13`（经 `env.*` 读，wrangler `.dev.vars` 注入）
- **ECH 真相**：README 里的「ECH-TLS / enable_ech」属于**搭建方式 1 的本地客户端**（`cfsh.sh`/
  Docker `ygkkk/cfsh`，一个 Go 写的 Socks5/Http 本地代理，作为**客户端**带 ECH 连 CF 边缘），
  **不是** worker 的能力。worker 只是 CF 边缘后的代理脚本，ECH 由 CF 边缘对外终止，与 worker 无关。

### C. 关键技术事实（决定插件设计边界）

1. **本地 workerd 支持 `cloudflare:sockets connect()`**，且本地模式**不限制连私有/回环地址**
   （生产 CF 会拦 127.x/10.x，本地 dev 不拦）——所以 worker 本地可 `connect({hostname:"127.0.0.1"})` 连 local-echo。
2. **`localhost` 有 IPv4/IPv6 二义**（Node 可能解析成 `::1`，workerd 解析成 `127.0.0.1`，
   PR #12913 已修内部通信）——**一律用 `127.0.0.1`，别用 `localhost`**。
3. **`--local-protocol https`（或 `[dev] local_protocol="https"`）启用本地 HTTPS**（自签名证书），
   供 `vless+ws+tls` 客户端连入。注意：这是**入站** TLS（客户端连 workerd），客户端需 skip 证书校验；
   与 workerd **出站** fetch 不信任自签证书是两码事。
   - **补充（08-28 实测 wrangler 4.127）**：`wrangler dev` 原生支持 `--https-cert-path` /
     `--https-key-path` 指定自定义证书（本机 `npx wrangler dev --help` 第 35-36 行确认）。
     插件据此**复用生态 localhost 证书**（`plugins/local-echo/certs/localhost.crt`+`localhost.key`，
     SAN 含 `DNS:localhost` + `IP:127.0.0.1`，PKCS#8），客户端侧信任处理与 local-echo TLS echo 统一，
     不必为 wrangler 随机生成的证书单独配置。
4. **worker 会把 IPv4 目标重写成 `www.<ip>.sslip.io`**（`connectAndWrite` 内，
   `atob('d3d3Lg==')+ip+atob('LnNzbGlwLmlv')`）——本地 E2E 若让客户端发 IP 目标会走外网 DNS。
   **解法：E2E 客户端 VLESS 目标地址用域名（atyp=0x02，如 `localhost`）**，worker 不重写、直连本机。
5. **ECH 本地不可测**（定论）：workerd 不做 ECH 终止，本地无 CF 边缘的 ECHConfigList/SNI 加密。
   插件对 E2E 只覆盖 **VLESS/Trojan + WS + TLS（非 ECH）**；ECH 留作文档说明 + 未来「真连 CF 边缘」场景。
6. **localhost 证书系统信任（08-28 实测落地）**：
   - **系统 Keychain**：`sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain
     plugins/local-echo/certs/localhost.crt`（exit=0）。Apple curl（`/usr/bin/curl`，SecureTransport 读
     系统 Keychain）`https://localhost` → 200 信任；Safari/Chrome 同走系统 Keychain 信任。
   - **Homebrew curl（`/opt/homebrew/bin/curl`，LibreSSL）不读系统 Keychain**，且其编译 CAfile 是
     `/etc/ssl/cert.pem`（**非** Homebrew `/opt/homebrew/etc/ca-certificates/cert.pem`——LibreSSL 默认值，
     `curl -V` 确认）。落地 = **合并副本 + ~/.curlrc**（不动 root 所有的系统文件）：
     `cat /etc/ssl/cert.pem localhost.crt > ~/.local/share/curl-ca/curl-ca-with-localhost.pem` +
     `~/.curlrc` 写 `cacert = <合并文件>`。双向实测 localhost → 200、example.com → 200（公共 CA 未破坏）。
     trade-off：合并副本不随 /etc/ssl/cert.pem 更新，公共 CA 极少新增，可随时重生成。
   - Firefox 走 NSS，需 `security.enterprise_roots.enabled=true` 才读系统 Keychain。
   **生态实际影响**：E2E 测试脚本/zig 客户端本就用 skip-cert-verify，此信任仅为 curl/浏览器手动
   调试 HTTPS 场景提供便利，非协议 E2E 硬依赖。
7. **wrangler dev 实测通过（08-28，subagent 三层验证全绿）**：render → 冒烟 → relay 全 PASS。
   - 冒烟：`[wrangler:info] Ready on http://127.0.0.1:18787`，非 WS GET 返回 200；配置/订阅页在
     `/<uuid>` 路径（`/` 返回 request.cf JSON——两者均 200，不影响）。
   - relay：VLESS 头 32 字节（version+uuid+optLen+command+port+atyp+域名）经 WS 帧解析，
     payload `ping-hello` relay 到 echo 原样回显（剥离 2 字节响应头后 == payload）。
   - **workerd 二进制已在 `~/.npm/_npx/.../@cloudflare/workerd-darwin-arm64` 缓存**（wrangler 4.127.0
     已装），**实测首跑无需网络拉取**——此前「首跑需一次网络」结论偏保守，实际 `npx wrangler --version`
     时已触发缓存。
   - 客户端非优雅断开时 workerd 记 `Network connection lost` NOSENTRY 警告 = 正常 teardown。
   - worker `retry()` 兜底：proxyIP 空串时远端连接无数据会重连空 host:443，echo 即时回写未触发；
     真实协议套件首包即发数据，风险低、后续留意。

### D. 插件设计决策（详见 task_plan.md 本次阶段）

- 复用 `singbox_ctl.py` / `xray_ctl.py` 的 `serve` 模式：`cfdev_ctl.py` 渲染 wrangler 项目 + 启 `wrangler dev`。
- 依赖：Node 24 + npx（wrangler 4.127 已随 proxy 装好；workerd 二进制已缓存于 ~/.npm/_npx/，
  实测首跑无需网络，见 C.7）。
- 端口：workers 18787 / pages 18788（避开 8787/8788 官方默认，防本机冲突）。
- worker 源：经 config 引用 `vendor/Cloudflare-vless-trojan/.../_worker明.js`（唯一真相源在 vendor，插件内不复制 2465 行混淆 JS）。
- **协议覆盖边界（定论）**：插件只覆盖 VLESS / Trojan 两种 worker（`worker_type: vless|trojan`）。
  vendor 第三套 `s5http_wkpgs/`（Socks5/HTTP over WS）**不接入**——socks5/http 入站测试已由
  sing-box 插件原生覆盖，本地 CF 无需重复。二者分工：sing-box 测原生 socks5/http，
  local-cf-dev 专测 CF 特有的 VLESS/Trojan-over-WS 生产形态。
