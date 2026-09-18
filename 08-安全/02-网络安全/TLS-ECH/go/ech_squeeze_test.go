// ech_squeeze_test.go —— F 组：ech_outer_extensions 压缩与四条 abort；G 组：接受确认值
//
// F 的四条 MUST abort（§5.1）是 ECH 防放大攻击的全部内容：少了任何一条，攻击者就能
// 用一条小请求让服务端吐出一条大响应。四条各造一个用例，按错误哨兵值分辨。
//
// G 没有官方向量（RFC 9849 不提供），所以判据是「确定性 + 区分度 + 手工复算」：
// 用 RFC 8446 §7.1 的 HkdfLabel 编码手工拼一遍，确认没有误用 HPKE 的 LabeledExpand。
package main

import (
	"bytes"
	"errors"
)

func checkOuterExt() int {
	before := checksTotal
	pk, sk := mustHex(vecPKR), mustHex(vecSKR)
	cfg := makeConfig(testConfigID, pk)
	rnd := bytes.Repeat([]byte{0x88}, 32)
	sid := bytes.Repeat([]byte{0x99}, 32)
	inner := makeInner(rnd, sid)
	tpl := makeOuterTpl(rnd, sid)
	names := []uint16{extSupportedVersion, extKeyShare}

	sq, err := compressInner(inner, tpl, names)
	check("压缩成功", err == nil, "")
	check("ech_outer_extensions 落在被删的第一个扩展的位置",
		sq.extIndex(echOuterExtensions) == 1, "")
	check("与 outer 逐字节相同的扩展被省掉（supported_versions / key_share）",
		sq.extIndex(extSupportedVersion) < 0 && sq.extIndex(extKeyShare) < 0, "")
	check("不参与压缩的 server_name 必须保留", sq.extIndex(extServerName) >= 0, "")

	body, errB := sq.Encode()
	exp, errE := decompressInner(body, tpl)
	check("还原后与 ClientHelloInner 逐字段一致（含扩展顺序）",
		errB == nil && errE == nil && sameCH(exp, inner), "")

	// 四条 MUST abort，各造一个用例
	bad1 := sq.copy()
	bad1.setExt(echOuterExtensions, []byte{0x00, 0x2B, 0x00, 0x33, 0x12, 0x34})
	w1, _ := bad1.Encode()
	_, e1 := decompressInner(w1, tpl)
	check("abort 1：引用了 ClientHelloOuter 里没有的扩展",
		errors.Is(e1, errOuterExtMissing), "")

	bad2 := sq.copy()
	bad2.setExt(echOuterExtensions, []byte{0x00, 0x33, 0x00, 0x33})
	w2, _ := bad2.Encode()
	_, e2 := decompressInner(w2, tpl)
	check("abort 2：重复引用同一扩展", errors.Is(e2, errOuterExtDupe), "")

	bad3 := sq.copy()
	bad3.setExt(echOuterExtensions, []byte{0xFE, 0x0D})
	w3, _ := bad3.Encode()
	_, e3 := decompressInner(w3, tpl)
	check("abort 3：引用了 encrypted_client_hello 自己", errors.Is(e3, errOuterExtSelf), "")

	bad4 := sq.copy()
	bad4.setExt(echOuterExtensions, []byte{0x00, 0x33, 0x00, 0x2B})
	w4, _ := bad4.Encode()
	_, e4 := decompressInner(w4, tpl)
	check("abort 4：相对顺序与 outer 相反（防放大攻击）",
		errors.Is(e4, errOuterExtOrder), "")

	// 压缩省掉的是「重复发送」而不是「不发送」—— 端到端必须仍然逐字节还原
	_, _, plainEnc, errP := clientEncrypt(inner, tpl, cfg, nil)
	compOuter, _, shrunkEnc, errC := clientEncrypt(inner, tpl, cfg, names)
	check("压缩后总长至少省下 32 字节（足以抵消一轮 32 字节对齐）",
		errP == nil && errC == nil && len(shrunkEnc)+32 <= len(plainEnc), "")
	got, errS := serverDecrypt(compOuter, sk, pk, cfg)
	check("压缩路径下服务端仍能逐字节还原 ClientHelloInner",
		errS == nil && sameCH(got, inner), "")
	return checksTotal - before
}

func checkConfirmation() int {
	before := checksTotal
	rnd := bytes.Repeat([]byte{0xAA}, 32)
	rnd2 := bytes.Repeat([]byte{0xAB}, 32)
	transcript := transcriptHash([]byte("ClientHelloInner"), []byte("ServerHello"))
	check("transcript = SHA-256(ClientHelloInner || ServerHello)", len(transcript) == 32, "")

	ac := acceptConfirmation(rnd, transcript)
	check("accept_confirmation 为 8 字节", len(ac) == 8, "")
	check("同一 (inner random, transcript) 必然得到同一值",
		constantTimeEq(ac, acceptConfirmation(rnd, transcript)), "")
	check("换 inner random → 值不同",
		!constantTimeEq(ac, acceptConfirmation(rnd2, transcript)), "")
	shifted := append(append([]byte{}, transcript...), 0x00)
	check("transcript 多一个字节 → 值不同",
		!constantTimeEq(ac, acceptConfirmation(rnd, shifted)), "")
	hrr := hrrAcceptConfirmation(rnd, transcript)
	check("HRR 版用不同标签，值必然不同",
		len(hrr) == 8 && !constantTimeEq(ac, hrr), "")

	// 手工按 RFC 8446 §7.1 拼一遍 HkdfLabel：
	//   u16(L) | u8(len("tls13 "+label)) | "tls13 " + label | u8(len(context)) | context
	// 注意这**不是** HPKE 的 LabeledExpand（那里 I2OSP(L,2) 在最前且带 "HPKE-v1" 与
	// suite_id）—— 两者长得像、编码完全不同，混用不会报错，只会得到错的值
	full := "tls13 ech accept confirmation"
	label := append([]byte{0x00, 0x08, byte(len(full))}, []byte(full)...)
	label = append(label, byte(len(transcript)))
	label = append(label, transcript...)
	manual := hkdfExpand(hkdfExtract(nil, rnd), label, 8)
	check("手工复算 HkdfLabel 与实现一致", constantTimeEq(ac, manual), "")
	return checksTotal - before
}
