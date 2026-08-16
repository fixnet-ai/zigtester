// local-echo — 统一 echo 测试服务器（Go 高性能实现,2026-08-17 重做）。
//
// 单程序承载全部 echo 协议(替代原 python3 echo_server.py + h2h3-echo 两进程):
//   - TCP echo :13333 — 协议自适应(SOCKS5 / HTTP CONNECT / HTTP),echo 阶段
//     io.Copy 内核 splice(每连接 goroutine,与 bench-echo 同款高性能模型)
//   - UDP echo :13333 — 原样回传数据报(透明代理 UDP 测试)
//   - DNS echo :5533 — 测试域名(echo.direct/proxy/block, baidu.com, google.com)
//     → 确定性 FakeIP(198.18.x.x / fd18::,MD5 哈希);localhost → 127.0.0.1/::1;
//     其余转发上游 DNS
//   - H2 echo :13335 — Go 标准库 ServeTLS(ALPN h2)
//   - H3 echo :13336 — quic-go http3.Server
//
// echo 语义与旧实现完全一致(测试契约):
//   - HTTP 请求 → 200 + Content-Length + 请求原文 body,优雅关闭
//   - SOCKS5 握手(no-auth) → CONNECT 请求 → 成功响应 → echo
//   - CONNECT 隧道 → 200 Connection Established → echo
//   - H2/H3 echo:请求体原样回显,无请求体回固定文本 "h2h3-echo-ok"
//
// 用法:
//   ./local-echo [--tcp-port 13333] [--dns-port 5533] [--upstream-dns 8.8.8.8]
//                [--h2-port 13335] [--h3-port 13336] [--cert ...] [--key ...]

package main

import (
	"bufio"
	"crypto/md5"
	"crypto/tls"
	"encoding/binary"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/quic-go/quic-go"
	"github.com/quic-go/quic-go/http3"
)

// ═══════════════════════════════════════════════════════════════
// 协议常量
// ═══════════════════════════════════════════════════════════════

var httpMethods = []string{"GET ", "POST ", "HEAD ", "PUT ", "DELETE ", "OPTIONS ", "PATCH "}

// 缓冲池(2026-08-17):短连接高并发下复用 64K 读缓冲与 bufio.Reader,
// 减少每连接分配与 GC 压力(800 连 = 51MB 分配 → 池化后 ~0)。
var chunkPool = sync.Pool{New: func() any { return make([]byte, 65536) }}
var readerPool = sync.Pool{New: func() any { return bufio.NewReaderSize(nil, 65536) }}

const httpOkHeaderTpl = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"

// ═══════════════════════════════════════════════════════════════
// TCP echo — 协议自适应
// ═══════════════════════════════════════════════════════════════

func handleTcp(conn net.Conn) {
	defer conn.Close()
	r := readerPool.Get().(*bufio.Reader)
	r.Reset(conn)
	defer readerPool.Put(r)
	// 单次读首包(非 Peek——客户端可能发完即等响应,ReadAll/Peek(7) 会死锁)
	buf := chunkPool.Get().([]byte)
	defer chunkPool.Put(buf)
	n, err := r.Read(buf)
	if err != nil || n == 0 {
		return
	}
	data := buf[:n]
	data = mergeWithin(conn, r, data, 500*time.Microsecond)

	if data[0] == 0x05 {
		handleSocks5(conn, r, data)
		return
	}
	if len(data) >= 7 && strings.EqualFold(string(data[:7]), "CONNECT") {
		// 丢弃 CONNECT 请求头(至空行),剩余字节进入 echo
		rest := drainHeaders(r, data[7:])
		conn.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n"))
		echoMode(conn, r, rest, true)
		return
	}
	// HTTP 请求:原文作为 response body,优雅关闭
	writeHttpResponse(conn, data)
}

// mergeWithin — 短窗口合并后续数据段(QUIC 惰性首帧等分片场景:
// 首段 252 字节 + 剩余 772 在后续帧,不合并则 echo 只回首段,比对失败)。
// 用连接层 read deadline:窗口内持续可读则持续合并(上限 64KB),
// 超时/EOF 停止并清 deadline(后续 io.Copy 正常)。
func mergeWithin(conn net.Conn, r *bufio.Reader, data []byte, window time.Duration) []byte {
	conn.SetReadDeadline(time.Now().Add(window))
	chunk := chunkPool.Get().([]byte)
	defer chunkPool.Put(chunk)
	for len(data) < 65536 {
		n, err := r.Read(chunk)
		if n > 0 {
			data = append(data, chunk[:n]...)
		}
		if err != nil {
			break // timeout 或 EOF
		}
		conn.SetReadDeadline(time.Now().Add(window)) // 续窗口
	}
	conn.SetReadDeadline(time.Time{})
	return data
}

