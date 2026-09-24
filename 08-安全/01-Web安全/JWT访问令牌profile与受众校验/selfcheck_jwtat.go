package main

// JWT访问令牌profile与受众校验 自检（与 selfcheck_jwtat.py 同一套断言）。
// 依据 RFC 9068（https://www.rfc-editor.org/rfc/rfc9068.txt）实读。

import "fmt"

var okCount int
var failed []string

func ck(name string, cond bool, detail string) {
	if cond {
		okCount++
	} else {
		failed = append(failed, name+" "+detail)
	}
}

func eq(name string, got, want interface{}) {
	ck(name, fmt.Sprintf("%v", got) == fmt.Sprintf("%v", want),
		fmt.Sprintf("got=%v want=%v", got, want))
}

const iss = "https://as.example.com"
const resA = "https://api-a.example.com/"
const resB = "https://api-b.example.com/"
const now = 1700000000

func tok(aud interface{}) Token {
	return MakeAccessToken(iss, aud, "user-1", "client-1", now, 300, "at+jwt", "RS256", nil)
}

func main() {
	eq("REQUIRED 声明共七个", len(RequiredClaims), 7)
	ck("含 client_id", contains(RequiredClaims, "client_id"), "")
	ck("含 jti", contains(RequiredClaims, "jti"), "")

	rs := ResourceServer{ResourceID: resA, Issuer: iss}
	r := rs.Validate(tok(resA), now, true)
	ck("合规令牌通过", r.OK, r.Step)
	eq("默认 typ 是 at+jwt", tok(resA).Header["typ"], "at+jwt")
	eq("默认 alg 是 RS256", tok(resA).Header["alg"], "RS256")

	// §2.1 typ
	full := MakeAccessToken(iss, resA, "u", "c", now, 300, "application/at+jwt", "RS256", nil)
	ck("application/at+jwt 也接受", rs.Validate(full, now, true).OK, "")
	idTok := MakeAccessToken(iss, resA, "u", "c", now, 300, "JWT", "RS256", nil)
	r = rs.Validate(idTok, now, true)
	ck("ID Token 的 typ=JWT 被拒", !r.OK && r.Step == "typ", r.Step)
	eq("typ 失败也是 invalid_token", r.Err, "invalid_token")
	noTyp := Token{Header: map[string]interface{}{"alg": "RS256"},
		Claims: map[string]interface{}{"iss": iss, "exp": now + 300}}
	for _, c := range RequiredClaims {
		if _, ok := noTyp.Claims[c]; !ok {
			noTyp.Claims[c] = "x"
		}
	}
	ck("typ 缺失被拒", rs.Validate(noTyp, now, true).Step == "typ", "")

	// §2.2 REQUIRED 声明
	for _, missing := range RequiredClaims {
		claims := map[string]interface{}{"iss": iss, "aud": resA, "exp": now + 300}
		for _, c := range RequiredClaims {
			claims[c] = "x"
		}
		claims["iss"], claims["aud"], claims["exp"] = iss, resA, now+300
		delete(claims, missing)
		bad := Token{Header: map[string]interface{}{"typ": "at+jwt", "alg": "RS256"},
			Claims: claims}
		rr := rs.Validate(bad, now, true)
		ck("缺 "+missing+" 被拒", !rr.OK && rr.Step == "required_claims", rr.Step)
	}

	// §4 iss 精确相等
	r = rs.Validate(MakeAccessToken("https://as.example.com/", resA, "u", "c",
		now, 300, "at+jwt", "RS256", nil), now, true)
	ck("斜杠后缀使 iss 不等", !r.OK && r.Step == "iss", r.Step)
	r = rs.Validate(MakeAccessToken("HTTPS://AS.EXAMPLE.COM", resA, "u", "c",
		now, 300, "at+jwt", "RS256", nil), now, true)
	ck("大小写不同即不等", !r.OK && r.Step == "iss", r.Step)

	// §4 aud
	r = rs.Validate(tok(resB), now, true)
	ck("发往别的资源被拒（跨 JWT 混淆）", !r.OK && r.Step == "aud", r.Step)
	multi := MakeAccessToken(iss, []interface{}{resB, resA}, "u", "c",
		now, 300, "at+jwt", "RS256", nil)
	ck("aud 数组含自身即通过", rs.Validate(multi, now, true).OK, "")
	eq("AudList 展开", fmt.Sprint(multi.AudList()), "["+resB+" "+resA+"]")
	rsB := ResourceServer{ResourceID: resB, Issuer: iss}
	ck("A 的令牌被 B 拒", !rsB.Validate(tok(resA), now, true).OK, "")
	ck("B 的令牌被 A 拒", !rs.Validate(tok(resB), now, true).OK, "")

	// §4 alg / 验签
	noneAlg := MakeAccessToken(iss, resA, "u", "c", now, 300, "at+jwt", "none", nil)
	ck("alg=none 被拒", rs.Validate(noneAlg, now, true).Step == "alg", "")
	r = rs.Validate(tok(resA), now, false)
	ck("验签失败被拒", !r.OK && r.Step == "alg", r.Step)

	// §4 exp
	t := tok(resA)
	ck("exp 之前通过", rs.Validate(t, now+299, true).OK, "")
	ck("恰好 exp 即失效", rs.Validate(t, now+300, true).Step == "exp", "")
	rsLw := ResourceServer{ResourceID: resA, Issuer: iss, Leeway: 60}
	ck("余量内仍接受", rsLw.Validate(t, now+330, true).OK, "")
	ck("超出余量失效", rsLw.Validate(t, now+360, true).Step == "exp", "")

	// 加密协商
	rsEnc := ResourceServer{ResourceID: resA, Issuer: iss, RequireEncryption: true}
	ck("协商加密而未加密 → 拒", rsEnc.Validate(tok(resA), now, true).Step == "encryption", "")
	enc := tok(resA)
	enc.Encrypted = true
	ck("已加密则通过", rsEnc.Validate(enc, now, true).OK, "")

	// 可选声明
	extra := map[string]interface{}{"auth_time": now - 10, "acr": "bronze",
		"scope": "read write"}
	withExtra := MakeAccessToken(iss, resA, "u", "c", now, 300, "at+jwt", "RS256", extra)
	ck("带认证信息与 scope 通过", rs.Validate(withExtra, now, true).OK, "")
	eq("scope 被保留", withExtra.Claims["scope"], "read write")

	// 检查顺序
	eq("检查顺序共七步", len(CheckOrder), 7)
	eq("typ 是第一步", CheckOrder[0], "typ")
	eq("exp 是最后一步", CheckOrder[len(CheckOrder)-1], "exp")
	both := MakeAccessToken(iss, resB, "u", "c", now, 300, "JWT", "RS256", nil)
	eq("多错时先报 typ", rs.Validate(both, now, true).Step, "typ")

	fmt.Println("OK =", okCount)
	if len(failed) > 0 {
		fmt.Println("FAILED =", len(failed))
		for _, s := range failed {
			fmt.Println("  -", s)
		}
	} else {
		fmt.Println("ALL OK")
	}
}
