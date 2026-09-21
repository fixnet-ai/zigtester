//! ech-echo — ECH（RFC 9849）回环测试服务端（zigtester 插件）
//!
//! 形态 = Go 1.24+ crypto/tls 原生 ECH 的最小 trojan 服务端：
//!   TLS1.3(+ECH) → trojan 头解析（[56B hex(SHA224(password))][\r\n][cmd][SocksAddr][\r\n]）
//!   → dial 客户端指定目标 → 双向 relay。
//!
//! E2E 断言 = 数据面 require-ech：握手后 ECHAccepted=false 即断连（回显往返必经
//! ECH 接受），另有 ECH_CONN= 日志行存证。启动时生成两把 X25519 HPKE 密钥：
//!   - real（注册进 EncryptedClientHelloKeys，SendAsRetry=true）→ --config-out
//!     （b64 完整 ECHConfigList = u16 总长前缀 + ECHConfig，zo mapTlsRuntime
//!     assembleEchConfigList 直接消费的形态）
//!   - stale（不注册）→ --config-out-stale（反腿：客户端持此配置 → 服务端解不开
//!     → recovery flow → 客户端 SSL_R_ECH_REJECTED / zo error.EchRejected）
//!
//! 用法: ./ech-echo --port 13420 --password ech-echo-test \
//!        --config-out ./ech_config.b64 --config-out-stale ./ech_config_stale.b64 \
//!        --cert ../local-echo/certs/localhost.crt --key ../local-echo/certs/localhost.key

package main

import (
	"bufio"
	"crypto/ecdh"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"strings"
	"time"
)

// marshalECHConfig — ECHConfig 线格式（version 0xfe0d / X25519-HKDF-SHA256 /
// HKDF-SHA256 + 三 AEAD），对齐 vendor sing-box common/tls/ech_shared.go 同名函数
// 与 RFC 9849 §4；Go tls.EncryptedClientHelloKey.Config 接受裸 ECHConfig（无 u16 前缀）。
func marshalECHConfig(id uint8, pubKey []byte, publicName string, maxNameLen uint8) ([]byte, error) {
	const (
		extEncryptedClientHello = 0xfe0d
		kemX25519HKDFSHA256     = 0x0020
		kdfHKDFSHA256           = 0x0001
		aeadAES128GCM           = 0x0001
		aeadAES256GCM           = 0x0002
		aeadChaCha20Poly1305    = 0x0003
	)
	b := make([]byte, 0, 64+len(pubKey))
	put16 := func(v uint16) { b = append(b, byte(v>>8), byte(v)) }
	put8 := func(v uint8) { b = append(b, v) }
	put16(extEncryptedClientHello)
	// content 长度占位（RFC 9849 ECHConfig = version(2B) + content_len(2B) + content；
	// Go parseECHConfig 读 version 后必读 u16 length，缺它即 "invalid length field"）
	lenOff := len(b)
	put16(0)
	put8(id)
	put16(kemX25519HKDFSHA256)
	put16(uint16(len(pubKey)))
	b = append(b, pubKey...)
	csOff := len(b)
	put16(0) // cipher_suites 长度占位
	for _, aead := range []uint16{aeadAES128GCM, aeadAES256GCM, aeadChaCha20Poly1305} {
		put16(kdfHKDFSHA256)
		put16(aead)
	}
	binary.BigEndian.PutUint16(b[csOff:], uint16(len(b)-csOff-2))
	put8(maxNameLen)
	put8(uint8(len(publicName)))
	b = append(b, publicName...)
	put16(0) // extensions 空
	binary.BigEndian.PutUint16(b[lenOff:], uint16(len(b)-lenOff-2))
	return b, nil
}

// echConfigListB64 — 完整 ECHConfigList（u16 大端总长前缀 + ECHConfig）的 base64。
// zo 侧消费：unicfg ech.config 条目 → assembleEchConfigList 剥前缀合并。
func echConfigListB64(cfg []byte) string {
	wire := make([]byte, 2+len(cfg))
	binary.BigEndian.PutUint16(wire, uint16(len(cfg)))
	copy(wire[2:], cfg)
	return base64.StdEncoding.EncodeToString(wire)
}

