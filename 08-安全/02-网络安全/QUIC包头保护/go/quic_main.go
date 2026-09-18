// quic_main.go —— QUIC v1 包保护自检（Go，纯标准库）
//
// 运行：go run ./go     （或 go build ./go && ./go）
//
// 期望值全部取自 RFC 原文：
//   RFC 9001 附录 A.1  Initial 密钥
//   RFC 9001 附录 A.2  客户端 Initial 的未保护头（长度 1182 / pn_offset 18 / 包号 2）
//   RFC 9001 附录 A.5  ChaCha20-Poly1305 短头包：从 secret 到 21 字节包（含 sample/mask）
//   RFC 9000 附录 A.2·A.3  包号编码字节数与解码窗口
package main

import (
	"bytes"
	"encoding/hex"
	"fmt"
)

var checks, failures int

// check：沿用本仓库 Go demo 的约定签名（label, ok, detail），失败时打印实际值
func check(label string, ok bool, detail string) {
	checks++
	if !ok {
		failures++
		fmt.Printf("  FAIL %-46s %s\n", label, detail)
	}
}

func hexEq(got []byte, wantHex string) bool {
	want, err := hex.DecodeString(wantHex)
	return err == nil && bytes.Equal(got, want)
}

// RFC 9001 附录 A.1：Initial 密钥全链路
func testInitialKeys() {
	dcid := mustHex("8394c8f03e515708")
	client, server := initialSecrets(dcid)
	check("client_initial_secret", hexEq(client,
		"c00cf151ca5be075ed0ebfb5c80323c42d6b7db67881289af4008f1f6c357aea"), "")
	check("server_initial_secret", hexEq(server,
		"3c199828fd139efd216c155ad844cc81fb82fa8d7446fa7d78be803acdda951b"), "")

	key, iv, hp := packetKeys(client, 16, 12, 16)
	check("client key", hexEq(key, "1f369613dd76d5467730efcbe3b1a22d"), "")
	check("client iv", hexEq(iv, "fa044b2f42a3fd3b46fb255c"), "")
	check("client hp", hexEq(hp, "9f50449e04a0e810283a1e9933adedd2"), "")

	skey, siv, shp := packetKeys(server, 16, 12, 16)
	check("server key", hexEq(skey, "cf3a5331653c364c88f0f379b6067e37"), "")
	check("server iv", hexEq(siv, "0ac1493ca1905853b0bba03e"), "")
	check("server hp", hexEq(shp, "c206b8d9b9f0f37644430b490eeaa314"), "")
	check("quic ku differs from secret", !hexEq(nextSecret(client),
		"c00cf151ca5be075ed0ebfb5c80323c42d6b7db67881289af4008f1f6c357aea"), "")
}

// RFC 9001 附录 A.2：未保护头解析
func testInitialHeader() {
	hdr := mustHex("c300000001088394c8f03e5157080000449e00000002")
	h, ok := parseInitialHeader(hdr)
	check("parse initial header", ok, "")
	check("type is Initial", h.typeName == "Initial" && h.version == 1, "")
	check("dcid/scid/token", hexEq(h.dcid, "8394c8f03e515708") &&
		len(h.scid) == 0 && len(h.token) == 0, "")
	// 「长度 1182 = 4 字节包号 + 1162 字节帧 + 16 字节标签」；0x449E 是线上带前缀的字节
	check("length == 1182 in 2-byte field", h.length == 1182 && h.lengthWidth == 2, "")
	check("pn_offset == 18", h.pnOffset == 18, "")
	check("pn length 4", pnLengthFromFirstByte(hdr[0]) == 4, "")
	check("packet number == 2", binaryBE(hdr[18:22]) == 2, "")
	check("sample offset == pn_offset+4", h.pnOffset+hpOffsetFromPN == 22, "")
}

func binaryBE(b []byte) uint64 {
	var v uint64
	for _, x := range b {
		v = v<<8 | uint64(x)
	}
	return v
}

