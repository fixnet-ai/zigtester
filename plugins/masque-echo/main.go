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
//                 [--client-ca client.crt]   # 非空 = 要求并校验客户端证书(mTLS)

package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/netip"
	"os"

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
	assignAddr := flag.String("assign", "10.0.0.1/32", "IPv4 prefix assigned to the client (src addr)")
	assignAddr6 := flag.String("assign6", "2001:db8::1/128", "IPv6 prefix assigned to the client (src addr)")
	clientCA := flag.String("client-ca", "", "PEM client CA cert(s); non-empty requires & verifies client cert (mTLS)")
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
	assignPrefix6, err := netip.ParsePrefix(*assignAddr6)
	if err != nil {
		log.Fatalf("invalid assign6 addr %q: %v", *assignAddr6, err)
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
		// 分配客户端源地址前缀（客户端发 IP 包须用此作 src；v4+v6 双栈，对齐 mihomo Ipv6）
		if err := conn.AssignAddresses(context.Background(), []netip.Prefix{assignPrefix, assignPrefix6}); err != nil {
			log.Printf("assign addresses: %v", err)
			_ = conn.Close()
			return
		}
		// 对齐 Cloudflare WARP 服务器端：不 AdvertiseRoute（不通告路由）。
		// 服务器仍收任意 dst 包（fork 的 AllowAnyDestination=true 跳过 dst 校验，真转发语义），
		// 客户端无需等路由通告即可直接发包（WARP 形态）。
		// IPv4+TCP/UDP 段做无状态反射（src/dst 翻转，P2 TCP + 32.5g 真 TUN UDP
		// 数据面，见 tcp_echo.go）；ICMP/IPv6 原样回显（echo 语义）。
		// 隧道重置注入（P5）：收到 UDP magic 载荷 → 关 CONNECT-IP 流（FIN）
		// 驱动客户端重连（见 tcp_echo.go isResetTrigger）。注意只关流、不关
		// QUIC 连接——客户端复用同 QUIC 连接重开 CONNECT-IP 流即可。
		go func() {
			for {
				pkt, err := conn.ReadPacket()
				if err != nil {
					_ = conn.Close()
					return
				}
				if isResetTrigger(pkt) {
					log.Printf("reset trigger: closing CONNECT-IP stream")
					_ = conn.Close()
					return
				}
				out := reflectIPv4TCP(pkt)
				if _, err := conn.WritePacket(out); err != nil {
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
		TLSConfig:       buildServerTLS(cert, *clientCA),
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

// buildServerTLS 构造服务器 TLS 配置。
//
// clientCAPath 非空时要求并校验客户端证书（mTLS）。校验语义**对齐 Cloudflare WARP
// / usque**（api/masque.go InsecureSkipVerify + VerifyPeerCertificate），而非 Go 标准
// 链校验（RequireAndVerifyClientCert）：
//   - WARP/usque 客户端发**运行时自签**的空 DN 证书（GenerateCert，issuer==subject==空），
//     Go 标准 RequireAndVerifyClientCert 无法把它验证为"由 CA 锚签发"（自签 leaf 直接
//     按根处理 → unknown authority，W4 实测 openssl error 18 / Go unknown authority）。
//   - 所以服务器侧要求证书但**跳过链校验**，改为 VerifyPeerCertificate **比对客户端证书
//     ECDSA 公钥与锚（client.crt）公钥**——zigbox 用配置 private-key 生成证书时公钥
//     与 client.crt 一致（同密钥对），公钥匹配即放行（对齐 usque 服务端公钥比对）。
//
// clientCAPath 为空时保持原行为（不要求客户端证书）。
func buildServerTLS(cert tls.Certificate, clientCAPath string) *tls.Config {
	cfg := &tls.Config{Certificates: []tls.Certificate{cert}}
	if clientCAPath == "" {
		return cfg
	}
	pemData, err := os.ReadFile(clientCAPath)
	if err != nil {
		log.Fatalf("read client-ca: %v", err)
	}
	block, _ := pem.Decode(pemData)
	if block == nil {
		log.Fatalf("client-ca %s: no PEM blocks", clientCAPath)
	}
	anchorCert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		log.Fatalf("client-ca %s: parse: %v", clientCAPath, err)
	}
	anchorKey, ok := anchorCert.PublicKey.(*ecdsa.PublicKey)
	if !ok {
		log.Fatalf("client-ca %s: anchor public key not ECDSA", clientCAPath)
	}
	// RequireAnyClientCert：TLS 1.3 发 CertificateRequest；客户端无证书时拒绝
	// （mtls-neg）。InsecureSkipVerify=true 跳过默认链校验；VerifyPeerCertificate
	// 比对客户端证书公钥与锚公钥（对齐 WARP/usque 公钥固定语义）。
	cfg.ClientAuth = tls.RequireAnyClientCert
	cfg.InsecureSkipVerify = true
	cfg.VerifyPeerCertificate = func(rawCerts [][]byte, _ [][]*x509.Certificate) error {
		if len(rawCerts) == 0 {
			return errors.New("no client certificate")
		}
		leaf, err := x509.ParseCertificate(rawCerts[0])
		if err != nil {
			return fmt.Errorf("parse client cert: %v", err)
		}
		peerKey, ok := leaf.PublicKey.(*ecdsa.PublicKey)
		if !ok {
			return fmt.Errorf("client cert public key not ECDSA")
		}
		if !peerKey.Equal(anchorKey) {
			return errors.New("client cert public key mismatch with --client-ca anchor")
		}
		return nil
	}
	return cfg
}