// genEchKey — X25519 HPKE 密钥对 + 对应裸 ECHConfig
func genEchKey(configID uint8, publicName string) (*ecdh.PrivateKey, []byte, error) {
	priv, err := ecdh.X25519().GenerateKey(rand.Reader)
	if err != nil {
		return nil, nil, err
	}
	cfg, err := marshalECHConfig(configID, priv.PublicKey().Bytes(), publicName, 0)
	if err != nil {
		return nil, nil, err
	}
	return priv, cfg, nil
}

func main() {
	host := flag.String("host", "127.0.0.1", "listen host")
	port := flag.Int("port", 13420, "TLS+trojan listen port")
	password := flag.String("password", "ech-echo-test", "trojan password")
	publicName := flag.String("public-name", "ech-echo.test", "ECH public_name（多标签 DNS 名；Go 客户端 validDNSName 拒单标签，如 localhost）")
	configOut := flag.String("config-out", "./ech_config.b64", "real ECHConfigList b64 输出路径")
	configOutStale := flag.String("config-out-stale", "./ech_config_stale.b64", "stale（未注册密钥）配置输出路径")
	certPath := flag.String("cert", "../local-echo/certs/localhost.crt", "TLS 证书 PEM（复用 local-echo 生态统一 localhost 证书）")
	keyPath := flag.String("key", "../local-echo/certs/localhost.key", "TLS 私钥 PEM")
	flag.Parse()

	// 证书：复用 local-echo 自签 localhost 证书（客户端 insecure=true，仅作 TLS 载体）
	cert, err := tls.LoadX509KeyPair(*certPath, *keyPath)
	if err != nil {
		log.Fatalf("[ech-echo] load cert: %v", err)
	}

	// real 密钥（注册 + SendAsRetry）与 stale 密钥（仅发布配置文件、永不注册）
	realKey, realCfg, err := genEchKey(0x2a, *publicName)
	if err != nil {
		log.Fatalf("[ech-echo] gen real key: %v", err)
	}
	_, staleCfg, err := genEchKey(0x2b, *publicName)
	if err != nil {
		log.Fatalf("[ech-echo] gen stale key: %v", err)
	}

	if err := os.WriteFile(*configOut, []byte(echConfigListB64(realCfg)), 0o644); err != nil {
		log.Fatalf("[ech-echo] write config-out: %v", err)
	}
	if err := os.WriteFile(*configOutStale, []byte(echConfigListB64(staleCfg)), 0o644); err != nil {
		log.Fatalf("[ech-echo] write config-out-stale: %v", err)
	}

	tlsCfg := &tls.Config{
		Certificates: []tls.Certificate{cert},
		MinVersion:   tls.VersionTLS13, // ECH 仅 TLS 1.3（Go 强制约束）
		EncryptedClientHelloKeys: []tls.EncryptedClientHelloKey{{
			Config:      realCfg,
			PrivateKey:  realKey.Bytes(),
			SendAsRetry: true,
		}},
	}

	ln, err := net.Listen("tcp", fmt.Sprintf("%s:%d", *host, *port))
	if err != nil {
		log.Fatalf("[ech-echo] listen: %v", err)
	}
	log.Printf("[ech-echo] listening %s:%d public_name=%s config_out=%s", *host, *port, *publicName, *configOut)

	wantKey := hex.EncodeToString(trojanKeyBytes(*password))
	for {
		tcpConn, err := ln.Accept()
		if err != nil {
			log.Printf("[ech-echo] accept: %v", err)
			return
		}
		go handleConn(tcpConn, tlsCfg, wantKey)
	}
}

// trojanKeyBytes — hex(SHA224(password))（对齐 zo trojan.zig / sing-box 派生）
func trojanKeyBytes(password string) []byte {
	sum := sha256.Sum224([]byte(password))
	return sum[:]
}

