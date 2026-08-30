// masque-echo — CONNECT-IP (RFC 9643) echo 测试服务器（connect-ip-go 最小实现）。
//
// 用途:zigoutbounds masque.zig（阶段 31）的数据面/胶囊解析本地联调目标。
// 语义:
//   - UDP :13400 — QUIC + HTTP/3 监听,处理 :protocol=connect-ip 的 Extended CONNECT 请求
//     (参考 connect-ip-go proxy_test.go:setupConns 骨架)
//   - 隧道建立后 AssignAddresses(10.0.0.1/32) 把客户端源地址前缀分配给对端
//   - 收到的 IP 包(DATAGRAM)原样回显 WritePacket(echo 语义,payload 逐字节比对)
//   - TCP :13401 — readiness 探针(zigtester 端口归属/就绪探测;QUIC UDP 端口无法 TCP 探测)
//
// 用法:
//   ./masque-echo [--host 127.0.0.1] [--port 13400] [--ready-port 13401]
//                 [--cert ../local-echo/certs/localhost.crt] [--key ...] [--assign 10.0.0.1/32]

package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/netip"

	connectip "github.com/metacubex/connect-ip-go"
	h "github.com/metacubex/http"
	"github.com/metacubex/quic-go/http3"
	"github.com/metacubex/tls"
	"github.com/yosida95/uritemplate/v3"
)

func main() {
	host := flag.String("host", "127.0.0.1", "bind address")
	port := flag.Int("port", 13400, "CONNECT-IP QUIC port (UDP)")
	readyPort := flag.Int("ready-port", 13401, "TCP readiness probe port")
	certPath := flag.String("cert", "../local-echo/certs/localhost.crt", "TLS certificate")
	keyPath := flag.String("key", "../local-echo/certs/localhost.key", "TLS key")
	assignAddr := flag.String("assign", "10.0.0.1/32", "IP prefix assigned to the client (src addr)")
	flag.Parse()

	// ---- TCP readiness 探针（accept 即关）----
	rl, err := net.Listen("tcp", fmt.Sprintf("%s:%d", *host, *readyPort))
	if err != nil {
		log.Fatalf("ready listen: %v", err)
	}
	go func() {
		for {
			c, err := rl.Accept()
			if err != nil {
				return
			}
			_ = c.Close()
		}
	}()

	template := uritemplate.MustNew(fmt.Sprintf("https://%s:%d/masque", *host, *port))
	assignPrefix, err := netip.ParsePrefix(*assignAddr)
	if err != nil {
		log.Fatalf("invalid assign addr %q: %v", *assignAddr, err)
	}

	mux := h.NewServeMux()
	mux.HandleFunc("/masque", func(w h.ResponseWriter, r *h.Request) {
		mreq, err := connectip.ParseRequest(r, template)
		if err != nil {
			var perr *connectip.RequestParseError
			if errors.As(err, &perr) {
				w.WriteHeader(perr.HTTPStatus)
			} else {
				w.WriteHeader(h.StatusBadRequest)
			}
			return
		}
		conn, err := (&connectip.Proxy{}).Proxy(w, mreq)
		if err != nil {
			log.Printf("connect-ip proxy: %v", err)
			return
		}
		// 分配客户端源地址前缀（客户端发 IP 包须用此作 src）
		if err := conn.AssignAddresses(context.Background(), []netip.Prefix{assignPrefix}); err != nil {
			log.Printf("assign addresses: %v", err)
			_ = conn.Close()
			return
		}
		// 通告本端可达路由（全段，对齐 mihomo masque.go:138-154）。
		// connect-ip-go 方向语义:本端 ReadPacket 校验 dst 用本端 localRoutes（本端 AdvertiseRoute 的），
		// 对端发来的 ROUTE_ADVERTISEMENT 只更新 availableRoutes——服务器不 AdvertiseRoute 则
		// 客户端发往任意 dst 的包被服务器拒收（实测 "datagram destination address not allowed"）。
		if err := conn.AdvertiseRoute(context.Background(), []connectip.IPRoute{
			{StartIP: netip.MustParseAddr("0.0.0.0"), EndIP: netip.MustParseAddr("255.255.255.255")},
			{StartIP: netip.MustParseAddr("::"), EndIP: netip.MustParseAddr("ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff")},
		}); err != nil {
			log.Printf("advertise route: %v", err)
			_ = conn.Close()
			return
		}
		// IP 包原样回显（echo 语义）
		go func() {
			for {
				pkt, err := conn.ReadPacket()
				if err != nil {
					_ = conn.Close()
					return
				}
				if _, err := conn.WritePacket(pkt); err != nil {
					_ = conn.Close()
					return
				}
			}
		}()
	})

	cert, err := tls.LoadX509KeyPair(*certPath, *keyPath)
	if err != nil {
		log.Fatalf("load cert: %v", err)
	}
	udpConn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP(*host), Port: *port})
	if err != nil {
		log.Fatalf("quic listen: %v", err)
	}
	h3Server := &http3.Server{
		Handler:         mux,
		TLSConfig:       &tls.Config{Certificates: []tls.Certificate{cert}},
		EnableDatagrams: true,
	}
	go func() {
		if err := h3Server.Serve(udpConn); err != nil {
			log.Printf("h3 server: %v", err)
			return
		}
	}()
	fmt.Printf("MASQUE_ECHO=%s:%d ready=%s:%d assign=%s\n", *host, *port, *host, *readyPort, assignPrefix.String())
	fmt.Println("RESULT=READY")
	select {} // 常驻
}