// RFC 9001 附录 A.5：ChaCha20-Poly1305 短头包的完整端到端向量
func testChaCha20ShortPacket() {
	secret := mustHex("9ac312a7f877468ebe69422748ad00a1" +
		"5443f18203a07d6060f688f30f21632b")
	key, iv, hp := packetKeys(secret, 32, 12, 32)
	check("a5 key", hexEq(key,
		"c6d98ff3441c3fe1b2182094f69caa2ed4b716b65488960a7a984979fb23e1c8"), "")
	check("a5 iv", hexEq(iv, "e0459b3474bdd0e44a41c144"), "")
	check("a5 hp", hexEq(hp,
		"25a282b9e82f06f21f488917a4fc8f1b73573685608597d0efcb076b0ab7a7a4"), "")
	check("a5 ku", hexEq(nextSecret(secret),
		"1223504755036d556342ee9361d253421a826c9ecdf3c7148684b36b714881f9"), "")
	check("a5 nonce", hexEq(aeadNonce(iv, 654360564), "e0459b3474bdd0e46d417eb0"), "")

	// pn = 654360564 取 3 字节编码 → 0x00bff4（附录为省掉 PADDING 帧而固定 3 字节）
	pnBytes := encodePacketNumber(654360564, 3)
	check("pn low 3 bytes", hexEq(pnBytes, "00bff4"), "")
	check("sample algorithm gives 4 bytes", packetNumberBytes(654360564, false, 0) == 4, "")

	pkt := buildShortPacket(nil, pnBytes, 654360564, []byte{0x01}, key, iv, hp, 0)
	expect := "4cfe4189655e5cd55c41f69080575d7999c25a5bfb"
	check("protected 21-byte packet", len(pkt) == 21 && hexEq(pkt, expect), "")
	check("chacha20 hp mask", hexEq(headerMask(hp, pkt[5:21]), "aefefe7d03"), "")
	// 采样 = 包号起点(1) + 4 起 16 字节：3 字节包号会跳过 1 字节负载
	check("one payload byte skipped", pnOffsetShort(0)+pnLengthFromFirstByte(0x42) == 4, "")
	// 受保护首字节 0x4c 的低 2 位是 0，直接读会得出「1 字节包号」的错误结论
	check("protected first byte hides pn length", pnLengthFromFirstByte(pkt[0]) == 1, "")

	pn, pt, ok := openShortPacket(pkt, 0, 654360563, key, iv, hp)
	check("open roundtrip", ok && pn == 654360564 && bytes.Equal(pt, []byte{0x01}), "")

	tampered := append([]byte{}, pkt...)
	tampered[20] ^= 0x01
	_, _, ok = openShortPacket(tampered, 0, 654360563, key, iv, hp)
	check("tampered packet rejected", !ok, "")

	// 去掉头保护后必须还原出未保护头 4200bff4
	buf := append([]byte{}, pkt...)
	n, ok := removeHeaderProtection(buf, 1, hp)
	check("remove hp -> pn_len 3 + header", ok && n == 3 && hexEq(buf[:4], "4200bff4"), "")
}

// RFC 9000 §16：varint 边界与「不要求最短编码」
func testVarint() {
	for _, v := range []uint64{0, 63, 64, 16383, 16384, 1<<30 - 1, 1 << 30, varintMax} {
		enc := encodeVarint(v)
		got, used, ok := decodeVarint(enc, 0)
		check(fmt.Sprintf("varint roundtrip %d", v), ok && got == v && used == len(enc), "")
	}
	check("varint widths", varintLen(63) == 1 && varintLen(64) == 2 &&
		varintLen(16384) == 4 && varintLen(1<<30) == 8, "")
	got, used, ok := decodeVarint(mustHex("403f"), 0)
	check("non-minimal encoding is legal", ok && got == 63 && used == 2, "")
	_, _, ok = decodeVarint(mustHex("40"), 0) // 前缀声称 2 字节，实际只剩 1 字节
	check("truncated varint must be rejected", !ok, "")
}

