package main

import (
	"encoding/binary"
	"testing"
)

// buildClientPkt 构造一个客户端 IPv4+TCP 段（src=10.0.0.2 → dst=10.0.0.1）。
// clientSeq/clientAck 为段的 seq/ack；flags 置 SYN/FIN/PSH 位；payload 可空。
// 选项字节为 12 字节（MSS+SACK+WS），验证反射后选项原样保留。
func buildClientPkt(t *testing.T, clientSeq, clientAck uint32, flags byte, payload []byte) []byte {
	t.Helper()
	ihl := 20
	total := ihl + 20 + 12 + len(payload) // IP头 + TCP头 + 选项 + payload
	pkt := make([]byte, total)
	pkt[0] = 0x45 // IPv4, IHL=5 (20字节; 无 IP 选项)
	binary.BigEndian.PutUint16(pkt[2:4], uint16(total))
	pkt[8] = 64
	pkt[9] = 6 // TCP
	copy(pkt[12:16], []byte{10, 0, 0, 2})
	copy(pkt[16:20], []byte{10, 0, 0, 1})
	tcp := pkt[ihl:total]
	binary.BigEndian.PutUint16(tcp[0:2], 40000)       // client src port
	binary.BigEndian.PutUint16(tcp[2:4], 8080)        // dst port
	binary.BigEndian.PutUint32(tcp[4:8], clientSeq)   // seq
	binary.BigEndian.PutUint32(tcp[8:12], clientAck)  // ack
	tcp[12] = (20 + 12) / 4 << 4                      // data offset = 8 (20+12)
	tcp[13] = flags
	copy(tcp[20:], []byte{2, 4, 5, 180, 4, 2, 8, 3, 3, 3, 0, 1}) // MSS=1460+SACK+WS
	copy(tcp[32:], payload)
	binary.BigEndian.PutUint16(tcp[16:18], tcpChecksum(pkt[12:16], pkt[16:20], tcp))
	binary.BigEndian.PutUint16(pkt[10:12], ipChecksum(pkt[:ihl]))
	return pkt
}

// verifyReflectedChecksums 重算反射包的 IP 与 TCP 校验和，必须全零。
func verifyReflectedChecksums(t *testing.T, pkt []byte) {
	t.Helper()
	ihl := int(pkt[0]&0x0F) * 4
	if got := ipChecksum(pkt[:ihl]); got != 0 {
		t.Fatalf("reflected IP checksum != 0: %04x", got)
	}
	total := int(binary.BigEndian.Uint16(pkt[2:4]))
	tcp := pkt[ihl:total]
	dataOff := int(tcp[12]>>4) * 4
	if got := tcpChecksum(pkt[12:16], pkt[16:20], tcp[:dataOff+len(tcp)-dataOff]); got != 0 {
		t.Fatalf("reflected TCP checksum != 0: %04x", got)
	}
}

func TestReflectSYN(t *testing.T) {
	// 客户端 SYN: seq=100, ack=0
	pkt := buildClientPkt(t, 100, 0, 0x02, nil)
	out := reflectIPv4TCP(pkt)
	if &out[0] == &pkt[0] {
		t.Fatalf("expected new allocation for TCP reflection")
	}
	verifyReflectedChecksums(t, out)
	ihl := int(out[0]&0x0F) * 4
	tcp := out[ihl:]
	// src/dst IP 对调: 10.0.0.1 → 10.0.0.2
	if out[12] != 10 || out[13] != 0 || out[14] != 0 || out[15] != 1 {
		t.Fatalf("expected dst swapped src=10.0.0.1, got %v", out[12:16])
	}
	if out[16] != 10 || out[17] != 0 || out[18] != 0 || out[19] != 2 {
		t.Fatalf("expected dst=10.0.0.2, got %v", out[16:20])
	}
	// 端口对调
	if srcPort := binary.BigEndian.Uint16(tcp[0:2]); srcPort != 8080 {
		t.Fatalf("expected reflected src port 8080, got %d", srcPort)
	}
	if dstPort := binary.BigEndian.Uint16(tcp[2:4]); dstPort != 40000 {
		t.Fatalf("expected reflected dst port 40000, got %d", dstPort)
	}
	// seq=serverISN, ack=101, flags=SYN|ACK
	if seq := binary.BigEndian.Uint32(tcp[4:8]); seq != serverISN {
		t.Fatalf("expected seq=%d (serverISN), got %d", serverISN, seq)
	}
	if ack := binary.BigEndian.Uint32(tcp[8:12]); ack != 101 {
		t.Fatalf("expected ack=101, got %d", ack)
	}
	if flags := tcp[13]; flags != 0x12 {
		t.Fatalf("expected SYN|ACK (0x12), got 0x%02x", flags)
	}
	// 选项原样保留
	if tcp[20] != 2 || tcp[21] != 4 {
		t.Fatalf("expected MSS option preserved, got %02x %02x", tcp[20], tcp[21])
	}
}

