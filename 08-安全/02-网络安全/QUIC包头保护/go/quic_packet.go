// quic_packet.go —— QUIC v1 包格式与包号编解码（RFC 9000 §16/§17.1/§17.2 与附录 A.2·A.3）
//
// 只放「不涉及密钥」的部分：变长整数、头部布局与 pn_offset、包号编解码、
// AEAD nonce 与最小帧长度。头保护与短头包收发在 quic_protect.go。
package main

import (
	"encoding/binary"
	"math/bits"
)

const (
	varintMax       = uint64(1)<<62 - 1
	sampleLen       = 16
	hpOffsetFromPN  = 4 // 采样从包号字段起点往后跳 4 字节
	shortHeaderMask = 0x40
	longHeaderMask  = 0x80
)

// varintLen：前缀 00/01/10/11 分别对应 1/2/4/8 字节
func varintLen(v uint64) int {
	switch {
	case v < 1<<6:
		return 1
	case v < 1<<14:
		return 2
	case v < 1<<30:
		return 4
	default:
		return 8
	}
}

// encodeVarint 按最短宽度编码 —— 注意 QUIC **不要求**最短编码，只是这里选择这么做
func encodeVarint(v uint64) []byte {
	n := varintLen(v)
	out := make([]byte, n)
	for i := 0; i < n; i++ {
		out[n-1-i] = byte(v >> (8 * uint(i)))
	}
	if n > 1 {
		prefix := byte(1)
		if n == 8 {
			prefix = 3
		} else if n == 4 {
			prefix = 2
		}
		out[0] |= prefix << 6
	}
	return out
}

// decodeVarint 返回 (值, 消耗字节数, 是否成功)
func decodeVarint(data []byte, off int) (uint64, int, bool) {
	if off < 0 || off >= len(data) {
		return 0, 0, false
	}
	width := 1 << (data[off] >> 6)
	if off+width > len(data) {
		return 0, 0, false
	}
	var v uint64
	for i := 0; i < width; i++ {
		v = v<<8 | uint64(data[off+i])
	}
	if width == 1 {
		v &= 0x3f
	} else {
		v &= (uint64(1) << (8*uint(width) - 2)) - 1
	}
	return v, width, true
}

// pnOffsetInitial：Length 与 Token Length 都是 varint，宽度参与计算 ——
// 所以「包号固定在某个偏移」这种假设是错的。
func pnOffsetInitial(dcidLen, scidLen, tokenLen, lengthFieldLen int) int {
	return 7 + dcidLen + scidLen + varintLen(uint64(tokenLen)) + tokenLen + lengthFieldLen
}

func pnOffsetShort(dcidLen int) int {
	return 1 + dcidLen
}

func pnLengthFromFirstByte(first byte) int {
	return int(first&0x03) + 1
}

func longHeaderType(first byte) int {
	return int((first & 0x30) >> 4)
}

type initialHeader struct {
	version     uint32
	dcid        []byte
	scid        []byte
	token       []byte
	length      uint64
	lengthWidth int
	pnOffset    int
	typeName    string
}

var longHeaderTypes = [...]string{"Initial", "0-RTT", "Handshake", "Retry"}

// parseInitialHeader 按规范顺序逐段解码，而不是按固定偏移切片
func parseInitialHeader(pkt []byte) (initialHeader, bool) {
	var h initialHeader
	if len(pkt) < 7 || pkt[0]&longHeaderMask == 0 {
		return h, false
	}
	h.version = binary.BigEndian.Uint32(pkt[1:5])
	dcidLen := int(pkt[5])
	if 6+dcidLen >= len(pkt) {
		return h, false
	}
	h.dcid = pkt[6 : 6+dcidLen]
	off := 6 + dcidLen
	scidLen := int(pkt[off])
	if off+1+scidLen > len(pkt) {
		return h, false
	}
	h.scid = pkt[off+1 : off+1+scidLen]
	off += 1 + scidLen
	tokenLen, n, ok := decodeVarint(pkt, off)
	if !ok {
		return h, false
	}
	off += n
	if off+int(tokenLen) > len(pkt) {
		return h, false
	}
	h.token = pkt[off : off+int(tokenLen)]
	off += int(tokenLen)
	length, n, ok := decodeVarint(pkt, off)
	if !ok {
		return h, false
	}
	h.length, h.lengthWidth = length, n
	h.pnOffset = off + n
	h.typeName = longHeaderTypes[longHeaderType(pkt[0])]
	return h, true
}

// pnMinBits 是 RFC 9000 附录 A.2 里 log(n,2)+1 的整数等价形式。
// 伪代码用实数 log：n 恰为 2 的幂时不再多一位；写成 bits.Len64(n)+1
// 会在这些点上多要一个字节（n=128 应是 1 字节而不是 2 字节）。
func pnMinBits(n uint64) int {
	b := bits.Len64(n)
	if b == 0 {
		return 1
	}
	if n == uint64(1)<<(b-1) {
		return b
	}
	return b + 1
}

// packetNumberBytes 给出「足以表示未确认区间两倍以上」所需的最小字节数
func packetNumberBytes(fullPN uint64, hasLargestAcked bool, largestAcked uint64) int {
	unacked := fullPN + 1
	if hasLargestAcked {
		unacked = fullPN - largestAcked
	}
	n := (pnMinBits(unacked) + 7) / 8
	return min(max(n, 1), 4)
}

func encodePacketNumber(fullPN uint64, nbytes int) []byte {
	out := make([]byte, nbytes)
	for i := 0; i < nbytes; i++ {
		out[nbytes-1-i] = byte(fullPN >> (8 * uint(i)))
	}
	return out
}

// decodePacketNumber 按附录 A.3 的窗口还原完整包号。
// Go 的 int64 转换是必需的：`expected_pn - pn_hwin` 在 expected_pn 很小时
// 会无符号下溢成巨大值，使第一个分支恒真、把包号算错。
func decodePacketNumber(largestPN, truncated uint64, nbits uint) uint64 {
	expected := int64(largestPN) + 1
	win := int64(1) << nbits
	hwin := win / 2
	cand := (expected & ^(win - 1)) | int64(truncated)
	if cand <= expected-hwin && uint64(cand) < (uint64(1)<<62)-uint64(win) {
		return uint64(cand + win)
	}
	if cand > expected+hwin && cand >= win {
		return uint64(cand - win)
	}
	return uint64(cand)
}

// aeadNonce = IV ⊕ (62 位包号左填零)，逐字节异或
func aeadNonce(iv []byte, pn uint64) []byte {
	out := make([]byte, len(iv))
	copy(out, iv)
	for i := 0; i < len(iv) && i < 8; i++ {
		out[len(iv)-1-i] ^= byte(pn >> (8 * uint(i)))
	}
	return out
}

// minFrameLength：pn_len + 认证标签 + 帧数据 ≥ 4 + 16 才能取样
func minFrameLength(pnLen int, expansion int) int {
	need := hpOffsetFromPN + sampleLen
	if pnLen+expansion >= need {
		return 0
	}
	return need - pnLen - expansion
}

func sampleOK(pktLen, pnOffset int) bool {
	return pnOffset+hpOffsetFromPN+sampleLen <= pktLen
}
