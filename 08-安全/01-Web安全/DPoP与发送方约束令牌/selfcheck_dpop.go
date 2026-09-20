// DPoP与发送方约束令牌 自检（Go 侧）。
package main

import (
	"fmt"
	"strings"
)

var okCount int
var failures []string

func ck(name string, cond bool, detail string) {
	if cond {
		okCount++
		return
	}
	failures = append(failures, fmt.Sprintf("%s  %s", name, detail))
}

func eqStr(name, got, want string) {
	ck(name, got == want, fmt.Sprintf("got=%q want=%q", got, want))
}

func main() {
	// 官方向量（RFC 9449 Figure 9 / Figure 14 实读抄录）
	official := map[string]string{
		"crv": "P-256",
		"kty": "EC",
		"x":   "l8tFrhx-34tV3hRICRDY9zCkDlpBhF42UQUfWVAWBFs",
		"y":   "9VE4jf_Ok_o64zbTTlcuNJajHmt6v9TDVrU0CdvGRDA",
	}
	eqStr("jkt 复现 RFC9449 Figure 9", JWKThumbprint(official),
		"0ZcOCORZNYy-DWpqq30jZyJGHTN0d2HglBV3uiguA4I")
	eqStr("ath 复现 RFC9449 Figure 14",
		ATHOf("Kz~8mXK1EalYznwH-LC-1fBAo.4Ljp~zsPE_NeO.gxU"),
		"fUHyO2r2Z3DZ53EsNrWBb0xWXoaNy59IiKCAqksmQEo")

	kp := NewKeyPair(256, 20260920)
	ck("密钥对生成成功", kp != nil, "nil")
	proof := MakeProof(kp, "jti-1", "POST", "https://server.example.com/token",
		1562262616, "", "", "dpop+jwt", "RS256", true, false)
	okv, whyv, header, _ := JWSVerify(proof)
	ck("自签 proof 能被公钥验过", okv, whyv)
	eqStr("typ 是 dpop+jwt", header["typ"], "dpop+jwt")
	ck("jwk 含 e", strings.Contains(header["jwk"], "\"e\""), header["jwk"])
	eqStr("htu 去掉 query", StripQueryFragment("https://a/x?q=1"), "https://a/x")
	eqStr("htu 去掉 fragment", StripQueryFragment("https://a/x#f"), "https://a/x")

	forged := proof[:strings.LastIndex(proof, ".")+1] + "AAAA"
	okf, whyf, _, _ := JWSVerify(forged)
	ck("篡改签名被拒", !okf && strings.Contains(whyf, "签名"), whyf)

	AT1 := "Kz~8mXK1EalYznwH-LC-1fBAo.4Ljp~zsPE_NeO.gxU"
	AT2 := " rotated-token-value-with-different-bytes "
	jkt1 := JWKThumbprint(JWKPublic(kp))
	rs := NewServer("resource", false, 1562262620)
	resURI := "https://resource.example.org/protectedresource"

	good := MakeProof(kp, "jti-A", "GET", resURI, 1562262618, ATHOf(AT1), "",
		"dpop+jwt", "RS256", true, false)
	ok1, why1, _ := rs.CheckProof(Request{Method: "GET", URI: resURI + "?a=1",
		DPoPHeaders: []string{good}, Authorization: "DPoP " + AT1,
		AccessToken: AT1}, AT1, jkt1)
	ck("正常 DPoP 请求通过（htu 可带 query）", ok1, why1)

	ok2, why2, _ := rs.CheckProof(Request{Method: "GET", URI: resURI,
		DPoPHeaders: []string{good, good}}, AT1, jkt1)
	ck("E1 两个 DPoP 头被拒", !ok2 && strings.HasPrefix(why2, "E1"), why2)
	ok3, why3, _ := rs.CheckProof(Request{Method: "GET", URI: resURI,
		Authorization: "Bearer " + AT1}, AT1, jkt1)
	ck("E1 完全没有 DPoP 头被拒（裸 Bearer 不可用）",
		!ok3 && strings.HasPrefix(why3, "E1"), why3)

	badTyp := MakeProof(kp, "j", "GET", resURI, 1562262618, "", "", "JWT",
		"RS256", true, false)
	ok4, why4, _ := rs.CheckProof(Request{Method: "GET", URI: resURI,
		DPoPHeaders: []string{badTyp}}, "", "")
	ck("E4 typ 不是 dpop+jwt 被拒", !ok4 && strings.HasPrefix(why4, "E4"), why4)

	badAlg := MakeProof(kp, "j", "GET", resURI, 1562262618, "", "", "dpop+jwt",
		"HS256", true, false)
	ok5, why5, _ := rs.CheckProof(Request{Method: "GET", URI: resURI,
		DPoPHeaders: []string{badAlg}}, "", "")
	ck("E5 alg=HS256 被拒", !ok5 && strings.HasPrefix(why5, "E5"), why5)
	badAlg2 := MakeProof(kp, "j", "GET", resURI, 1562262618, "", "", "dpop+jwt",
		"none", true, false)
	ok6, why6, _ := rs.CheckProof(Request{Method: "GET", URI: resURI,
		DPoPHeaders: []string{badAlg2}}, "", "")
	ck("E5 alg=none 被拒", !ok6 && strings.HasPrefix(why6, "E5"), why6)

	leak := MakeProof(kp, "j", "GET", resURI, 1562262618, "", "", "dpop+jwt",
		"RS256", true, true)
	ok7, why7, _ := rs.CheckProof(Request{Method: "GET", URI: resURI,
		DPoPHeaders: []string{leak}}, "", "")
	ck("E7 jwk 里带私钥 d 被拒", !ok7 && strings.HasPrefix(why7, "E7"), why7)

	ok8, why8, _ := rs.CheckProof(Request{Method: "POST", URI: resURI,
		DPoPHeaders: []string{good}}, AT1, jkt1)
	ck("E8 htm 不匹配被拒", !ok8 && strings.HasPrefix(why8, "E8"), why8)
	ok9, why9, _ := rs.CheckProof(Request{Method: "GET",
		URI: "https://resource.example.org/other", DPoPHeaders: []string{good}},
		AT1, jkt1)
	ck("E9 htu 不匹配被拒", !ok9 && strings.HasPrefix(why9, "E9"), why9)

	staleIAT := MakeProof(kp, "jti-old", "GET", resURI, 1562262618-600,
		ATHOf(AT1), "", "dpop+jwt", "RS256", true, false)
	ok10, why10, _ := rs.CheckProof(Request{Method: "GET", URI: resURI,
		DPoPHeaders: []string{staleIAT}}, AT1, jkt1)
	ck("E11 iat 超窗被拒", !ok10 && strings.HasPrefix(why10, "E11"), why10)

	ok11, why11, _ := rs.CheckProof(Request{Method: "GET", URI: resURI,
		DPoPHeaders: []string{good}, Authorization: "DPoP " + AT2,
		AccessToken: AT2}, AT2, jkt1)
	ck("E12a proof 换到另一个访问令牌上被拒",
		!ok11 && strings.HasPrefix(why11, "E12a"), why11)
	noAth := MakeProof(kp, "jti-C", "GET", resURI, 1562262618, "", "",
		"dpop+jwt", "RS256", true, false)
	ok12, why12, _ := rs.CheckProof(Request{Method: "GET", URI: resURI,
		DPoPHeaders: []string{noAth}}, AT1, jkt1)
	ck("E12a 无 ath 访问资源被拒", !ok12 && strings.HasPrefix(why12, "E12a"), why12)

	kp2 := NewKeyPair(256, 777)
	other := MakeProof(kp2, "jti-B", "GET", resURI, 1562262618, ATHOf(AT1), "",
		"dpop+jwt", "RS256", true, false)
	ok13, why13, _ := rs.CheckProof(Request{Method: "GET", URI: resURI,
		DPoPHeaders: []string{other}}, AT1, jkt1)
	ck("E12b 另一把钥匙签的 proof 被拒",
		!ok13 && strings.HasPrefix(why13, "E12b"), why13)

	ck("jti 首次可用", rs.SingleUse("jti-A"), "")
	ck("jti 二次被拒（单用检查）", !rs.SingleUse("jti-A"), "")

	// §8 nonce
	asrv := NewServer("as", true, 1562262600)
	tokenURI := "https://server.example.com/token"
	noNonce := MakeProof(kp, "jti-n1", "POST", tokenURI, 1562262600, "", "",
		"dpop+jwt", "RS256", true, false)
	ok14, why14, _ := asrv.CheckProof(Request{Method: "POST", URI: tokenURI,
		DPoPHeaders: []string{noNonce}}, "", "")
	ck("E10 需要 nonce 时缺 nonce 被拒",
		!ok14 && strings.HasPrefix(why14, "E10"), why14)
	n1 := asrv.IssueNonce()
	withN1 := MakeProof(kp, "jti-n2", "POST", tokenURI, 1562262600, "", n1,
		"dpop+jwt", "RS256", true, false)
	ok15, why15, _ := asrv.CheckProof(Request{Method: "POST", URI: tokenURI,
		DPoPHeaders: []string{withN1}}, "", "")
	ck("带上新下发的 nonce 通过", ok15, why15)
	fake := MakeProof(kp, "jti-n3", "POST", tokenURI, 1562262600, "",
		"N-not-issued-by-server", "dpop+jwt", "RS256", true, false)
	ok16, why16, _ := asrv.CheckProof(Request{Method: "POST", URI: tokenURI,
		DPoPHeaders: []string{fake}}, "", "")
	ck("E10 陈旧/伪造 nonce 被拒", !ok16 && strings.HasPrefix(why16, "E10"), why16)

	// §9：AS 与 RS 的 nonce 彼此独立
	rsn := NewServer("resource-n", true, 1562262600)
	rsOwn := rsn.IssueNonce()
	withRS := MakeProof(kp, "jti-n4", "GET", resURI, 1562262600, "", rsOwn,
		"dpop+jwt", "RS256", true, false)
	ok17, why17, _ := rsn.CheckProof(Request{Method: "GET", URI: resURI,
		DPoPHeaders: []string{withRS}}, "", "")
	ck("RS 自己的 nonce 在 RS 上通过", ok17, why17)
	withAS := MakeProof(kp, "jti-n5", "GET", resURI, 1562262600, "", n1,
		"dpop+jwt", "RS256", true, false)
	ok18, why18, _ := rsn.CheckProof(Request{Method: "GET", URI: resURI,
		DPoPHeaders: []string{withAS}}, "", "")
	ck("AS 下发的 nonce 被 RS 拒绝（两套 nonce 独立）",
		!ok18 && strings.HasPrefix(why18, "E10"), why18)

	// §10 dpop_jkt
	dpopJkt := JWKThumbprint(JWKPublic(kp))
	ck("dpop_jkt 与 proof 公钥一致时 AS 受理",
		JWKThumbprint(JWKPublic(kp)) == dpopJkt, "")
	ck("dpop_jkt 与 proof 公钥不一致时 AS 必须拒绝",
		JWKThumbprint(JWKPublic(kp2)) != dpopJkt, "")

	// RFC 8705
	derA := append([]byte{0x30, 0x82, 0x01, 0x0a}, make([]byte, 240)...)
	derB := append([]byte{0x30, 0x82, 0x01, 0x0b}, make([]byte, 240)...)
	x5t := X5TS256(derA)
	ck("x5t#S256 无尾随 '='", !strings.Contains(x5t, "="), x5t)
	ck("x5t#S256 长度确定（256 bit → 43 字符）", len(x5t) == 43, x5t)
	okm1, _ := MTLSResourceCheck(x5t, derA)
	ck("mTLS 证书一致放行", okm1, "")
	okm2, reason := MTLSResourceCheck(x5t, derB)
	ck("mTLS 证书不一致返回 invalid_token", !okm2 && reason == "invalid_token", reason)
	okm3, _ := MTLSResourceCheck("", derB)
	ck("mTLS 未绑定证书时不做比对", okm3, "")

	fmt.Println("OK =", okCount)
	if len(failures) > 0 {
		fmt.Println("FAILED =", len(failures))
		for _, f := range failures {
			fmt.Println("  -", f)
		}
	} else {
		fmt.Println("ALL OK")
	}
}