// handleConn — TLS(+ECH) 握手 → require-ech 门 → trojan 头 → relay
func handleConn(raw net.Conn, tlsCfg *tls.Config, wantKeyHex string) {
	defer raw.Close()
	tlsConn := tls.Server(raw, tlsCfg)
	tlsConn.SetDeadline(time.Now().Add(10 * time.Second)) // 握手+头解析 10s 上限
	if err := tlsConn.Handshake(); err != nil {
		log.Printf("[ech-echo] ECH_CONN handshake_error addr=%s err=%v", raw.RemoteAddr(), err)
		return
	}
	cs := tlsConn.ConnectionState()
	if !cs.ECHAccepted {
		// require-ech：非 ECH 连接（含 recovery flow 的 rejection 握手）一律断连 ——
		// 正腿回显成功即证明 ECH 被接受（数据面断言，不依赖日志 grep）
		log.Printf("[ech-echo] ECH_CONN ECH=rejected addr=%s (closing: require-ech)", raw.RemoteAddr())
		return
	}
	log.Printf("[ech-echo] ECH_CONN ECH=accepted addr=%s", raw.RemoteAddr())

	r := bufio.NewReader(tlsConn)
	// trojan 头: [56B hex key][\r\n][cmd 1B][SocksAddr][\r\n]
	key := make([]byte, 56)
	if _, err := io.ReadFull(r, key); err != nil {
		log.Printf("[ech-echo] read key: %v", err)
		return
	}
	if !strings.EqualFold(string(key), wantKeyHex) {
		log.Printf("[ech-echo] trojan auth failed addr=%s", raw.RemoteAddr())
		return
	}
	if err := expectCRLF(r); err != nil {
		log.Printf("[ech-echo] key separator: %v", err)
		return
	}
	cmd, err := r.ReadByte()
	if err != nil {
		log.Printf("[ech-echo] read cmd: %v", err)
		return
	}
	if cmd != 0x01 { // 仅 TCP（cmd=0x03 UDP associate 不在回环腿范围）
		log.Printf("[ech-echo] unsupported cmd=0x%02x", cmd)
		return
	}
	host, portStr, err := readSocksAddr(r)
	if err != nil {
		log.Printf("[ech-echo] read addr: %v", err)
		return
	}
	if err := expectCRLF(r); err != nil {
		log.Printf("[ech-echo] addr separator: %v", err)
		return
	}

	target, err := net.DialTimeout("tcp", net.JoinHostPort(host, portStr), 5*time.Second)
	if err != nil {
		log.Printf("[ech-echo] dial target %s:%s: %v", host, portStr, err)
		return
	}
	defer target.Close()
	tlsConn.SetDeadline(time.Time{}) // 数据面清限，relay 长连
	go func() {
		defer tlsConn.Close()
		defer target.Close()
		io.Copy(tlsConn, target)
	}()
	io.Copy(target, r) // r 先排 bufio 余量再回源 tlsConn
}

func expectCRLF(r *bufio.Reader) error {
	b0, err := r.ReadByte()
	if err != nil {
		return err
	}
	b1, err := r.ReadByte()
	if err != nil {
		return err
	}
	if b0 != '\r' || b1 != '\n' {
		return fmt.Errorf("bad separator 0x%02x 0x%02x", b0, b1)
	}
	return nil
}

// readSocksAddr — ATYP 0x01 IPv4 / 0x03 domain / 0x04 IPv6 + 2B BE 端口
func readSocksAddr(r *bufio.Reader) (string, string, error) {
	atyp, err := r.ReadByte()
	if err != nil {
		return "", "", err
	}
	var host string
	switch atyp {
	case 0x01:
		var ip [4]byte
		if _, err := io.ReadFull(r, ip[:]); err != nil {
			return "", "", err
		}
		host = net.IP(ip[:]).String()
	case 0x03:
		l, err := r.ReadByte()
		if err != nil {
			return "", "", err
		}
		d := make([]byte, l)
		if _, err := io.ReadFull(r, d); err != nil {
			return "", "", err
		}
		host = string(d)
	case 0x04:
		var ip [16]byte
		if _, err := io.ReadFull(r, ip[:]); err != nil {
			return "", "", err
		}
		host = net.IP(ip[:]).String()
	default:
		return "", "", fmt.Errorf("bad atyp 0x%02x", atyp)
	}
	var p [2]byte
	if _, err := io.ReadFull(r, p[:]); err != nil {
		return "", "", err
	}
	return host, fmt.Sprintf("%d", binary.BigEndian.Uint16(p[:])), nil
}
