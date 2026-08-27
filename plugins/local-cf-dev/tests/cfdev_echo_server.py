#!/usr/bin/env python3
"""echo server — local-cf-dev relay 闭环验证用。
收连接 → 读数据 → 原样回写 → 关闭。监听 127.0.0.1:18999。
"""
import socket
import sys

HOST = "127.0.0.1"
PORT = 18999


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(16)
    print(f"[echo] listening on {HOST}:{PORT}", flush=True)
    while True:
        conn, addr = srv.accept()
        print(f"[echo] accepted from {addr}", flush=True)
        try:
            data = conn.recv(65536)
            if not data:
                print("[echo] empty recv, closing", flush=True)
                continue
            print(f"[echo] recv {len(data)} bytes", flush=True)
            conn.sendall(data)
            print(f"[echo] echoed {len(data)} bytes", flush=True)
        except Exception as e:
            print(f"[echo] error: {e}", flush=True)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