// RFC 9000 附录 A.2/A.3：包号字节数与窗口还原
func testPacketNumber() {
	const largest = uint64(0xABE8B3)
	check("A.2 0xac5c02 -> 2 bytes", packetNumberBytes(0xAC5C02, true, largest) == 2, "")
	check("A.2 truncated value", hexEq(encodePacketNumber(0xAC5C02, 2), "5c02"), "")
	check("A.2 0xace8fe -> 3 bytes", packetNumberBytes(0xACE8FE, true, largest) == 3, "")
	check("A.3 decode 16-bit", decodePacketNumber(largest, 0x5C02, 16) == 0xAC5C02, "")
	// 0xACE8FE 不足 24 位，所以截断值就是它自己（传 16 位的 0xE8FE 会解错）
	check("A.3 decode 24-bit", decodePacketNumber(largest, 0xACE8FE, 24) == 0xACE8FE, "")
	check("first packet uses 1 byte", packetNumberBytes(2, false, 0) == 1, "")
	check("power-of-two boundary", packetNumberBytes(127, true, 0) == 1 &&
		packetNumberBytes(128, true, 0) == 1 && packetNumberBytes(129, true, 0) == 2, "")
	check("no unsigned underflow", decodePacketNumber(0, 0x40, 8) == 0x40, "")
	check("window edges", decodePacketNumber(0x0000FF, 0x00, 8) == 0x000100 &&
		decodePacketNumber(0x000100, 0xFF, 8) == 0x0000FF, "")
	check("min frame length", minFrameLength(1, 16) == 3 && minFrameLength(2, 16) == 2 &&
		minFrameLength(3, 16) == 1 && minFrameLength(4, 16) == 0, "")
}

// 自己组一个短头包做往返，覆盖「采样不足 / 错方向密钥」两条失败路径
func testTransport() {
	dcid := mustHex("0102030405060708")
	client, server := initialSecrets(dcid)
	key, iv, hp := packetKeys(client, 32, 12, 32)
	payload := make([]byte, 32)
	for i := range payload {
		payload[i] = byte(i)
	}
	pnBytes := encodePacketNumber(0xAC5C02, packetNumberBytes(0xAC5C02, true, 0xABE8B2))
	pkt := buildShortPacket(dcid, pnBytes, 0xAC5C02, payload, key, iv, hp, 0)
	check("short header", pkt[0]&longHeaderMask == 0, "")
	check("packet size", len(pkt) == 1+len(dcid)+len(pnBytes)+len(payload)+16, "")
	pn, pt, ok := openShortPacket(pkt, len(dcid), 0xABE8B2, key, iv, hp)
	check("roundtrip", ok && pn == 0xAC5C02 && bytes.Equal(pt, payload), "")

	_, sampleOKFlag := sampleFor(pkt[:20], pnOffsetShort(len(dcid)))
	check("short packet must be rejected", !sampleOKFlag, "")

	wrongKey, wrongIV, wrongHP := packetKeys(server, 32, 12, 32)
	_, _, ok = openShortPacket(pkt, len(dcid), 0xABE8B2, wrongKey, wrongIV, wrongHP)
	check("wrong direction keys must fail", !ok, "")

	nk, niv, nhp := packetKeys(nextSecret(client), 32, 12, 32)
	_, _, ok = openShortPacket(pkt, len(dcid), 0xABE8B2, nk, niv, nhp)
	check("updated keys must not open old packet", !ok, "")
	check("hp mask is deterministic & sample-dependent",
		bytes.Equal(headerMask(hp, make([]byte, 16)), headerMask(hp, make([]byte, 16))) &&
			!bytes.Equal(headerMask(hp, make([]byte, 16)), headerMask(hp, ramp(16))), "")
}

func ramp(n int) []byte {
	b := make([]byte, n)
	for i := range b {
		b[i] = byte(i + 1)
	}
	return b
}

func main() {
	groups := []struct {
		name string
		fn   func()
	}{
		{"RFC 9001 A.1 initial keys", testInitialKeys},
		{"RFC 9001 A.2 initial header", testInitialHeader},
		{"RFC 9001 A.5 chacha20 short packet", testChaCha20ShortPacket},
		{"RFC 9000 §16 varint", testVarint},
		{"RFC 9000 A.2/A.3 packet number", testPacketNumber},
		{"short packet transport", testTransport},
	}
	for _, g := range groups {
		before, badBefore := checks, failures
		g.fn()
		mark := ""
		if failures != badBefore {
			mark = "  <-- FAIL"
		}
		fmt.Printf("  %-38s %d checks%s\n", g.name, checks-before, mark)
	}
	fmt.Printf("quic_main: %d checks passed, %d failed\n", checks-failures, failures)
}