// drainHeaders — 消费 HTTP 头(首包内偏移 7 起,不足则从 reader 续读)直到空行。
// 返回空行之后的剩余字节(echo 数据)。
func drainHeaders(r *bufio.Reader, rest []byte) []byte {
	// 在 rest 内找 \r\n\r\n(允许仅 \n\n)
	for {
		idx := -1
		if i := strings.Index(string(rest), "\r\n\r\n"); i >= 0 {
			idx = i
			rest = rest[i+4:]
			return rest
		}
		if i := strings.Index(string(rest), "\n\n"); i >= 0 {
			idx = i
			rest = rest[i+2:]
			return rest
		}
		_ = idx
		// 未找到:续读一段
		line, err := r.ReadString('\n')
		if err != nil || (line == "\n" || line == "\r\n") {
			return nil
		}
	}
}

// handleSocks5 — 缓冲驱动状态机:握手 → CONNECT 请求 → 成功响应 → echo。
// data = 首包(可能含握手+请求+echo 数据的粘包)。
func handleSocks5(conn net.Conn, r *bufio.Reader, data []byte) {
	buf := data
	state := 0 // 0=handshake 1=request

	// 补读 helper:buf 不足 required 时从 reader 续读
	needMore := func(buf []byte, required int) ([]byte, bool) {
		for len(buf) < required {
			chunk := chunkPool.Get().([]byte)
			n, err := r.Read(chunk)
			chunkPool.Put(chunk)
			if err != nil || n == 0 {
				return buf, false
			}
			buf = append(buf, chunk[:n]...)
		}
		return buf, true
	}

	for {
		switch state {
		case 0: // handshake
			var ok bool
			buf, ok = needMore(buf, 2)
			if !ok {
				return
			}
			nmethods := int(buf[1])
			buf, ok = needMore(buf, 2+nmethods)
			if !ok {
				return
			}
			conn.Write([]byte{0x05, 0x00}) // no-auth
			buf = buf[2+nmethods:]
			state = 1
		case 1: // request
			var ok bool
			buf, ok = needMore(buf, 4)
			if !ok {
				return
			}
			if !(buf[0] == 0x05 && buf[1] == 0x01 && buf[2] == 0x00) {
				// 非 CONNECT 帧:回退 echo(兼容旧行为)
				echoMode(conn, r, buf, false)
				return
			}
			total := 0
			switch buf[3] {
			case 0x01:
				total = 4 + 4 + 2
			case 0x03:
				buf, ok = needMore(buf, 5)
				if !ok {
					return
				}
				total = 4 + 1 + int(buf[4]) + 2
			case 0x04:
				total = 4 + 16 + 2
			default:
				echoMode(conn, r, buf, false)
				return
			}
			buf, ok = needMore(buf, total)
			if !ok {
				return
			}
			conn.Write([]byte{0x05, 0x00, 0x00, 0x01, 0x7f, 0x00, 0x00, 0x01, 0x00, 0x00})
			buf = buf[total:]
			echoMode(conn, r, buf, false)
			return
		}
	}
}

// echoMode — echo 阶段:首段数据是 HTTP 方法 → 200+原文+close;
// 否则原样回显(closeAfterEcho = CONNECT 隧道语义:回显后优雅关闭)。
func echoMode(conn net.Conn, r *bufio.Reader, first []byte, closeAfterEcho bool) {
	// 首段数据可能还在 reader(粘包耗尽):读一段再探测
	// (事件驱动语义的同步等价:goroutine 阻塞等待客户端首段,不占其他连接)
	if len(first) == 0 {
		chunk := chunkPool.Get().([]byte)
		n, err := r.Read(chunk)
		chunkPool.Put(chunk)
		if err != nil || n == 0 {
			return
		}
		first = chunk[:n]
	}
	if startsWithMethodBytes(first) {
		writeHttpResponse(conn, first)
		return
	}
	conn.Write(first)
	io.Copy(conn, r) // 内核 splice 回显剩余
	if closeAfterEcho {
		if tcp, ok := conn.(*net.TCPConn); ok {
			tcp.CloseWrite()
		}
		conn.SetReadDeadline(time.Now().Add(5 * time.Second))
		io.Copy(io.Discard, conn) // drain(防 RST 丢数据)
	}
}

// writeHttpResponse — 200 + Content-Length + 请求原文 body,优雅关闭。
// 半关写后 drain 对端剩余数据(等 EOF)再关闭——立即 Close 会 RST 掉
// 未达数据(2026-08-17 tuic TCP 功能验证超时根因:CloseWrite 后 defer Close
// 触发得太快)。
func writeHttpResponse(conn net.Conn, data []byte) {
	fmt.Fprintf(conn, httpOkHeaderTpl, len(data))
	conn.Write(data)
	if tcp, ok := conn.(*net.TCPConn); ok {
		tcp.CloseWrite()
	}
	conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	io.Copy(io.Discard, conn) // drain 直到对端关闭
}

