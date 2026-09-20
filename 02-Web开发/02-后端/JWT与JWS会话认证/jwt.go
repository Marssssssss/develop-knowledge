// Go 侧对照实现：RFC 7515 紧凑序列化 + HS256 + RFC 7519 注册声明校验。
//
// 官方口径：
//   RFC 7515 §7.1  BASE64URL(Protected Header) || '.' || BASE64URL(Payload) || '.'
//                  || BASE64URL(Signature)
//                  JWS Signing Input 是 ASCII("header.payload")，
//                  即「第二个句点之前、不含第二个句点」的那段
//   RFC 7515 A.1   {"typ":"JWT",\r\n "alg":"HS256"} 的签名
//                  dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk
//   RFC 7515 A.5   {"alg":"none"} → eyJhbGciOiJub25lIn0，签名是**空字节串**
//   RFC 7519 §4.1  exp：now MUST be before exp；nbf：now MUST be after or equal to nbf；
//                  aud：处理方若不能在 aud 中认出自己，MUST be rejected
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

// 附录 A.1 / A.5 的官方常量。
const (
	officialHeaderB64  = "eyJ0eXAiOiJKV1QiLA0KICJhbGciOiJIUzI1NiJ9"
	officialPayloadB64 = "eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6Ly9leGFtcGxlLmNvbS9pc19yb290Ijp0cnVlfQ"
	officialSigB64     = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
	officialJWKK       = "AyM1SysPpbyDfgZld3umj1qzKObwVMkoqQ-EstJQLr_T-1qS0gZH75aKtMN3Yj0iPS4hcgUuTwjAzZr1Z9CAow"
	noneHeaderB64      = "eyJhbGciOiJub25lIn0"
)

// ErrInvalid / ErrAlgNotAllowed / ErrExpired / ErrAudience 是各类拒绝原因。
var (
	ErrInvalid      = errors.New("invalid JWS")
	ErrAlgNotAllowed = errors.New("alg not allowed")
	ErrExpired      = errors.New("token expired")
	ErrNotYetValid  = errors.New("token not yet valid")
	ErrAudience     = errors.New("audience mismatch")
)

// B64URLEncode 是「无 padding 的 base64url」。
func B64URLEncode(raw []byte) string {
	return base64.RawURLEncoding.EncodeToString(raw)
}

// B64URLDecode 还原；标准库会自动处理 -_ 与缺失的 padding。
func B64URLDecode(s string) ([]byte, error) {
	return base64.RawURLEncoding.DecodeString(strings.TrimRight(s, "="))
}

// SigningInput 对应 JWS Signing Input。
func SigningInput(headerB64, payloadB64 string) []byte {
	return []byte(headerB64 + "." + payloadB64)
}

// SignHS256 计算 HS256 的签名段。
func SignHS256(headerB64, payloadB64 string, key []byte) string {
	mac := hmac.New(sha256.New, key)
	mac.Write(SigningInput(headerB64, payloadB64))
	return B64URLEncode(mac.Sum(nil))
}

// Serialize 拼出紧凑序列化。
func Serialize(headerB64, payloadB64, sigB64 string) string {
	return headerB64 + "." + payloadB64 + "." + sigB64
}

// ParseCompact 拆成三段。
func ParseCompact(token string) (string, string, string, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return "", "", "", ErrInvalid
	}
	return parts[0], parts[1], parts[2], nil
}

// Claims 是校验时关心的声明集合；其余字段忽略。
type Claims struct {
	Iss string      `json:"iss"`
	Sub string      `json:"sub"`
	Aud interface{} `json:"aud"` // 单值字符串或字符串数组
	Exp *float64    `json:"exp"`
	Nbf *float64    `json:"nbf"`
	Iat *float64    `json:"iat"`
	Jti string      `json:"jti"`
}

// VerifyHS256 校验签名与 alg 白名单，返回声明。
func VerifyHS256(token string, key []byte, allowedAlgs []string) (*Claims, error) {
	h, p, s, err := ParseCompact(token)
	if err != nil {
		return nil, err
	}
	rawHeader, err := B64URLDecode(h)
	if err != nil {
		return nil, ErrInvalid
	}
	var header struct {
		Alg string `json:"alg"`
		Typ string `json:"typ"`
	}
	if err := json.Unmarshal(rawHeader, &header); err != nil {
		return nil, ErrInvalid
	}
	allowed := false
	for _, a := range allowedAlgs {
		if a == header.Alg {
			allowed = true
			break
		}
	}
	if !allowed {
		return nil, ErrAlgNotAllowed
	}
	if header.Alg == "none" {
		if s != "" {
			return nil, ErrInvalid // alg=none 时签名必须是空字节串
		}
	} else {
		if !hmac.Equal([]byte(SignHS256(h, p, key)), []byte(s)) {
			return nil, ErrInvalid
		}
	}
	rawPayload, err := B64URLDecode(p)
	if err != nil {
		return nil, ErrInvalid
	}
	c := &Claims{}
	if err := json.Unmarshal(rawPayload, c); err != nil {
		return nil, ErrInvalid
	}
	return c, nil
}

// AudienceValues 把 aud 归一成字符串切片（单值与数组两种形态）。
func (c *Claims) AudienceValues() []string {
	switch v := c.Aud.(type) {
	case string:
		return []string{v}
	case []interface{}:
		out := make([]string, 0, len(v))
		for _, e := range v {
			if s, ok := e.(string); ok {
				out = append(out, s)
			}
		}
		return out
	}
	return nil
}

// Validate 按 RFC 7519 §4.1 校验时间类与受众类声明。
func (c *Claims) Validate(now float64, audience, issuer string, leeway float64) error {
	if c.Exp != nil && !(now < *c.Exp+leeway) {
		return ErrExpired
	}
	if c.Nbf != nil && !(now >= *c.Nbf-leeway) {
		return ErrNotYetValid
	}
	if c.Aud != nil {
		if audience == "" {
			return ErrAudience
		}
		found := false
		for _, a := range c.AudienceValues() {
			if a == audience {
				found = true
				break
			}
		}
		if !found {
			return ErrAudience
		}
	}
	if issuer != "" && c.Iss != issuer {
		return ErrAudience
	}
	return nil
}

func main() {
	key, err := B64URLDecode(officialJWKK)
	if err != nil {
		fmt.Println("bad key:", err)
		return
	}
	sig := SignHS256(officialHeaderB64, officialPayloadB64, key)
	fmt.Printf("signature matches RFC 7515 A.1: %v\n", sig == officialSigB64)

	token := Serialize(officialHeaderB64, officialPayloadB64, officialSigB64)
	claims, err := VerifyHS256(token, key, []string{"HS256"})
	fmt.Printf("verify official: err=%v iss=%s exp=%v\n", err, claims.Iss, claims.Exp)

	// alg=none 攻击：默认白名单必须拒绝
	unsecured := Serialize(noneHeaderB64, officialPayloadB64, "")
	if _, err := VerifyHS256(unsecured, key, []string{"HS256"}); err != nil {
		fmt.Printf("alg=none rejected: %v\n", err)
	}
	if _, err := VerifyHS256(unsecured, key, []string{"none"}); err == nil {
		fmt.Println("alg=none accepted when explicitly allowed (whitelist matters)")
	}

	// 声明校验：exp 是严格小于
	now := float64(1300819380)
	if err := claims.Validate(now, "", "", 0); err != nil {
		fmt.Printf("now == exp -> %v\n", err)
	}
	if err := claims.Validate(now-1, "", "", 0); err == nil {
		fmt.Println("now < exp -> accepted")
	}
}
