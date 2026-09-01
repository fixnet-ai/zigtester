// tcp_echo.go — stateless TCP reflector for the CONNECT-IP echo server.
//
// 纯 IP 回显无法完成 TCP 握手（回显 SYN 得到的是 SYN 而非 SYN-ACK）。
// 本文件在 echo 语义之上叠加一个无状态 TCP 反射器（P2 no-tun TCP 数据面）：
//   解析 IPv4+TCP 头，src/dst IP 与端口对调，seq/ack 按接收段推算回射，
//   payload 原样回显，重算 IP 与 TCP（伪头）校验和。
//   - SYN      -> SYN-ACK（固定服务端 ISN；ack = seq+1）
//   - 数据/ACK -> ACK(PSH) + 回显 payload（ack = seq+len+SYN+FIN）
//   - FIN      -> FIN-ACK（ack = seq+len+SYN+FIN）
//   - RST      -> 原样回显（连接已中止，无状态反射无意义）
//   - 其余     -> 原样回显（UDP/ICMP/IPv6 保持纯 echo 语义）
//
// 设计约束：
//   - 无状态：不维护连接表，仅凭当前段推算响应（服务端 seq = 收到的 ack，
//     仅 SYN 且 ack==0 时用固定 ISN）。重传段会得到相同的响应，天然幂等。
//   - 选项字节原样保留：响应拷贝整包后仅改 src/dst/seq/ack/flags，客户端
//     SYN 携带的 MSS/SACK/WS 选项原样反射，lwIP 客户端按自身能力协商即可。
//   - 仅 IPv4+TCP（echo 服务器 assign 为 IPv4；IPv6 TCP 数据面暂无用例）。
package main

import (
	"encoding/binary"
)

// serverISN — 无状态反射的固定服务端初始序列号。
const serverISN uint32 = 0x22001100

// reflectIPv4TCP 对收到的 IP 包做 TCP 反射；非 IPv4+TCP 返回原包（echo 语义）。
// 返回的切片可能是原包（无副本）也可能是新分配的反射包，调用方按 WritePacket
// 语义原样发送即可（composeDatagram 会再次递减 TTL 并重算 IP 校验和）。
func reflectIPv4TCP(pkt []byte) []byte {
	// IPv4 版本 + 头长
	if len(pkt) < 20 || pkt[0]>>4 != 4 {
		return pkt
	}
	ihl := int(pkt[0]&0x0F) * 4
	if ihl < 20 || len(pkt) < ihl {
		return pkt
	}
	if pkt[9] != 6 { // 非 TCP（UDP/ICMP）→ 原样回显
		return pkt
	}
	totalLen := int(binary.BigEndian.Uint16(pkt[2:4]))
	if len(pkt) < totalLen || totalLen < ihl {
		return pkt
	}
	tcp := pkt[ihl:totalLen]
	if len(tcp) < 20 {
		return pkt
	}
	flags := tcp[13]
	if flags&0x04 != 0 { // RST → 原样回显
		return pkt
	}
	dataOff := int(tcp[12]>>4) * 4
	if dataOff < 20 || len(tcp) < dataOff {
		return pkt
	}
	payload := tcp[dataOff:]
	seq := binary.BigEndian.Uint32(tcp[4:8])
	ack := binary.BigEndian.Uint32(tcp[8:12])

	// 响应 seq/ack
	var respSeq uint32
	if ack != 0 {
		respSeq = ack
	} else {
		respSeq = serverISN
	}
	respAck := seq + uint32(len(payload))
	if flags&0x02 != 0 { // SYN 占 1 字节序号空间
		respAck++
	}
	if flags&0x01 != 0 { // FIN 占 1 字节序号空间
		respAck++
	}

	var respFlags byte
	switch {
	case flags&0x02 != 0:
		respFlags = 0x12 // SYN|ACK
	case flags&0x01 != 0:
		respFlags = 0x11 // FIN|ACK
	case len(payload) > 0:
		respFlags = 0x18 // ACK|PSH
	default:
		respFlags = 0x10 // ACK
	}

	// 拷贝整包，改 src/dst IP + 端口 + seq/ack/flags，重算校验和
	resp := make([]byte, totalLen)
	copy(resp, pkt[:totalLen])
	srcIP := pkt[12:16]
	dstIP := pkt[16:20]
	copy(resp[12:16], dstIP)
	copy(resp[16:20], srcIP)
	rt := resp[ihl:totalLen]
	binary.BigEndian.PutUint16(rt[0:2], binary.BigEndian.Uint16(tcp[2:4])) // src port = 原 dst
	binary.BigEndian.PutUint16(rt[2:4], binary.BigEndian.Uint16(tcp[0:2])) // dst port = 原 src
	binary.BigEndian.PutUint32(rt[4:8], respSeq)
	binary.BigEndian.PutUint32(rt[8:12], respAck)
	rt[13] = respFlags
	rt[16] = 0
	rt[17] = 0 // 清零 TCP 校验和再重算
	binary.BigEndian.PutUint16(rt[16:18], tcpChecksum(dstIP, srcIP, rt))
	resp[10] = 0
	resp[11] = 0 // 清零 IP 校验和再重算
	binary.BigEndian.PutUint16(resp[10:12], ipChecksum(resp[:ihl]))
	return resp
}

// tcpChecksum 计算带 IPv4 伪头的 TCP 校验和（checksum 字段须已清零）。
func tcpChecksum(srcIP, dstIP, seg []byte) uint16 {
	var sum uint32
	sum += uint32(binary.BigEndian.Uint16(srcIP[0:2]))
	sum += uint32(binary.BigEndian.Uint16(srcIP[2:4]))
	sum += uint32(binary.BigEndian.Uint16(dstIP[0:2]))
	sum += uint32(binary.BigEndian.Uint16(dstIP[2:4]))
	sum += 6                    // protocol = TCP
	sum += uint32(len(seg))     // TCP 段长度
	for i := 0; i+1 < len(seg); i += 2 {
		sum += uint32(binary.BigEndian.Uint16(seg[i : i+2]))
	}
	if len(seg)%2 == 1 {
		sum += uint32(seg[len(seg)-1]) << 8
	}
	for sum>>16 != 0 {
		sum = (sum & 0xFFFF) + (sum >> 16)
	}
	return ^uint16(sum)
}

// ipChecksum 计算 IPv4 头校验和（header 须完整，checksum 字段可为任意值）。
func ipChecksum(header []byte) uint16 {
	var sum uint32
	for i := 0; i+1 < len(header); i += 2 {
		sum += uint32(binary.BigEndian.Uint16(header[i : i+2]))
	}
	for sum>>16 != 0 {
		sum = (sum & 0xFFFF) + (sum >> 16)
	}
	return ^uint16(sum)
}