func startsWithMethodBytes(data []byte) bool {
	for _, m := range httpMethods {
		if len(data) >= len(m) && strings.HasPrefix(string(data), m) {
			return true
		}
	}
	return false
}

func startsWithMethod(s string) bool {
	for _, m := range httpMethods {
		if strings.HasPrefix(s, m) {
			return true
		}
	}
	return false
}

// ═══════════════════════════════════════════════════════════════
// UDP echo — 原样回传
// ═══════════════════════════════════════════════════════════════

func handleUdp(conn *net.UDPConn) {
	buf := make([]byte, 65535)
	for {
		n, addr, err := conn.ReadFromUDP(buf)
		if err != nil {
			log.Printf("[udp-echo] read: %v", err)
			return
		}
		conn.WriteToUDP(buf[:n], addr)
	}
}

// ═══════════════════════════════════════════════════════════════
// DNS echo — 测试域名→FakeIP,其余转发上游
// ═══════════════════════════════════════════════════════════════

var testDomains = []string{"echo.direct", "echo.proxy", "echo.block", "baidu.com", "google.com"}

func isTestDomain(domain string) bool {
	for _, d := range testDomains {
		if domain == d || strings.HasSuffix(domain, "."+d) {
			return true
		}
	}
	return false
}

func fakeipV4(domain string) net.IP {
	h := md5.Sum([]byte(domain))
	return net.IPv4(198, 18, h[0]&0x7F, h[1])
}

func fakeipV6(domain string) net.IP {
	h := md5.Sum([]byte(domain))
	return net.ParseIP(fmt.Sprintf("fd18::%02x%02x:%02x%02x", h[0], h[1], h[2], h[3]))
}

type dnsQuery struct {
	tid    uint16
	domain string
	qtype  uint16
}

func parseDnsQuery(data []byte) *dnsQuery {
	if len(data) < 12 {
		return nil
	}
	tid := binary.BigEndian.Uint16(data[0:2])
	// 跳过问题区前:找域名(从 12 偏移开始)
	off := 12
	var labels []string
	for off < len(data) {
		l := int(data[off])
		if l == 0 {
			off++
			break
		}
		if l&0xC0 == 0xC0 {
			return nil // 压缩指针:查询不应有
		}
		if off+1+l > len(data) {
			return nil
		}
		labels = append(labels, string(data[off+1:off+1+l]))
		off += 1 + l
	}
	if off+4 > len(data) {
		return nil
	}
	qtype := binary.BigEndian.Uint16(data[off : off+2])
	return &dnsQuery{tid: tid, domain: strings.Join(labels, "."), qtype: qtype}
}

func buildDnsResponse(packet []byte, query *dnsQuery, ips []net.IP) []byte {
	resp := make([]byte, 0, 512)
	resp = append(resp, packet[:12]...) // 复制头部
	binary.BigEndian.PutUint16(resp[2:4], 0x8180) // QR+RD+RA
	binary.BigEndian.PutUint16(resp[6:8], 1)       // ANCOUNT=1
	// 问题区:原样域名 + qtype/class
	qoff := 12
	for qoff < len(packet) {
		l := int(packet[qoff])
		resp = append(resp, packet[qoff:qoff+1+l]...)
		qoff += 1 + l
		if l == 0 {
			break
		}
	}
	resp = append(resp, packet[qoff:qoff+4]...) // qtype + class
	// 应答区:name 指针 + 类型 + class + ttl + rdlen + rdata
	for _, ip := range ips {
		resp = append(resp, 0xC0, 0x0C)
		if ip.To4() != nil {
			resp = append(resp, 0x00, 0x01, 0x00, 0x01)         // A, IN
			resp = append(resp, 0x00, 0x00, 0x00, 0x3C)         // TTL 60
			resp = append(resp, 0x00, 0x04)                     // RDLEN 4
			resp = append(resp, ip.To4()...)
		} else {
			resp = append(resp, 0x00, 0x1C, 0x00, 0x01)         // AAAA, IN
			resp = append(resp, 0x00, 0x00, 0x00, 0x3C)         // TTL 60
			resp = append(resp, 0x00, 0x10)                     // RDLEN 16
			resp = append(resp, ip.To16()...)
		}
	}
	return resp
}

