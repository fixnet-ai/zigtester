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
