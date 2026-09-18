// quic_protect.go —— 头保护与短头包收发
// （RFC 9001 §5.3 nonce / §5.4 头保护 / §5.5 接收受保护包 + RFC 9000 §17.3 短头包格式）
//
// 头保护是 QUIC 相对 TLS 的独有设计：**包头的关键位也被加密**，链路上的观察者
// 既看不到包号，也看不到「包号字段有多长」。代价是采样位置必须与被保护的位无关，
// 因此采样从**包号字段起点往后跳 4 字节**取 16 字节（按最长包号预留）。
package main

import "encoding/binary"

// headerMask：ChaCha20 套件的掩码 = 用样本加密 5 个零字节。
// 样本前 4 字节是块计数器（小端），后 12 字节是 nonce。
// AES 套件则是 mask = AES-ECB(hp_key, sample) 的前 5 字节（见 C 版）。
func headerMask(hpKey, sample []byte) []byte {
	zeros := make([]byte, 5)
	return chacha20XOR(hpKey, binary.LittleEndian.Uint32(sample[:4]), sample[4:16], zeros)
}

// sampleFor：长度不足必须丢弃整包，而不是勉强算出掩码
func sampleFor(pkt []byte, pnOffset int) ([]byte, bool) {
	start := pnOffset + hpOffsetFromPN
	if start+sampleLen > len(pkt) {
		return nil, false
	}
	return pkt[start : start+sampleLen], true
}

// applyHeaderProtection 就地加密头部：长头只掩低 4 位（保住类型位与固定位），
// 短头掩低 5 位（保住固定位与密钥相位）。
func applyHeaderProtection(pkt []byte, pnOffset, pnLen int, mask []byte) {
	if pkt[0]&longHeaderMask != 0 {
		pkt[0] ^= mask[0] & 0x0f
	} else {
		pkt[0] ^= mask[0] & 0x1f
	}
	for i := 0; i < pnLen; i++ {
		pkt[pnOffset+i] ^= mask[1+i]
	}
}

// removeHeaderProtection 返回解出的包号长度；采样不足返回 false。
// 顺序不能反：先用只依赖偏移的采样算掩码，还原首字节后才知道包号多长。
func removeHeaderProtection(pkt []byte, pnOffset int, hpKey []byte) (int, bool) {
	sample, ok := sampleFor(pkt, pnOffset)
	if !ok {
		return 0, false
	}
	mask := headerMask(hpKey, sample)
	if pkt[0]&longHeaderMask != 0 {
		pkt[0] ^= mask[0] & 0x0f
	} else {
		pkt[0] ^= mask[0] & 0x1f
	}
	pnLen := pnLengthFromFirstByte(pkt[0])
	if pnLen > 4 {
		return 0, false
	}
	for i := 0; i < pnLen; i++ {
		pkt[pnOffset+i] ^= mask[1+i]
	}
	return pnLen, true
}

// buildShortPacket 组装一个 1-RTT 短头包：AAD 是**未保护**的完整头
func buildShortPacket(dcid []byte, pnBytes []byte, packetNumber uint64,
	payload, key, iv, hpKey []byte, keyPhase byte) []byte {
	header := make([]byte, 0, 1+len(dcid)+len(pnBytes))
	header = append(header, shortHeaderMask|keyPhase<<2|byte(len(pnBytes)-1))
	header = append(header, dcid...)
	header = append(header, pnBytes...)
	ct := aeadSeal(key, aeadNonce(iv, packetNumber), header, payload)
	pkt := append(header, ct...)
	applyHeaderProtection(pkt, pnOffsetShort(len(dcid)), len(pnBytes), headerMask(hpKey, mustSample(pkt, pnOffsetShort(len(dcid)))))
	return pkt
}

func mustSample(pkt []byte, pnOffset int) []byte {
	s, ok := sampleFor(pkt, pnOffset)
	if !ok {
		panic("packet too short for header protection sample")
	}
	return s
}

// openShortPacket 返回 (完整包号, 明文)；解析顺序：先去头保护，再解 AEAD
func openShortPacket(pkt []byte, dcidLen int, largestPN uint64, key, iv,
	hpKey []byte) (uint64, []byte, bool) {
	buf := make([]byte, len(pkt))
	copy(buf, pkt)
	pnOffset := pnOffsetShort(dcidLen)
	pnLen, ok := removeHeaderProtection(buf, pnOffset, hpKey)
	if !ok {
		return 0, nil, false
	}
	var truncated uint64
	for i := 0; i < pnLen; i++ {
		truncated = truncated<<8 | uint64(buf[pnOffset+i])
	}
	pn := decodePacketNumber(largestPN, truncated, uint(pnLen*8))
	header := buf[:pnOffset+pnLen]
	pt, ok := aeadOpen(key, aeadNonce(iv, pn), header, buf[pnOffset+pnLen:])
	if !ok {
		return 0, nil, false
	}
	return pn, pt, true
}
