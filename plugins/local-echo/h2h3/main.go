// h2h3-echo — 标准 HTTP/2 + HTTP/3 echo 服务（local-echo 插件子服务）。
//
// 用途: 为 fixnet 生态的 h2/h3 客户端（H2Client/H3Client 等）提供本地、可控、
// 可复现的 quic-go 对端——不依赖外网服务器。
//
//  - h2: Go 标准库 http.Server + ServeTLS（ALPN h2/http1.1 自动协商）
//  - h3: quic-go http3.Server（ALPN h3）
//
// echo 语义: 请求体原样回显；无请求体返回固定文本 "h2h3-echo-ok"（数据面往返验证用）。
//
// 用法:
//   ./h2h3-echo --h2-port 13335 --h3-port 13336 --cert certs/localhost.crt --key certs/localhost.key
//
// 输出 KEY=VALUE: H2H3_ECHO=h2:13335,h3:13336 RESULT=READY

package main

import (
	"crypto/tls"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"time"

	"github.com/quic-go/quic-go"
	"github.com/quic-go/quic-go/http3"
)

func echoHandler(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	if len(body) > 0 {
		w.Write(body)
	} else {
		w.Write([]byte("h2h3-echo-ok"))
	}
}

func main() {
	h2Port := flag.Int("h2-port", 13335, "HTTP/2 listen port (TCP+TLS)")
	h3Port := flag.Int("h3-port", 13336, "HTTP/3 listen port (UDP)")
	certPath := flag.String("cert", "certs/localhost.crt", "TLS certificate path")
	keyPath := flag.String("key", "certs/localhost.key", "TLS key path")
	flag.Parse()

	handler := http.HandlerFunc(echoHandler)

	// ---- h2（TCP + TLS，标准库自动协商 ALPN h2/http1.1）----
	h2Ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", *h2Port))
	if err != nil {
		log.Fatalf("h2 listen: %v", err)
	}
	h2Server := &http.Server{Handler: handler}
	go func() {
		if err := h2Server.ServeTLS(h2Ln, *certPath, *keyPath); err != nil && err != http.ErrServerClosed {
			log.Printf("h2 server: %v", err)
			os.Exit(1)
		}
	}()
	fmt.Printf("H2_ECHO=127.0.0.1:%d\n", *h2Port)

	// ---- h3（UDP，quic-go http3）----
	cert, err := tls.LoadX509KeyPair(*certPath, *keyPath)
	if err != nil {
		log.Fatalf("load cert: %v", err)
	}
	udpConn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: *h3Port})
	if err != nil {
		log.Fatalf("h3 udp listen: %v", err)
	}
	h3Server := &http3.Server{
		Handler: handler,
		TLSConfig: &tls.Config{
			Certificates: []tls.Certificate{cert},
			NextProtos:   []string{"h3"},
			MinVersion:   tls.VersionTLS13,
		},
		QUICConfig: &quic.Config{
			MaxIdleTimeout:  60 * time.Second,
			EnableDatagrams: false,
		},
	}
	fmt.Printf("H3_ECHO=127.0.0.1:%d\n", *h3Port)
	fmt.Println("RESULT=READY")

	// h3 前台运行（插件主进程）；h2 已后台
	if err := h3Server.Serve(udpConn); err != nil {
		log.Fatalf("h3 server: %v", err)
	}
}
