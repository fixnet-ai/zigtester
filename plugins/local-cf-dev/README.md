# local-cf-dev 插件

本地部署 Cloudflare Workers 代理（[yonggekkk/Cloudflare-vless-trojan](https://github.com/yonggekkk/Cloudflare-vless-trojan)
的 VLESS/Trojan-over-WebSocket worker），经 `wrangler dev`（workerd 运行时）在**离线闭环**内跑
zigbox / zigoutbounds 的 VLESS / Trojan + WS（+TLS）协议 E2E。

## 为什么需要它

zigbox / zigoutbounds 已有 vless / trojan 出站客户端，且已对 sing-box / xray 的 vless/trojan
入站做过 E2E（`bench-tcp-vless` / `bench-tcp-trojan` 等）。但 CF Workers 代理是一类**真实生产形态**
（VLESS/Trojan 跑在 `WebSocket` 之上、由 workerd 解析协议头），其数据路径与 sing-box/xray 的
原生入站不同。本插件把这种形态搬回本机，离线测通 zig* 客户端 ↔ CF worker 的协议互通。

## 离线数据路径（闭环）

```
zigbox vless+ws 客户端
  └─> 127.0.0.1:18787（wrangler dev / workerd，本地）
        worker 解析 VLESS 头（version+uuid+command+port+atyp+addr）
        └─> cloudflare:sockets connect({hostname:"localhost"})
              └─> 127.0.0.1:13333（local-echo echo，回显）
                    └─> 回程
```

全链路不触网；`cloudflare:sockets connect()` 在本地 workerd 中完整可用，且本地模式不拦回环地址。

## 用法

```bash
# 消费方项目 zigtester.yaml 声明插件（自动 build + start + 自检 + stop）
plugins:
  - local-cf-dev                # 默认 vless worker，http 模式，端口 18787
  # - name: local-cf-dev
  #   config:
  #     local_protocol: https   # vless+ws+tls 场景
  #     worker_type: trojan     # 切 Trojan worker
```

## 关键事实（务必知悉）

1. **ECH 本地不可测**。ECH（Encrypted Client Hello）是 CF **边缘**的 TLS 终止特性，本地 workerd
   不做 ECH 终止，也没有 CF 边缘的 ECHConfigList。本项目 README 里的「ECH-TLS / enable_ech」属于
   搭建方式 1 的**本地客户端**（`cfsh.sh` / Docker `ygkkk/cfsh`，Go 写的 Socks5/Http 本地代理，作为
   客户端带 ECH 连 CF 边缘），**不是 worker 的能力**——worker 是 CF 边缘之后的代理脚本，与 ECH 无关。
   故本插件 E2E 只覆盖 **VLESS/Trojan + WS + TLS（非 ECH）**；ECH 留待「真连 CF 边缘」场景。
2. **目标地址用域名，别用 IP**。worker 会把 IPv4 目标重写成 `www.<ip>.sslip.io`（需外网 DNS），
   所以 E2E 客户端的 VLESS 目标地址必须是域名（atyp=0x02，如 `localhost`），worker 才直连本机不回环外网。
3. **一律 `127.0.0.1`，别用 `localhost`**。Node 可能把 `localhost` 解析成 `::1`，而 workerd 解析成
   `127.0.0.1`，二者不一致会导致连不上（workers-sdk PR #12913）。
4. **首跑需一次网络**：`npx wrangler dev` 会拉 workerd 二进制；npx 缓存后即可离线。`build.command`
   （`npx wrangler --version`）是存在性检查，失败时 zigtester 标记插件不可用。
5. **TLS 模式**：`local_protocol: https` 让 wrangler dev 以 HTTPS 提供，供 `vless+ws+tls` 客户端连入。
   **复用生态 localhost 证书**（`plugins/local-echo/certs/localhost.crt` + `localhost.key`，SAN 含
   `DNS:localhost` + `IP:127.0.0.1`，PKCS#8）——经 `wrangler dev --https-cert-path` / `--https-key-path`
   注入，客户端侧可用与 local-echo TLS echo 一致的信任处理，不必为 wrangler 每次生成的随机自签名证书
   单独配置。客户端仍需 skip-cert-verify / insecure（证书自签名，非 CA 签发）。这与 workerd **出站**
   fetch 不信任自签证书是两码事（本插件只测入站，不涉及出站 fetch 到本地 HTTPS）。

## 文件

| 文件 | 作用 |
|------|------|
| `plugin.yaml` | 插件清单（config 端口/凭证真相源 + build + lifecycle） |
| `cfdev_ctl.py` | 渲染 wrangler 项目 + 启 `wrangler dev`（serve 模式，同 singbox_ctl/xray_ctl） |
| `tests/cfdev_echo_server.py` | 独立 echo（relay 闭环验证，替代 local-echo） |
| `tests/cfdev_vless_ws_client.py` | VLESS-over-WS 裸 socket 客户端（插件自检，仅标准库无依赖） |

worker 源经 `config.vendor_worker_dir` 引用 `vendor/Cloudflare-vless-trojan`（唯一真相源），
本插件不复制 2465 行混淆 JS；vendor 缺失时启动失败并给明确报错。
