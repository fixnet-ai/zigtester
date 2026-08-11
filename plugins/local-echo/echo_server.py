#!/usr/bin/env python3
"""local-echo — TCP echo + DNS echo 服务器，用于 zigbox 代理链路测试。

完全复刻 zigbox/src/local-echo.zig 的功能，使用 Python asyncio 实现：
  - TCP Echo（默认 :13333）— 协议自适应（SOCKS5 / HTTP CONNECT / HTTP）
  - DNS Echo（默认 127.0.0.1:53，需 sudo）— 选择性 DNS 代理（测试域名→FakeIP，其余→上游转发）

设计原则:
  - 启动即服务，无生命周期状态管理 — 进程活着就服务
  - asyncio 高性能异步 IO，对性能测试结果影响最小
  - 零外部依赖（仅 Python 3.10+ 标准库）
  - 跨平台（macOS / Linux / Windows）

用法:
  python3 echo_server.py [--tcp-host ::] [--tcp-port 13333]
                         [--dns-port 53] [--upstream-dns 8.8.8.8]
                         [--no-dns] [--quiet]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import signal
import socket
import struct
import sys
import time
from typing import Optional

# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

# 测试域名（与 local-echo.zig TEST_DOMAINS 一致）
TEST_DOMAINS = frozenset({"echo.direct", "echo.proxy", "echo.block"})

# DNS 查询类型
QTYPE_A = 1
QTYPE_AAAA = 28
QCLASS_IN = 1

# FakeIP 范围（与 zigbox TUN 198.18.0.0/15 一致）
FAKEIP_V4_NET = "198.18"
FAKEIP_V6_PREFIX = "fd18"

# ═══════════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════════

logger = logging.getLogger("local-echo")


def setup_logging(quiet: bool = False) -> None:
    """配置日志：默认 INFO 级别，--quiet 降为 WARNING。"""
    logging.basicConfig(
        level=logging.WARNING if quiet else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


# ═══════════════════════════════════════════════════════════════
# FakeIP 分配（确定性哈希，每次运行同一域名分配相同 IP）
# ═══════════════════════════════════════════════════════════════

class FakeIpPool:
    """确定性 FakeIP 分配器 — MD5(域名) → 198.18.x.x / fd18::x:x:x"""

    def __init__(self) -> None:
        self._v4: dict[str, str] = {}
        self._v6: dict[str, str] = {}

    def get_v4(self, domain: str) -> str:
        if domain not in self._v4:
            h = hashlib.md5(domain.encode()).digest()
            a = h[0] & 0x7F          # 0..127（198.18 范围上半段）
            b = h[1]
            self._v4[domain] = f"{FAKEIP_V4_NET}.{a}.{b}"
        return self._v4[domain]

    def get_v6(self, domain: str) -> str:
        if domain not in self._v6:
            h = hashlib.md5(domain.encode()).digest()
            self._v6[domain] = (
                f"{FAKEIP_V6_PREFIX}::"
                f"{h[0]:02x}{h[1]:02x}:{h[2]:02x}{h[3]:02x}"
            )
        return self._v6[domain]


# 全局 FakeIP 池（进程生命周期内缓存）
_fakeip = FakeIpPool()


# ═══════════════════════════════════════════════════════════════
# DNS 线格式处理
# ═══════════════════════════════════════════════════════════════

def _encode_dns_name(name: str) -> bytes:
    """域名编码为 DNS 线格式（长度前缀标签序列 + 零终止）。"""
    result = b""
    for label in name.rstrip(".").split("."):
        encoded = label.encode("ascii", errors="replace")
        result += bytes([len(encoded)]) + encoded
    result += b"\x00"
    return result


def _decode_dns_name(data: bytes, offset: int) -> tuple[str | None, int]:
    """解码 DNS 线格式域名（含压缩指针支持）。

    返回 (域名, 消耗字节数) 或 (None, offset) 表示解析失败。
    """
    labels: list[str] = []
    jumped = False
    orig_offset = offset

    for _ in range(10):  # 循环检测上限（防畸形包无限循环）
        if offset >= len(data):
            return None, offset
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            # 压缩指针：取低 14 位作为新偏移
            if offset + 2 > len(data):
                return None, offset
            pointer = struct.unpack("!H", data[offset:offset + 2])[0] & 0x3FFF
            if not jumped:
                orig_offset = offset + 2
            offset = pointer
            jumped = True
        else:
            offset += 1
            if offset + length > len(data):
                return None, offset
            labels.append(data[offset:offset + length].decode("ascii", errors="replace"))
            offset += length
    else:
        return None, offset

    if not jumped:
        orig_offset = offset
    return ".".join(labels), orig_offset


def parse_dns_query(data: bytes) -> tuple[int, str, int] | None:
    """解析 DNS 查询包，返回 (tid, domain, qtype) 或 None。"""
    if len(data) < 12:
        return None
    tid, _flags, qdcount, _ancount, _nscount, _arcount = \
        struct.unpack("!HHHHHH", data[:12])
    if qdcount != 1:
        return None
    domain, offset = _decode_dns_name(data, 12)
    if domain is None or offset + 4 > len(data):
        return None
    qtype, _qclass = struct.unpack("!HH", data[offset:offset + 4])
    return tid, domain, qtype


def build_dns_response(tid: int, domain: str, qtype: int, ips: list[str]) -> bytes | None:
    """构建 DNS 响应包。ips 中的 IP 地址类型必须与 qtype 一致。"""
    flags = 0x8180  # 标准响应，无错误
    qdcount = 1
    ancount = len(ips)

    # 头部 (12B)
    response = struct.pack("!HHHHHH", tid, flags, qdcount, ancount, 0, 0)
    # 问题段 — 域名 + QTYPE + QCLASS
    response += _encode_dns_name(domain)
    response += struct.pack("!HH", qtype, QCLASS_IN)

    # 应答段 — 每个 IP 一条 A/AAAA 记录
    for ip in ips:
        response += b"\xc0\x0c"  # 名称压缩指针 → 偏移 12（问题段域名）
        if qtype == QTYPE_A:
            response += struct.pack("!HHIH", QTYPE_A, QCLASS_IN, 300, 4)
            response += socket.inet_aton(ip)
        elif qtype == QTYPE_AAAA:
            response += struct.pack("!HHIH", QTYPE_AAAA, QCLASS_IN, 300, 16)
            response += socket.inet_pton(socket.AF_INET6, ip)

    return response


# ═══════════════════════════════════════════════════════════════
# TCP Echo — 协议自适应（SOCKS5 / HTTP CONNECT / HTTP）
# ═══════════════════════════════════════════════════════════════

class EchoProtocol(asyncio.Protocol):
    """TCP 协议自适应 echo。

    状态机:
      detect → socks5_echo | connect_echo | http_close
    """

    def __init__(self) -> None:
        self.transport: asyncio.Transport | None = None
        self._state = "detect"
        self._peer: str = ""

    # ── asyncio.Protocol 接口 ──────────────────────────────────

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        peername = transport.get_extra_info("peername")
        self._peer = f"{peername[0]}:{peername[1]}" if peername else "?"
        logger.debug(f"[tcp] connect: peer={self._peer}")

    def data_received(self, data: bytes) -> None:
        if self._state == "detect":
            self._detect(data)
        elif self._state == "socks5_echo":
            self._echo(data)
        elif self._state == "connect_echo":
            self._echo_then_close(data)
        # http_close / connect_close: 忽略后续数据

    def connection_lost(self, exc: Exception | None) -> None:
        logger.debug(f"[tcp] close: peer={self._peer}")

    # ── 协议检测 & 响应 ───────────────────────────────────────

    def _detect(self, data: bytes) -> None:
        """首字节协议检测（与 local-echo.zig detectAndRespond 一致）。

        优先级:
          1. 0x05 → SOCKS5 握手 → 响应 0x05 0x00，进入 echo 模式
          2. CONNECT 前缀 → HTTP CONNECT 隧道 → 200 Connection Established
          3. 其他 → HTTP 请求 → 200 OK + 请求原文 body
        """
        if data and data[0] == 0x05:
            # SOCKS5 无认证握手
            self._state = "socks5_echo"
            if self.transport:
                self.transport.write(b"\x05\x00")
            logger.debug(f"[tcp] SOCKS5 detected: peer={self._peer}")
        elif len(data) >= 7 and data[:7].upper() == b"CONNECT":
            # HTTP CONNECT 隧道
            self._state = "connect_echo"
            if self.transport:
                self.transport.write(
                    b"HTTP/1.1 200 Connection Established\r\n\r\n"
                )
            logger.debug(f"[tcp] CONNECT detected: peer={self._peer}")
        else:
            # HTTP 请求 — 返回 200 OK + 请求原文 body，然后优雅关闭
            self._state = "http_close"
            header = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )
            if self.transport:
                self.transport.write(header + data)
                self.transport.write_eof()
            logger.debug(
                f"[tcp] HTTP detected: peer={self._peer} len={len(data)}"
            )

    def _echo(self, data: bytes) -> None:
        """SOCKS5 echo 模式：原样回显，逐包持续（不断开连接）。"""
        if self.transport:
            self.transport.write(data)

    def _echo_then_close(self, data: bytes) -> None:
        """CONNECT echo 模式：回显一次后优雅关闭。

        与 local-echo.zig onEchoWritten 行为一致：
        echo → shutdown(SHUT_WR) → drain → close
        """
        self._state = "connect_close"
        if self.transport:
            self.transport.write(data)
            self.transport.write_eof()
        logger.debug(f"[tcp] CONNECT echo+close: peer={self._peer}")


# ═══════════════════════════════════════════════════════════════
# DNS Echo — 选择性 DNS 代理（测试域名→FakeIP，其余→上游）
# ═══════════════════════════════════════════════════════════════

class DnsEchoProtocol(asyncio.DatagramProtocol):
    """DNS 回显服务器。

    测试域名（echo.direct/echo.proxy/echo.block）→ FakeIP 本地应答。
    localhost → 127.0.0.1 / ::1。
    其余域名 → 转发到上游 DNS，响应回传客户端。

    与 local-echo.zig DnsEchoServer 行为一致（不含 utun IP 层拦截）。
    """

    def __init__(self, upstream_dns: str) -> None:
        self.upstream_dns = upstream_dns
        self.upstream_addr = (upstream_dns, 53)
        self.transport: asyncio.DatagramTransport | None = None
        # pending: tid → (client_host, client_port)
        self._pending: dict[int, tuple[str, int]] = {}
        self._query_count: int = 0

    # ── asyncio.DatagramProtocol 接口 ──────────────────────────

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        # 区分上游响应 vs 客户端查询
        if addr[0] == self.upstream_dns and addr[1] == 53:
            self._handle_upstream(data)
        else:
            self._handle_client(data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.debug(f"[dns] UDP error: {exc}")

    def connection_lost(self, exc: Exception | None) -> None:
        pass

    # ── 客户端查询 ────────────────────────────────────────────

    def _handle_client(self, data: bytes, addr: tuple[str, int]) -> None:
        parsed = parse_dns_query(data)
        if parsed is None:
            return
        tid, domain, qtype = parsed

        if qtype not in (QTYPE_A, QTYPE_AAAA):
            return

        is_local = domain == "localhost" or domain in TEST_DOMAINS
        if is_local:
            self._respond_local(tid, domain, qtype, addr)
        else:
            self._forward_upstream(data, addr, tid)

    def _respond_local(
        self, tid: int, domain: str, qtype: int, addr: tuple[str, int]
    ) -> None:
        """本地应答：测试域名→FakeIP，localhost/echo.direct→127.0.0.1/::1。

        与 local-echo.zig buildDnsResponse 行为一致：
        echo.direct / localhost → 127.0.0.1（本地直连，不经过 TUN）
        echo.proxy / echo.block  → FakeIP（经 TUN 路由分流验证）
        """
        if domain == "localhost" or domain == "echo.direct":
            ip = "127.0.0.1" if qtype == QTYPE_A else "::1"
        elif qtype == QTYPE_A:
            ip = _fakeip.get_v4(domain)
        else:
            ip = _fakeip.get_v6(domain)

        response = build_dns_response(tid, domain, qtype, [ip])
        if response and self.transport:
            self.transport.sendto(response, addr)
            self._query_count += 1
            logger.debug(
                f"[dns] local: domain={domain} qtype={qtype} ip={ip} → {addr}"
            )

    # ── 上游转发 ──────────────────────────────────────────────

    def _forward_upstream(
        self, data: bytes, addr: tuple[str, int], tid: int
    ) -> None:
        """将非测试域名查询转发到上游 DNS。"""
        self._pending[tid] = addr
        if self.transport:
            self.transport.sendto(data, self.upstream_addr)
            self._query_count += 1
            logger.debug(
                f"[dns] forward: tid={tid} to={self.upstream_dns} from={addr}"
            )

    def _handle_upstream(self, data: bytes) -> None:
        """上游 DNS 响应 → 匹配 pending 表 → 回传客户端。"""
        if len(data) < 2:
            return
        tid = struct.unpack("!H", data[:2])[0]
        client_addr = self._pending.pop(tid, None)
        if client_addr and self.transport:
            self.transport.sendto(data, client_addr)
            logger.debug(f"[dns] relay: tid={tid} → {client_addr}")


# ═══════════════════════════════════════════════════════════════
# Raw UDP Echo — 原样回传 UDP 数据报 (透明代理 UDP 测试用)
# ═══════════════════════════════════════════════════════════════

class RawUdpEchoProtocol(asyncio.DatagramProtocol):
    """Raw UDP echo: 收到什么就回传什么, 不做任何解析。"""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self.transport:
            self.transport.sendto(data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.debug(f"[udp-echo] UDP error: {exc}")

    def connection_lost(self, exc: Exception | None) -> None:
        pass


# ═══════════════════════════════════════════════════════════════
# 服务器启动与管理
# ═══════════════════════════════════════════════════════════════

class EchoServer:
    """TCP echo + DNS echo 服务器管理。

    启动 TCP echo（asyncio TCP server）和 DNS echo（UDP endpoint），
    共享同一个 event loop。支持优雅关闭（SIGTERM/SIGINT）。
    """

    def __init__(
        self,
        tcp_host: str = "::",
        tcp_port: int = 13333,
        dns_port: int = 5353,
        udp_echo_port: int = 13334,
        upstream_dns: str = "8.8.8.8",
        no_dns: bool = False,
        no_udp_echo: bool = False,
    ) -> None:
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.dns_port = dns_port
        self.udp_echo_port = udp_echo_port
        self.upstream_dns = upstream_dns
        self.no_dns = no_dns
        self.no_udp_echo = no_udp_echo

        self._tcp_servers: list[asyncio.AbstractServer] = []
        self._dns_transport: asyncio.DatagramTransport | None = None
        self._dns_protocol: DnsEchoProtocol | None = None
        self._udp_echo_transport: asyncio.DatagramTransport | None = None
        self._shutdown_event = asyncio.Event()
        self._started_at: float = 0.0

    # ── 启动 ──────────────────────────────────────────────────

    async def start(self) -> None:
        """启动 TCP echo 和 DNS echo 服务。"""
        self._started_at = time.monotonic()
        loop = asyncio.get_running_loop()

        # TCP echo — 双栈监听（IPv4 + IPv6），交叉平台兼容
        # Python 3.14 的 asyncio.start_server 强制使用 Stream API（reader/writer），
        # 必须用 loop.create_server 注册低层 Protocol
        for host in ("0.0.0.0", "::"):
            try:
                srv = await loop.create_server(
                    lambda: EchoProtocol(),
                    host=host,
                    port=self.tcp_port,
                )
                self._tcp_servers.append(srv)
                logger.info(f"[echo] tcp echo listening: {host}:{self.tcp_port}")
            except OSError as e:
                logger.debug(f"[echo] tcp bind skipped {host}:{self.tcp_port}: {e}")

        # DNS echo — 绑定 127.0.0.1，用 SO_REUSEADDR 避免 mDNSResponder（*:53）冲突
        if not self.no_dns:
            try:
                dns_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                dns_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                dns_sock.bind(("127.0.0.1", self.dns_port))
                transport, protocol = await loop.create_datagram_endpoint(
                    lambda: DnsEchoProtocol(self.upstream_dns),
                    sock=dns_sock,
                )
                self._dns_transport = transport
                self._dns_protocol = protocol
                logger.info(
                    f"[dns.echo] dns echo listening: 127.0.0.1:{self.dns_port} "
                    f"upstream={self.upstream_dns}:53"
                )
            except OSError as e:
                logger.warning(
                    f"[dns.echo] DNS echo skipped — port {self.dns_port} "
                    f"unavailable (try sudo): {e}"
                )

        # Raw UDP echo — 绑定 127.0.0.1 原样回传
        if not self.no_udp_echo:
            try:
                udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                udp_sock.bind(("127.0.0.1", self.udp_echo_port))
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: RawUdpEchoProtocol(),
                    sock=udp_sock,
                )
                self._udp_echo_transport = transport
                logger.info(
                    f"[udp-echo] raw udp echo listening: 127.0.0.1:{self.udp_echo_port}"
                )
            except OSError as e:
                logger.warning(
                    f"[udp-echo] UDP echo skipped — port {self.udp_echo_port} "
                    f"unavailable: {e}"
                )

        logger.info("[echo] ready")

    # ── 关闭 ──────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """优雅关闭：停止接受新连接 → 关闭 DNS → 等待已建立连接排空。"""
        logger.info("[echo] shutting down...")

        # 停止 TCP servers（不再接受新连接，已有连接继续处理）
        for srv in self._tcp_servers:
            srv.close()
            await srv.wait_closed()

        # 关闭 DNS transport
        if self._dns_transport:
            self._dns_transport.close()

        # 关闭 UDP echo transport
        if self._udp_echo_transport:
            self._udp_echo_transport.close()

        self._shutdown_event.set()
        elapsed = time.monotonic() - self._started_at
        logger.info(f"[echo] stopped: uptime={elapsed:.1f}s")

    # ── 运行 ──────────────────────────────────────────────────

    async def run_forever(self) -> None:
        """启动服务并阻塞直到收到关闭信号。"""
        await self.start()

        # 注册信号处理
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._signal_handler)
            except NotImplementedError:
                # Windows 不支持 add_signal_handler
                pass

        await self._shutdown_event.wait()

    def _signal_handler(self) -> None:
        """SIGTERM/SIGINT 触发优雅关闭。"""
        asyncio.create_task(self.shutdown())


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="local-echo — TCP echo + DNS echo for zigbox proxy chain testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 echo_server.py                                       # 默认 TCP :13333 + DNS :5353
  python3 echo_server.py --tcp-port 9999 --no-dns              # 仅 TCP echo
  python3 echo_server.py --dns-port 53 --upstream-dns 223.5.5.5  # DNS 使用阿里 DNS
        """,
    )
    parser.add_argument(
        "--tcp-host", default="::",
        help="TCP echo 监听地址 (默认: ::, 双栈)"
    )
    parser.add_argument(
        "--tcp-port", type=int, default=13333,
        help="TCP echo 监听端口 (默认: 13333)"
    )
    parser.add_argument(
        "--dns-port", type=int, default=53,
        help="DNS echo 监听端口 (默认: 53，绑定 127.0.0.1，需 sudo)"
    )
    parser.add_argument(
        "--upstream-dns", default="8.8.8.8",
        help="上游 DNS 服务器 (默认: 8.8.8.8)"
    )
    parser.add_argument(
        "--no-dns", action="store_true",
        help="禁用 DNS echo，仅启动 TCP echo"
    )
    parser.add_argument(
        "--udp-echo-port", type=int, default=13334,
        help="Raw UDP echo 监听端口 (默认: 13334)"
    )
    parser.add_argument(
        "--no-udp-echo", action="store_true",
        help="禁用 raw UDP echo"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="减少日志输出（WARNING 级别）"
    )
    args = parser.parse_args()

    setup_logging(quiet=args.quiet)

    server = EchoServer(
        tcp_host=args.tcp_host,
        tcp_port=args.tcp_port,
        dns_port=args.dns_port,
        udp_echo_port=args.udp_echo_port,
        upstream_dns=args.upstream_dns,
        no_dns=args.no_dns,
        no_udp_echo=args.no_udp_echo,
    )

    try:
        asyncio.run(server.run_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
