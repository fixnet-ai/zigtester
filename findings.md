# 有效技术结论

> 只保留仍然有效的技术结论；过程流水账已删除。与 [DESIGN.md](DESIGN.md) 冲突时以 DESIGN.md 为准。
>
> **二次瘦身（2026-08-23）**：① 技术定论均已下沉到对应代码头部/函数注释，此处只留「定论位置表」指针；
> ② Zig 0.16 语言经验已移出（跨项目通用，交 zig-codegen 汇总，不在此重复）；③ 久远历史阶段
> （Phase 1-12 过程）、被推翻结论、纯实验过程已删除。**本文件保留章节号（§X）供 task_plan
> 历史总表跳转与 git 追溯。**

## 定论位置表（技术定论已下沉代码注释，不在此重复正文）

| 机制/定论 | 代码位置 |
|-----------|---------|
| MCP 长任务 SSE + progress 心跳（`json_response=False`） | `src/zigtester/server.py` `_run_with_progress` + `main()` |
| HTTP transport（端口绑定互斥，杜绝多实例） | `src/zigtester/server.py` 模块头 |
| 资源采集 `target` 字段（只采目标被测程序） | `src/zigtester/monitor.py` 模块头 + `_collect` |
| per_suite_only（禁止 --level 全量压测） | `src/zigtester/runner.py` `run_project` + `config.py` |
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

## 最近关键定论（2026-08-18 → 08-25）

### §9 性能测试架构重构调研（2026-08-25，zigbox 会话，移交本项目推进）

- **sing-box test_server 实际 inbound（19 个）**：mixed:2080、ss:8388、trojan:9443、hy2:10443、
  vmess:16800、vless:16801、hy2-alt:16802、tuic:16803、hy2-salamander:16804、vless-ws:16805、
  trojan-ws:16806、vless-grpc:16807、trojan-grpc:16808、shadowtls:16809、shadowtls-ss:16810、
  anytls:16811、vless-reality:16812 → final→local-echo。zigbox 已实现出站客户端：
  socks/http/ss/trojan/hy2/vless/tuic/anytls。
- **xray test_server 实际 inbound（10 个）**：socks:2180（**无认证**）、ss:8488、trojan:9543、
  vmess:16900、vless-tls:16901、vless-ws:16905、vless-grpc:16907、trojan-grpc:16909、
  vless-flow:16908、vless-reality:16906 → freedom→echo。zigbox 可连：socks/ss/trojan/vless-tls。
- **框架约束**：`VALID_LEVELS` 硬编码 3 层（config.py:16）；parse_config 只遍历 VALID_LEVELS（:334）
  → 新层级需改 VALID_LEVELS + schema（schema `additionalProperties:false`，levels 仅 3 键）。
  `per_suite_only` = suite_filter is None 时跳过（runner.py:606-613）。`analyze_leak` 自动开 =
  level=="performance" and "long" in name（config.py:273-276），显式 `analyze_leak` 优先。
- **复用点**：zigbox `tests/lib/matrix.py`（`_heavy_outbound` 单出站配置 / TAG 常量 / `_dns_servers`）、
  `singbox_pair.py`（ensure_pair 只探测不启停）、zigoutbounds `tests/lib/xray.py`
  （plugin_ports 派生 `xray_port`/`xray_credential`/`xray_readiness_port` 模式）、
  zigbox `tests/lib/zigbox.py`（ZigboxProcess 生命周期 + `_cleanup_leftover` 等锁）。
- **缺口**：test_bench.py 无「短连接+时长」模式（`-n` 短连接计数 / `-d` keep-alive 时长二分）
  → 标准场景需新增。

### §10 bench-standard-outbound 数据路径诊断（2026-08-25，zigbox 遗留）

**症状**（zigtester_run 单套件 ×2 独立 run）：`--standard-outbound` 14 cell 无法通过。一次 exit_code=0
但 `resource_limits.fd_count=500` 超限 FAIL（peak_fd=2048）；一次 300s 超时（exit -1，同样 peak_fd=2048）。

**根因定位（已排除项）**：
- ❌ 凭证：zigbox config.py ss/trojan 等凭证经 `plugin_ports` 由 plugin.yaml 派生（非硬编码），与
  zigoutbounds bench-tcp-shadowsocks PASS（08-23，同凭证）同源 → 对端+凭证正确
- ❌ 配置字段：build_bench_config 生成 sing-box 风格 `{type, method, password}`；zo shadowsocks 实现
  （`zigoutbounds/src/protocol/shadowsocks.zig`）读 `sc.method`（parseMethod 支持 2022-blake3-*）+
  `sc.password` → 字段名匹配
- ❌ 对端：sing-box / xray 插件 up，端口可达（scenarios 57/57 证明）
- ❌ `_dns_servers()`：单 server（dns-real），非 fd 源

