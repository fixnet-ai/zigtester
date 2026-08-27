#!/usr/bin/env python3
"""VLESS-over-WS 客户端 — local-cf-dev relay 闭环验证用。

流程:
1. 连 127.0.0.1:18787，发 RFC6455 WS 握手，校验 Sec-WebSocket-Accept。
2. 拼 VLESS 头 (version+uuid+optLen+command+port+atyp+域名)，目标 localhost:18999，
   封装成单个 WS 二进制帧 (VLESS 头 + 测试 payload)。
3. 读回 WS 帧，剥离首帧 2 字节 cloudflare 响应头，比对回显 == payload。

仅用 Python 标准库裸 socket，不依赖 websocket-client。
"""
import base64
import hashlib
import os
import socket

WORKER_HOST = "127.0.0.1"
WORKER_PORT = 18787
TARGET_PORT = 18999
TARGET_HOST = b"localhost"
UUID_HEX = "86c50e3a5b8749ddbd2003c7f2735e40"
PAYLOAD = b"ping-hello"


def build_ws_handshake() -> bytes:
    key = base64.b64encode(os.urandom(16)).decode()
    return (
        f"GET / HTTP/1.1\r\n"
        f"Host: {WORKER_HOST}:{WORKER_PORT}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Origin: http://127.0.0.1\r\n"
        "\r\n"
    ).encode(), key


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"connection closed, got {len(buf)}/{n} bytes")
        buf += chunk
    return buf


def read_http_headers(sock: socket.socket) -> (int, dict, bytes):
    """读 HTTP 响应头，返回 (status, headers, 剩余字节)。"""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("connection closed during handshake")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status_line = lines[0]
    status = int(status_line.split(b" ")[1])
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            k, _, v = line.partition(b":")
            headers[k.strip().lower()] = v.strip()
    return status, headers, rest


def build_vless_header() -> bytes:
    """version(1)=0x00 + uuid(16) + optLen(1)=0x00 + command(1)=0x01(TCP)
    + port(2 BE) + atyp(1)=0x02(域名) + addrLen(1) + "localhost" """
    uuid_bytes = bytes.fromhex(UUID_HEX.replace("-", ""))
    assert len(uuid_bytes) == 16, len(uuid_bytes)
    hdr = b"\x00" + uuid_bytes + b"\x00\x01"
    hdr += TARGET_PORT.to_bytes(2, "big")  # 18999 -> 0x4a37
    hdr += b"\x02"  # atyp = domain
    hdr += bytes([len(TARGET_HOST)])  # 9
    hdr += TARGET_HOST
    return hdr


def build_ws_binary_frame(data: bytes) -> bytes:
    """单帧，FIN=1, opcode=0x2 (binary)，client→server 必须掩码。"""
    first = 0x80 | 0x02  # FIN + binary
    mask_bit = 0x80
    n = len(data)
    if n < 126:
        header = bytes([first, mask_bit | n])
    elif n < 65536:
        header = bytes([first, mask_bit | 126]) + n.to_bytes(2, "big")
    else:
        header = bytes([first, mask_bit | 127]) + n.to_bytes(8, "big")
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return header + mask + masked


def read_ws_frame(sock: socket.socket) -> bytes:
    """读一个 server→client 帧的 payload（server 帧不掩码）。"""
    b1, b2 = recv_exact(sock, 2)
    # b1: FIN+RSV+opcode; b2: mask(0)+len7
    length = b2 & 0x7F
    if length == 126:
        length = int.from_bytes(recv_exact(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(recv_exact(sock, 8), "big")
    assert not (b2 & 0x80), "unexpected masked server frame"
    return recv_exact(sock, length)


def main() -> None:
    # 1) TCP + WS 握手
    sock = socket.create_connection((WORKER_HOST, WORKER_PORT), timeout=10)
    req, key = build_ws_handshake()
    sock.sendall(req)
    status, headers, _ = read_http_headers(sock)
    print(f"[ws] handshake status={status} headers={headers}")
    accept_bytes = headers.get(b"sec-websocket-accept", b"")
    accept = accept_bytes.decode() if isinstance(accept_bytes, bytes) else accept_bytes
    expect = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    assert status == 101, f"handshake failed: status={status} headers={headers}"
    assert accept == expect, f"Sec-WebSocket-Accept mismatch: got={accept} expect={expect}"
    print("[ws] handshake OK, Sec-WebSocket-Accept 校验通过")

    # 2) VLESS 头 + payload 单帧发送
    vless_hdr = build_vless_header()
    print(f"[vless] header={vless_hdr.hex()} len={len(vless_hdr)}")
    print(f"[vless] target=localhost:{TARGET_PORT} atyp=domain payload={PAYLOAD!r}")
    frame = build_ws_binary_frame(vless_hdr + PAYLOAD)
    sock.sendall(frame)
    print(f"[ws] sent {len(frame)} bytes (single binary frame)")

    # 3) 读回显：首帧剥离 2 字节 cloudflare 响应头
    data = read_ws_frame(sock)
    print(f"[ws] recv frame {len(data)} bytes: {data[:64].hex()}")
    assert len(data) >= 2, "回显数据不足 2 字节"
    body = data[2:]  # 剥 cloudflareResponseHeader [version, 0]
    print(f"[ws] body after 2-byte header: {body!r}")
    if body == PAYLOAD:
        print(f"[RESULT] PASS — echo == payload ({PAYLOAD!r})")
    else:
        print(f"[RESULT] FAIL — echo != payload (got {body!r}, want {PAYLOAD!r})")
    sock.close()


if __name__ == "__main__":
    main()
