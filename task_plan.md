# zigtester 项目规划

> **架构设计唯一来源：[DESIGN.md](DESIGN.md)。** 本文档只做执行计划，不重复设计。
>
> **规划瘦身（2026-08-23）**：Phase 1-12 详细执行记录已压缩为「历史完成阶段总表」——每阶段一行
> （编号｜标题｜一句话结果｜锚点）。历史决策完整推导见 git log + DESIGN.md；
> 同步二次瘦身：findings.md 440→72 行、progress.md 405→46 行（久远流水/错误方向已删，
> 技术定论已在代码头部注释，findings 只留指针表）。**已删章节锚点一律以 git log 为准。**
> 本文档只承载仍在生效的决策与未完成工作。

## 当前状态（2026-08-23）

- **全部 Phase 1-12 完成**。最近收尾：插件 host 化（P12，2026-08-21，配合 zigbox #69-④ 三 VM 实测）；
  MCP 长任务 SSE + progress 心跳（2026-08-21）；per_suite_only（2026-08-22）；
  资源采集 target 字段（2026-08-23，只采目标被测程序）。
- **兄弟项目全部接入 zigtester**：zigfoundation/zigbox/zigtun/zigproxy/zigdns/zigoutbounds + zigroute/zigunicfg。
- **zigtester 自身单测全绿**：test_env_guard 15 + test_report_history 15 + test_per_suite_only 4 + test_target_monitor 5。

## 历史完成阶段总表（Phase 1-12，全部完成）

> 执行细节与决策推导见 git log + DESIGN.md + 代码头部注释（findings 二次瘦身后只留指针表）。

| 阶段 | 标题 | 一句话结果 | 锚点 |
|------|------|-----------|------|
| 1 | zigbox 试点替换 | tests skill → stub（-81%）、zigbox-outbound-dev 删除；token -83% | git log |
| 2 | zigtester.yaml 全覆盖 | 为 zigtun/zigproxy/zigdns 生成配置；scan 可发现全部项目 | git log |
| 3 | 简单项目接入 | 各项目 CLAUDE.md 补「测试」节指向 zigtester MCP | git log |
| 4 | zigoutbounds skill 替换 | tests → stub（-89%）、outbound-dev 删除迁 CLAUDE.md | git log |
| 5 | 最终验证 | scan 全 6 项目 + `--all --level unit` 全绿；token 合计 -93% | git log |
| 6 | 测试生命周期管理 | setup/teardown + readiness probe + 插件体系 + local-echo 迁移（Python→Go） | git log |
| 7 | sing-box 插件 | 统一配置 test_server.json + serve 直启（Clash API 热重载不可行）+ 端口表 | git log + singbox_ctl.py |
| 8 | xray-core 插件 | 插件本体 + 跨插件端口冲突检测；P8.3 zigoutbounds 接入见遗留待办 #1 | git log + xray_ctl.py |
| 9 | 环境统一治理 + pre-flight | PluginManager 三层校验 + 自愈 + fast fail + 生态铁律 | git log + plugin.py |
| 10 | 测试流畅性改进 | failure_lines / flaky 检测 / 分组并行 / scenarios fast fail | git log |
| 11 | flaky 根因修复 + 插件管道排空 | zigproxy 客户端超时缺陷 + zigdns FakeIP 期望 + PIPE drain | git log |
| 12 | 插件 host 化 | 远程插件模式（host 优先级：plugin_ref > plugin.yaml > env > 127.0.0.1） | git log + plugin.py |

**关键生效决策（最高优先级，勿改）**：

- **测试环境治理铁律（生态级，用户裁定）**：所有 zig* 项目测试一律经 zigtester 执行；兄弟项目测试脚本只探测依赖（detect-and-error），绝不自启 / 手动启停 echo/sing-box/xray；环境异常时唯一正确动作 = 重新 `zigtester run`（自愈恢复）。
- **访问 127.0.0.1 的 HTTP 客户端必须禁用代理**（requests `trust_env=False` / urllib `ProxyHandler({})`）——本机 HTTP_PROXY 会劫持 localhost 请求导致假判「插件未运行」。
- **MCP 长任务 = SSE 流 + progress 心跳**（`json_response=False`）；**HTTP transport 端口绑定天然互斥** 杜绝多实例僵尸进程。
- **压测禁止 `--level` 全量**：`per_suite_only` 套件只允许 `--suite` 单独运行。
- **资源采集 `target` 字段**：只采目标被测程序，未匹配跳过采样（防 python 包装进程污染）。

## 遗留待办

1. **Phase 8 P8.3 zigoutbounds xray 接入（原规划，需核实）** — 原计划 = `test_protocols.py` 加
   xray 复用分支 + `--cross-impl` + zigtester.yaml 加 xray-* 套件。实际已以「xray-core 插件 +
   阶段 14 REALITY 直连套件」形态整合（zigoutbounds `zigtester.yaml` 已引用 xray-core 插件，
   含 xray-reality-verify / xray-reality-bench / xray-client-probe 等套件）。原
   cross-impl 双参考实现对比（同协议同时打 sing-box 与 xray）未按原样落地；若仍需，后续补。
2. **端口真相源五处收敛**（中工程量，涉及多项目脚本改造）— zigtester CLAUDE.md /
   plugin.yaml ports / 各项目 tests/lib/config.py → plugin.yaml 单源。
3. **Go 测试工具 HTTP_PROXY 隐患验证** — grpc-verify 等若用 net/http 默认 ProxyFromEnvironment，
   同 Python 坑；遇「端口在听却连不上」先查此项。
4. **local-echo 极端满载启动偶发失败观察**（未修）— 10 核 CPU spinner 满载下 4 次中 1 次 ERROR，
   失败阶段未定位；复现时先查 `/tmp/zigtester-plugin-local-echo.log`。
5. **Windows 侧框架遗留** — pkill / /tmp POSIX 假设已登记 zigbox #63（跨仓跟踪）。

## 测试门禁

`zig build test` 全绿 → 集成 E2E（sing-box / xray）→ 性能（按需）。单请求 ≤100ms、全量 1 分钟内跑完。
详见 DESIGN.md 与用户级 CLAUDE.md § 测试脚本可用性。
