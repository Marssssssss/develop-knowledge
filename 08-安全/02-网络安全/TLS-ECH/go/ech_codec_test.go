// ech_codec_test.go —— B 组：编解码；C 组：填充公式
//
// 编解码解析的是「对端发来的字节」与「DNS 里拉来的 ECHConfig」，越界读就是可得的
// 内存安全问题，所以每个负例都要真的构造出来：尾部多余字节、重复扩展、带未知强制
// 扩展的配置。填充公式则由 §6.1.3 的三条式子唯一确定，可以逐条验证。
package main

import "bytes"

func checkCodec() int {
	before := checksTotal
	pk := mustHex(vecPKR)
	cfg := makeConfig(testConfigID, pk)

	wire, err := cfg.Encode()
	check("ECHConfig 以 version(0xfe0d) 开头",
		err == nil && len(wire) > 8 && wire[0] == 0xFE && wire[1] == 0x0D, "")
	check("ECHConfig.length 覆盖 contents（不含 version 与 length 自己）",
		err == nil && int(wire[2])<<8|int(wire[3]) == len(wire)-4, "")

	back, errB := decodeEchConfig(wire)
	again, errA := back.Encode()
	check("ECHConfig 解码后再编码逐字节相同",
		errB == nil && errA == nil && bytes.Equal(wire, again), "")
	check("public_name / maximum_name_length 还原正确",
		errB == nil && string(back.PublicName) == testPublic &&
			int(back.MaxNameLength) == testMaxName, "")
	check("密钥配置四元组还原正确",
		errB == nil && back.KeyConfig.ConfigID == testConfigID &&
			back.KeyConfig.KemID == kemIDX25519 &&
			len(back.KeyConfig.CipherSuites) == 1 &&
			back.KeyConfig.CipherSuites[0] == [2]uint16{kdfIDHKDFSHA256, aeadIDChaCha20Poly1305} &&
			bytes.Equal(back.KeyConfig.PublicKey, pk), "")

	tail := append(append([]byte{}, wire...), 0x00)
	_, errT := decodeEchConfig(tail)
	check("ECHConfig 尾部多一字节必须报错", errT != nil, "")

	// 本实现只支持无扩展的 ECHConfig：往尾部接一个「未知且强制」（type 高位为 1）的
	// 扩展，整条配置必须被拒绝，而**不是**被静默忽略（§4.1）
	dirty := append([]byte{}, wire...)
	dirty[len(dirty)-2], dirty[len(dirty)-1] = 0x00, 0x04 // extensions<0..2^16-1>
	dirty = append(dirty, 0x80, 0x01, 0x00, 0x00)
	nlen := len(dirty) - 4
	dirty[2], dirty[3] = byte(nlen>>8), byte(nlen)
	_, errM := decodeEchConfig(dirty)
	check("带未知强制扩展(0x8001)的 ECHConfig 必须整条拒绝", errM != nil, "")

	rnd := bytes.Repeat([]byte{0x11}, 32)
	sid := bytes.Repeat([]byte{0x22}, 32)
	ch := makeInner(rnd, sid)
	body, errE := ch.Encode()
	dec, consumed, errD := decodeClientHello(body)
	check("ClientHello 编解码往返（consumed == 总长）",
		errE == nil && errD == nil && consumed == len(body), "")
	check("往返后逐字段一致（含扩展顺序）", errD == nil && sameCH(ch, dec), "")
	// 这正是 EncodedClientHelloInner 尾部零填充所依赖的行为：解码器只吃到 ClientHello
	// 的边界，剩下的字节留给调用方判定是否全零 —— 所以这里**不**要求吃光
	_, consumed2, errD2 := decodeClientHello(append(append([]byte{}, body...), 0x00))
	check("尾部多字节时 consumed 只吃 ClientHello 本体",
		errD2 == nil && consumed2 == len(body), "")

	dup := ch.copy()
	dup.Exts = []ext{{Type: extSupportedVersion, Data: []byte{0x02, 0x03, 0x04}},
		{Type: extSupportedVersion, Data: []byte{0x02, 0x03, 0x04}}}
	dupWire, _ := dup.Encode()
	_, _, errDup := decodeClientHello(dupWire)
	check("重复扩展必须报错（RFC 8446 §4.2）", errDup != nil, "")
	return checksTotal - before
}

func checkPadding() int {
	before := checksTotal
	rnd := bytes.Repeat([]byte{0x33}, 32)
	sid := bytes.Repeat([]byte{0x44}, 32)
	inner := makeInner(rnd, sid)
	sniLen := len(testSNI)

	n1, e1 := namePadding(inner, testMaxName)
	check("有 SNI：补 max(0, M - D)", e1 == nil && n1 == testMaxName-sniLen, "")
	n2, e2 := namePadding(inner, sniLen)
	check("M == D：补 0", e2 == nil && n2 == 0, "")
	n3, e3 := namePadding(inner, sniLen-1)
	check("M < D：补 0 而不是负数", e3 == nil && n3 == 0, "")

	noSNI := inner.copy()
	noSNI.Exts = nil
	noSNI.setExt(extKeyShare, keyShareExt()) // 去掉 server_name
	n4, e4 := namePadding(noSNI, testMaxName)
	check("无 SNI：补 M + 9（= 一个 M 字节 host_name 的 server_name 扩展长度）",
		e4 == nil && n4 == testMaxName+9, "")

	// server_name 的 extension_data 是 ServerNameList，要解两层才到 name_type：
	// Data[0..1] 是列表长度，Data[2] 才是 name_type
	bad := inner.copy()
	bad.Exts[0].Data[2] = 0x01
	_, e5 := namePadding(bad, testMaxName)
	check("name_type 不是 host_name 必须报错", e5 != nil, "")

	allOK := true
	for _, l := range []int{1, 2, 31, 32, 33, 63, 64, 65, 100} {
		total := l + roundTo32(l)
		if total%32 != 0 || total < l || total-l >= 32 {
			allOK = false
		}
	}
	check("round_to_32：补齐到 32 的倍数且补得最少", allOK, "")

	enc, errE := encodedClientHelloInner(inner, 7)
	check("EncodedClientHelloInner 清空 legacy_session_id 再补零",
		errE == nil && len(enc) > 7 && bytes.Equal(enc[len(enc)-7:], make([]byte, 7)), "")
	dec, consumed, errD := decodeClientHello(enc[:len(enc)-7])
	check("去掉尾部填充后可解码且 session_id 为空",
		errD == nil && len(dec.SessionID) == 0 && consumed == len(enc)-7, "")
	return checksTotal - before
}