**逐 cell 实测图景**（`/tmp/zigbox-tests-dasimo/zigbox-pid-tracker.log` + cell 配置 mtime 对齐）：
| cell | zigbox 日志 | 耗时 | 行为 |
|------|------------|------|------|
| direct / http / socks-xray | 0B | 6-7s | 正常（请求快速失败或成功） |
| socks | 1.3MB `[server] accept failed: ProcessFdQuotaExceeded`（23644 行纯刷屏） | 6s | fd 耗尽（EMFILE） |
| redirect | 373KB `[redirect] tcp socket create failed: dest=127.0.0.1:18080` + `[hook] outbound connectTcp failed: SocketFailed`（2744 对，**无 EMFILE**） | 7s | **Linux 专用透明重定向，macOS 平台不支持**（非 fd 泄漏、非数据路径 bug） |
| ss/trojan/hy2/vless/tuic/anytls + ss-xray/trojan-xray | 0B | 各 31s | **请求挂 30s 无响应**（第一个请求超时） |
| vless-xray | 0B | 卡死 | 300s 超时被杀 |

> ⚠️ **redirect 归因修正（用户裁定，08-25）**：redirect 是 Linux 专用透明重定向（需透明代理能力），
> 不是代理协议。它在 macOS 上 socket create failed 是**平台不支持**（日志无 EMFILE），不构成数据路径
> bug 证据——14 cell 在 macOS 上枚举 redirect 本身是套件设计问题（见 task_plan #89 处置建议）。

**对照（scenarios vs bench 组合差异，根因未定位）**：scenarios 矩阵（08-25 当天 5 次 PASS）用 `build_config`：
`route.rules` 规则分流到重量出站 + `final=direct` + `sniff=True` + **目标域名**（socks5h google.com/echo.proxy → ATYP=3）
+ echo 端口 13333。standard-outbound 用 `build_bench_config`：`rules=[]` + `final=bench-out` + `sniff=False`
+ **目标纯 IP 127.0.0.1:18080**（socks5 非 h → ATYP=1）+ HTTP echo 端口 18080。两者 `_dns_servers` 相同
（单 dns-real），inbounds 同为 mixed-in:12080，重量出站配置字段与凭证逐字一致（`_bench_outbound` vs
`_heavy_outbound`）。

> ⚠️ **强假设撤销（2026-08-25 源码核查）**：此前"zigbox 对 route.final 直接兜底到非 direct 出站有数据面
> bug"为**推断、未定位**。逐层核查 final 兜底路径代码正常：`zigroute/src/route.zig` Router.route（空 rules →
> 返回 `final_tag=bench-out`）；`zigbox/src/engine/session.zig` L171-173 final_tag 正确编译 + L248-252 bench-out
> recipe 正确登记 lazy 配方；dispatch → lazy getOrCreate 标准分派。**该路径无可见代码缺陷。** 真实状态 =
> 症状确凿（socks fd 耗尽 + 重量出站 30s 挂起）但根因未归因到具体环节；与 scenarios 的差异是多维组合
> （final/sniff/rules × IP目标/域名 × 18080/13333）。定位需最小配置实验（改 build_bench_config 对齐
> scenarios 单维度，单跑 ss cell），见 zigbox task_plan #89。证据文件：`/tmp/zigbox-tests-dasimo/` 下
> bench-*.json + zigbox-*.log + zigbox-pid-tracker.log。

**处置进展（2026-08-25，zigbox 落实）**：
- **EOF 语义定案**：重量出站 30s 挂起 = HTTP 客户端等 EOF 而 sing-box 不转发上游 FIN → CL 语义
  （`test_bench.read_by_cl` 默认开）后全部重量出站 PASS（含 ss/trojan/hy2/vless/tuic/anytls +
  trojan-xray/vless-xray）。
- **ss-xray 剔除（用户裁定）**：zigbox SS 客户端 ↔ xray 2022-blake3-aes-128-gcm 握手不兼容
  （connect EV_EOF，sing-box 对端正常），剔除非修复；`bench_outbound_cells()` 已删该 cell
  （调研归 zigoutbounds task_plan『已裁定』+ findings §51）。
- **redirect 平台守卫已落地**：`bench_outbound_cells()` 非 Linux 不枚举（macOS 12 cell）。
- **剩余（确定性复现 ×2）**：**socks + socks-xray fd 耗尽**——24593 条
  `[server] accept failed: ProcessFdQuotaExceeded`，679 成功请求 ×3 fd ≈ 2048 peak 连接不释放，
  两 socks cell 同症、与对端（sing-box/xray）无关 → **zigbox socks 出站连接泄漏**，疑点
  zo.socks5 出站 + TcpBridge 关闭传播，待定位（见 zigbox #89）。

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

- **HTTP_PROXY 劫持 localhost**：任何访问 127.0.0.1 的 HTTP 客户端必须禁用代理，否则在有代理的开发机上必挂。
- **pkill/pgrep 自匹配陷阱**：测试脚本 `pkill -f "local-echo --tcp-port"` 时外层 shell 命令行自身含该串
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

## 遗留观察（已迁 zigbox findings「跨项目技术背景」区）

> 遗留观察项已迁 zigbox findings.md「跨项目技术背景」区。
