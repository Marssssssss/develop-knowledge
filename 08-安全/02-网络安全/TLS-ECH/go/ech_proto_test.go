// ech_proto_test.go —— D 组：端到端往返；E 组：篡改与错密钥必然失败
//
// 判据不是魔数而是**性质**：
//
//	D  线上看不到真正的 SNI；服务端还原出的 ClientHelloInner 与客户端输入逐字节相同；
//	   legacy_session_id 以 ClientHelloOuter 为准。
//	E  任何被 AAD 覆盖的字段被改动 → AEAD 认证失败；config_id 不匹配 → errEchConfigID
//	   （服务端应换下一个 ECHConfig 重试）；填充非零 → errEchPadding。
package main

import (
	"bytes"
	"errors"
)

func checkRoundtrip() int {
	before := checksTotal
	pk, sk := mustHex(vecPKR), mustHex(vecSKR)
	cfg := makeConfig(testConfigID, pk)
	rnd := bytes.Repeat([]byte{0x55}, 32)
	rndB := bytes.Repeat([]byte{0x77}, 32)
	sid := bytes.Repeat([]byte{0x66}, 32)
	sidB := bytes.Repeat([]byte{0x99}, 32)
	inner := makeInner(rnd, sid)
	tpl := makeOuterTpl(rndB, sid)

	outer, ctx, encoded, err := clientEncrypt(inner, tpl, cfg, nil)
	check("客户端封装成功", err == nil, "")
	if err != nil {
		return checksTotal - before
	}

	cleared := inner.copy()
	cleared.SessionID = nil
	cleanWire, errC := cleared.Encode()
	check("EncodedClientHelloInner 长度是 32 的倍数（§6.1.3 第 2 步）",
		errC == nil && len(encoded) > len(cleanWire) && len(encoded)%32 == 0, "")
	check("前缀就是清空 session_id 的 ClientHelloInner",
		bytes.HasPrefix(encoded, cleanWire), "")
	allZero := true
	for _, b := range encoded[len(cleanWire):] {
		if b != 0 {
			allZero = false
		}
	}
	check("其余全部是零填充", allZero, "")

	o, errO := decodeOuterExt(outer.Ext(echExtType))
	check("ClientHelloOuter 带 ECH 扩展且能按 outer 变体解出字段", errO == nil, "")
	check("config_id 与套件与 ECHConfig 一致",
		errO == nil && o.ConfigID == testConfigID && o.KdfID == kdfIDHKDFSHA256 &&
			o.AeadID == aeadIDChaCha20Poly1305, "")
	check("enc 是 32 字节 X25519 公钥；payload = 密文 + 16 字节 tag",
		errO == nil && len(o.Enc) == 32 && len(o.Payload) == len(encoded)+aeadTagLen, "")
	check("客户端只 Seal 了一次（§6.1.1 只做一次密钥封装）", ctx.Seq == 1, "")

	// AAD 与最终 ClientHelloOuter 等长，所以把占位换成真密文时不用重算长度前缀
	aadLike := outer.copy()
	errR := rebuildEchExt(aadLike, make([]byte, len(o.Payload)))
	realWire, errW1 := outer.Encode()
	aadWire, errW2 := aadLike.Encode()
	check("payload 换成等长零后总长度不变（AAD 可以先算后填）",
		errR == nil && errW1 == nil && errW2 == nil && len(aadWire) == len(realWire), "")

	got, errS := serverDecrypt(outer, sk, pk, cfg)
	check("服务端解封成功", errS == nil, "")
	check("ClientHelloInner.random 还原", errS == nil && bytes.Equal(got.Random, rnd), "")
	check("legacy_session_id 从 ClientHelloOuter 抄回",
		errS == nil && bytes.Equal(got.SessionID, sid), "")
	check("还原后与 ClientHelloInner 逐字段一致（含扩展顺序）",
		errS == nil && sameCH(got, inner), "")
	check("真正的 SNI 不出现在线上字节里", !hasSub(realWire, testSNI), "")
	check("线上只有 public_name", hasSub(realWire, testPublic), "")

	tplB := makeOuterTpl(rndB, sidB)
	outerB, _, _, errB := clientEncrypt(inner, tplB, cfg, nil)
	gotB, errSB := serverDecrypt(outerB, sk, pk, cfg)
	check("session_id 一律以 ClientHelloOuter 为准（与 inner 里放的值无关）",
		errB == nil && errSB == nil && bytes.Equal(gotB.SessionID, sidB), "")
	return checksTotal - before
}

func checkTamper() int {
	before := checksTotal
	pk, sk := mustHex(vecPKR), mustHex(vecSKR)
	cfg := makeConfig(testConfigID, pk)
	wrongID := makeConfig(testConfigID^0x01, pk)
	otherSK, _, errK := deriveKeyPair(make([]byte, 32))
	rnd := bytes.Repeat([]byte{0x55}, 32)
	sid := bytes.Repeat([]byte{0x66}, 32)
	inner := makeInner(rnd, sid)
	tpl := makeOuterTpl(rnd, sid)

	outer, _, _, err := clientEncrypt(inner, tpl, cfg, nil)
	_, errOK := serverDecrypt(outer, sk, pk, cfg)
	check("（前置）未篡改时解封成功", err == nil && errOK == nil, "")
	if err != nil || errK != nil {
		return checksTotal - before
	}

	tr1 := outer.copy()
	tr1.CipherSuites = []uint16{0x1301}
	_, e1 := serverDecrypt(tr1, sk, pk, cfg)
	check("改 cipher_suites → 认证失败（AAD 覆盖了它）", errors.Is(e1, errHpkeAuth), "")

	tr2 := outer.copy()
	tr2.setExt(extServerName, serverNameExt("attacker.example"))
	_, e2 := serverDecrypt(tr2, sk, pk, cfg)
	check("改 ClientHelloOuter 的 SNI → 认证失败", errors.Is(e2, errHpkeAuth), "")

	// payload 是 ECH 扩展的最后一个字段，而 ECH 扩展又是整条 ClientHello 的最后一个
	// 扩展 —— 所以线上最后一个字节一定落在 payload 里，翻转它必然破坏 AEAD 的 tag
	wire, _ := outer.Encode()
	wire[len(wire)-1] ^= 0x01
	tr3, _, errDec := decodeClientHello(wire)
	_, e3 := serverDecrypt(tr3, sk, pk, cfg)
	check("翻转 payload 末字节 → 认证失败", errDec == nil && errors.Is(e3, errHpkeAuth), "")

	_, e4 := serverDecrypt(outer, sk, pk, wrongID)
	check("config_id 不匹配 → 试解密失败（服务端应换下一个 ECHConfig）",
		errors.Is(e4, errEchConfigID), "")

	_, e5 := serverDecrypt(outer, otherSK, pk, cfg)
	check("换一把私钥（config_id 相同）→ 密钥计划不同，认证失败",
		errors.Is(e5, errHpkeAuth), "")

	// §5.1：EncodedClientHelloInner 尾部的填充必须全零
	dirty, errD := encodedClientHelloInner(inner, 8)
	dirty[len(dirty)-1] = 0x01
	raw, errRaw := encryptRaw(tpl, cfg, dirty)
	_, e6 := serverDecrypt(raw, sk, pk, cfg)
	check("非零填充 → 填充错误",
		errD == nil && errRaw == nil && errors.Is(e6, errEchPadding), "")
	return checksTotal - before
}