func TestReflectData(t *testing.T) {
	// 客户端 ACK+数据: seq=101, ack=serverISN+1, payload="hello"
	payload := []byte("hello")
	pkt := buildClientPkt(t, 101, serverISN+1, 0x18, payload)
	out := reflectIPv4TCP(pkt)
	verifyReflectedChecksums(t, out)
	ihl := int(out[0]&0x0F) * 4
	total := int(binary.BigEndian.Uint16(out[2:4]))
	tcp := out[ihl:total]
	// seq = 收到 ack = serverISN+1; ack = 101+5 = 106; flags = ACK|PSH
	if seq := binary.BigEndian.Uint32(tcp[4:8]); seq != serverISN+1 {
		t.Fatalf("expected seq=serverISN+1, got %d", seq)
	}
	if ack := binary.BigEndian.Uint32(tcp[8:12]); ack != 106 {
		t.Fatalf("expected ack=106, got %d", ack)
	}
	if flags := tcp[13]; flags != 0x18 {
		t.Fatalf("expected ACK|PSH (0x18), got 0x%02x", flags)
	}
	// payload 回显
	dataOff := int(tcp[12]>>4) * 4
	if got := string(tcp[dataOff:]); got != "hello" {
		t.Fatalf("expected echoed payload 'hello', got %q", got)
	}
}

func TestReflectFIN(t *testing.T) {
	// 客户端 FIN: seq=106, ack=serverISN+6
	pkt := buildClientPkt(t, 106, serverISN+6, 0x11, nil)
	out := reflectIPv4TCP(pkt)
	verifyReflectedChecksums(t, out)
	ihl := int(out[0]&0x0F) * 4
	tcp := out[ihl:]
	if seq := binary.BigEndian.Uint32(tcp[4:8]); seq != serverISN+6 {
		t.Fatalf("expected seq=serverISN+6, got %d", seq)
	}
	if ack := binary.BigEndian.Uint32(tcp[8:12]); ack != 107 {
		t.Fatalf("expected ack=107 (FIN consume), got %d", ack)
	}
	if flags := tcp[13]; flags != 0x11 {
		t.Fatalf("expected FIN|ACK (0x11), got 0x%02x", flags)
	}
}

func TestReflectNonTCPEcho(t *testing.T) {
	// UDP 包原样回显（同一切片）
	pkt := buildClientPkt(t, 0, 0, 0, nil)
	pkt[9] = 17 // UDP
	out := reflectIPv4TCP(pkt)
	if &out[0] != &pkt[0] {
		t.Fatalf("expected same slice for non-TCP echo")
	}
}

func TestReflectRSTEcho(t *testing.T) {
	pkt := buildClientPkt(t, 100, 0, 0x04, nil)
	out := reflectIPv4TCP(pkt)
	if &out[0] != &pkt[0] {
		t.Fatalf("expected same slice for RST echo")
	}
}

// buildClientUDPPkt 构造客户端 IPv4+UDP 包（src=10.0.0.1 → dst=10.0.0.2）。
// 对齐 masque.zig buildIpv4Udp 线格式（20B IP 头 + 8B UDP 头 + payload）。
func buildClientUDPPkt(t *testing.T, payload []byte) []byte {
	t.Helper()
	total := 20 + 8 + len(payload)
	pkt := make([]byte, total)
	pkt[0] = 0x45 // IPv4, IHL=5
	binary.BigEndian.PutUint16(pkt[2:4], uint16(total))
	pkt[8] = 64
	pkt[9] = 17 // UDP
	copy(pkt[12:16], []byte{10, 0, 0, 1})
	copy(pkt[16:20], []byte{10, 0, 0, 2})
	binary.BigEndian.PutUint16(pkt[20:22], 40000) // sport
	binary.BigEndian.PutUint16(pkt[22:24], 13400) // dport = masque echo port
	binary.BigEndian.PutUint16(pkt[24:26], uint16(8+len(payload)))
	copy(pkt[28:], payload)
	return pkt
}

func TestIsResetTrigger(t *testing.T) {
	// 命中：UDP 载荷以 magic 前缀开头
	pkt := buildClientUDPPkt(t, append(append([]byte{}, resetMagic...), []byte("rest")...))
	if !isResetTrigger(pkt) {
		t.Fatalf("expected reset trigger on magic UDP payload")
	}
	// 精确载荷命中
	if !isResetTrigger(buildClientUDPPkt(t, resetMagic)) {
		t.Fatalf("expected reset trigger on exact magic payload")
	}
	// 随机 echo 载荷（1024B）不命中
	rand := make([]byte, 1024)
	for i := range rand {
		rand[i] = byte(i * 7)
	}
	if isResetTrigger(buildClientUDPPkt(t, rand)) {
		t.Fatalf("unexpected reset trigger on random payload")
	}
	// 短载荷（不足 magic）不命中
	if isResetTrigger(buildClientUDPPkt(t, resetMagic[:5])) {
		t.Fatalf("unexpected reset trigger on short payload")
	}
	// TCP 包不命中（proto=6）
	tcpPkt := buildClientPkt(t, 100, 0, 0x18, resetMagic)
	if isResetTrigger(tcpPkt) {
		t.Fatalf("unexpected reset trigger on TCP packet")
	}
	// 非 IPv4（IPv6 版本号 6）不命中
	v6 := buildClientUDPPkt(t, resetMagic)
	v6[0] = 0x60
	if isResetTrigger(v6) {
		t.Fatalf("unexpected reset trigger on IPv6 packet")
	}
	// 畸形短包（<28B）不命中
	if isResetTrigger([]byte{0x45, 0x00, 0x00, 0x14}) {
		t.Fatalf("unexpected reset trigger on malformed short packet")
	}
}
