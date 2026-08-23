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

## 最近关键定论（2026-08-18 → 08-23）

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

## 遗留/观察（未修）

- 端口真相源五处收敛（task_plan 遗留 #2）
- Go 测试工具 HTTP_PROXY 隐患（task_plan 遗留 #3）
- local-echo 极端满载启动偶发失败（task_plan 遗留 #4）
