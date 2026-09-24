// Package main 实现 JWT 访问令牌 profile（RFC 9068）与受众校验最小模型。
//
// 依据 RFC 9068（https://www.rfc-editor.org/rfc/rfc9068.txt 35141 B 全文实读）：
//   - §2.1 令牌 MUST 签名且 MUST NOT 用 "none"；AS/RS MUST 支持 RS256；
//     typ MUST 是 at+jwt 或 application/at+jwt（推荐省略 application/ 前缀），
//     目的是防止 OIDC ID Token 被当访问令牌接受。
//   - §2.2 七个 REQUIRED 声明：iss / exp / aud / sub / client_id / iat / jti。
//   - §4 校验顺序：typ → 解密 → iss（精确相等）→ aud（含自身资源指示值）
//     → 验签 → exp；失败一律 invalid_token；exp MAY 给几分钟余量。
//   - §5 跨 JWT 混淆：AS MUST 用互不相同的 aud 区分同一签发者发往不同资源的令牌。
package main

import "fmt"

// ATJWTTypes 是 §4 接受的 typ 值。
var ATJWTTypes = []string{"at+jwt", "application/at+jwt"}

// RequiredClaims 是 §2.2 的七个 REQUIRED 声明。
var RequiredClaims = []string{"iss", "exp", "aud", "sub", "client_id", "iat", "jti"}

// CheckOrder 是可审计的检查顺序（本 demo 把 REQUIRED 声明检查插在 typ 之后）。
var CheckOrder = []string{"typ", "required_claims", "encryption", "iss", "aud", "alg", "exp"}

// Token 是一个 JWT（可以是访问令牌，也可以是 ID Token 之类）。
type Token struct {
	Header    map[string]interface{}
	Claims    map[string]interface{}
	Encrypted bool
}

// AudList 把 aud 统一成列表（RFC 7519 允许单值或数组）。
func (t Token) AudList() []string {
	aud, ok := t.Claims["aud"]
	if !ok || aud == nil {
		return nil
	}
	if s, ok := aud.(string); ok {
		return []string{s}
	}
	var out []string
	switch v := aud.(type) {
	case []string:
		out = append(out, v...)
	case []interface{}:
		for _, e := range v {
			if s, ok := e.(string); ok {
				out = append(out, s)
			}
		}
	}
	return out
}

// ResourceServer 是资源服务器；ResourceID 是它认为属于自己的资源指示值。
type ResourceServer struct {
	ResourceID         string
	Issuer             string
	Leeway             int
	RequireEncryption  bool
}

// Result 是校验结果。
type Result struct {
	OK    bool
	Err   string
	Step  string
}

// Validate 按 §4 的 CheckOrder 逐步校验。
func (rs ResourceServer) Validate(t Token, now int, signatureOK bool) Result {
	bad := func(step string) Result { return Result{false, "invalid_token", step} }
	for _, step := range CheckOrder {
		switch step {
		case "typ":
			typ, _ := t.Header["typ"].(string)
			if !contains(ATJWTTypes, typ) {
				return bad("typ")
			}
		case "required_claims":
			for _, c := range RequiredClaims {
				if _, ok := t.Claims[c]; !ok {
					return bad("required_claims")
				}
			}
		case "encryption":
			if rs.RequireEncryption && !t.Encrypted {
				return bad("encryption")
			}
		case "iss":
			iss, _ := t.Claims["iss"].(string)
			if iss != rs.Issuer { // MUST 精确相等
				return bad("iss")
			}
		case "aud":
			if !contains(t.AudList(), rs.ResourceID) {
				return bad("aud")
			}
		case "alg":
			if alg, _ := t.Header["alg"].(string); alg == "none" {
				return bad("alg")
			}
			if !signatureOK {
				return bad("alg")
			}
		case "exp":
			exp := toInt(t.Claims["exp"])
			if now >= exp+rs.Leeway {
				return bad("exp")
			}
		}
	}
	return Result{true, "", ""}
}

func contains(list []string, s string) bool {
	for _, e := range list {
		if e == s {
			return true
		}
	}
	return false
}

func toInt(v interface{}) int {
	switch n := v.(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		return int(n)
	}
	return 0
}

// MakeAccessToken 按 §2.1/§2.2 造一个合规的 JWT 访问令牌。
func MakeAccessToken(issuer string, audience interface{}, sub, clientID string,
	now, ttl int, typ, alg string, extra map[string]interface{}) Token {
	claims := map[string]interface{}{
		"iss": issuer, "exp": now + ttl, "aud": audience, "sub": sub,
		"client_id": clientID, "iat": now,
		"jti": fmt.Sprintf("jti-%d-%s", now, sub),
	}
	for k, v := range extra {
		claims[k] = v
	}
	return Token{Header: map[string]interface{}{"typ": typ, "alg": alg}, Claims: claims}
}