func handleDns(conn *net.UDPConn, upstream string) {
	buf := make([]byte, 65535)
	pending := make(map[uint16]*net.UDPAddr)
	var mu sync.Mutex
	upstreamAddr := &net.UDPAddr{IP: net.ParseIP(upstream), Port: 53}

	go func() {
		// 读 socket(客户端查询 + 上游响应同一 socket)
		for {
			n, addr, err := conn.ReadFromUDP(buf)
			if err != nil {
				log.Printf("[dns] read: %v", err)
				return
			}
			packet := make([]byte, n)
			copy(packet, buf[:n])

			if addr.IP.Equal(upstreamAddr.IP) && addr.Port == 53 {
				// 上游响应 → 按 tid 回传客户端
				mu.Lock()
				client := pending[binary.BigEndian.Uint16(packet[0:2])]
				delete(pending, binary.BigEndian.Uint16(packet[0:2]))
				mu.Unlock()
				if client != nil {
					conn.WriteToUDP(packet, client)
				}
				continue
			}

			query := parseDnsQuery(packet)
			if query == nil {
				continue
			}
			if query.qtype != 1 && query.qtype != 28 {
				continue
			}
			if query.domain == "localhost" || isTestDomain(query.domain) {
				var ips []net.IP
				if query.domain == "localhost" {
					if query.qtype == 1 {
						ips = []net.IP{net.IPv4(127, 0, 0, 1)}
					} else {
						ips = []net.IP{net.ParseIP("::1")}
					}
				} else if query.qtype == 1 {
					ips = []net.IP{fakeipV4(query.domain)}
				} else {
					ips = []net.IP{fakeipV6(query.domain)}
				}
				resp := buildDnsResponse(packet, query, ips)
				conn.WriteToUDP(resp, addr)
			} else {
				// 转发上游
				mu.Lock()
				pending[query.tid] = addr
				mu.Unlock()
				conn.WriteToUDP(packet, upstreamAddr)
			}
		}
	}()
}

// ═══════════════════════════════════════════════════════════════
// H2 / H3 echo(合并自 h2h3-echo)
// ═══════════════════════════════════════════════════════════════

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
	tcpPort := flag.Int("tcp-port", 13333, "TCP+UDP echo port")
	dnsPort := flag.Int("dns-port", 5533, "DNS echo port")
	upstreamDns := flag.String("upstream-dns", "8.8.8.8", "upstream DNS for non-test domains")
	h2Port := flag.Int("h2-port", 13335, "HTTP/2 echo port")
	h3Port := flag.Int("h3-port", 13336, "HTTP/3 echo port")
	certPath := flag.String("cert", "certs/localhost.crt", "TLS certificate")
	keyPath := flag.String("key", "certs/localhost.key", "TLS key")
	flag.Parse()

	// ---- TCP echo(协议自适应)----
	tcpLn, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", *tcpPort))
	if err != nil {
		log.Fatalf("tcp listen: %v", err)
	}
	go func() {
		for {
			conn, err := tcpLn.Accept()
			if err != nil {
				log.Printf("[tcp] accept: %v", err)
				return
			}
			go handleTcp(conn)
		}
	}()
	fmt.Printf("TCP_ECHO=127.0.0.1:%d\n", *tcpPort)

	// ---- UDP echo(原样回传)----
	udpConn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: *tcpPort})
	if err != nil {
		log.Fatalf("udp echo listen: %v", err)
	}
	go handleUdp(udpConn)
	fmt.Printf("UDP_ECHO=127.0.0.1:%d\n", *tcpPort)

	// ---- DNS echo(选择性代理)----
	dnsConn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: *dnsPort})
	if err != nil {
		log.Fatalf("dns listen: %v", err)
	}
	go handleDns(dnsConn, *upstreamDns)
	fmt.Printf("DNS_ECHO=127.0.0.1:%d\n", *dnsPort)

	// ---- H2 echo ----
	h2Ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", *h2Port))
	if err != nil {
		log.Fatalf("h2 listen: %v", err)
	}
	handler := http.HandlerFunc(echoHandler)
	h2Server := &http.Server{Handler: handler}
	go func() {
		if err := h2Server.ServeTLS(h2Ln, *certPath, *keyPath); err != nil && err != http.ErrServerClosed {
			log.Printf("h2 server: %v", err)
			os.Exit(1)
		}
	}()
	fmt.Printf("H2_ECHO=127.0.0.1:%d\n", *h2Port)

	// ---- H3 echo ----
	cert, err := tls.LoadX509KeyPair(*certPath, *keyPath)
	if err != nil {
		log.Fatalf("load cert: %v", err)
	}
	h3Conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: *h3Port})
	if err != nil {
		log.Fatalf("h3 udp listen: %v", err)
	}
	h3Server := &http3.Server{
		Handler:    handler,
		TLSConfig:  &tls.Config{Certificates: []tls.Certificate{cert}},
		QUICConfig: &quic.Config{MaxIdleTimeout: 30 * time.Second},
	}
	go func() {
		if err := h3Server.Serve(h3Conn); err != nil {
			log.Printf("h3 server: %v", err)
			os.Exit(1)
		}
	}()
	fmt.Printf("H3_ECHO=127.0.0.1:%d\n", *h3Port)

	fmt.Println("RESULT=READY")
	select {} // 常驻
}
