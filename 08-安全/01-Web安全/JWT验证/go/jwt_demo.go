// JWT (RFC 7519) HS256 验证演示 — 纯 stdlib 实现
// 演示:base64url 编解码 / HS256 签发验签 / exp 校验 / alg confusion 防御
//       RFC 7519 §A.1 向量 / 篡改检测
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

// ─────────────── 错误定义 ───────────────

var (
	ErrInvalidSignature    = errors.New("invalid signature")
	ErrExpiredToken        = errors.New("token expired")
	ErrInvalidAlgorithm    = errors.New("invalid algorithm (alg whitelist)")
	ErrMalformedToken      = errors.New("malformed token (must have 3 parts)")
)

// ─────────────── base64url ───────────────

func b64urlEncode(data []byte) string {
	return strings.TrimRight(base64.URLEncoding.EncodeToString(data), "=")
}

func b64urlDecode(s string) ([]byte, error) {
	padding := (4 - len(s)%4) % 4
	return base64.URLEncoding.DecodeString(s + strings.Repeat("=", padding))
}

// ─────────────── HS256 签发 ───────────────

func jwtEncodeHS256(header, payload map[string]any, secret []byte) (string, error) {
	alg, _ := header["alg"].(string)
	if alg != "HS256" {
		return "", ErrInvalidAlgorithm
	}
	hJSON, _ := json.Marshal(header)
	pJSON, _ := json.Marshal(payload)
	hB64 := b64urlEncode(hJSON)
	pB64 := b64urlEncode(pJSON)
	signingInput := hB64 + "." + pB64

	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(signingInput))
	sigB64 := b64urlEncode(mac.Sum(nil))
	return signingInput + "." + sigB64, nil
}

// ─────────────── HS256 验签 ───────────────

type Claims = map[string]any

func jwtDecodeHS256(token string, secret []byte, algorithms []string, now time.Time) (Claims, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return nil, ErrMalformedToken
	}
	hB64, pB64, sigB64 := parts[0], parts[1], parts[2]
	if sigB64 == "" {
		return nil, ErrInvalidSignature
	}

	// 1. 解 header,校验 alg(★ RFC 8725 BCP:白名单,不按 header 选算法)
	hJSON, err := b64urlDecode(hB64)
	if err != nil {
		return nil, fmt.Errorf("decode header: %w", err)
	}
	var header map[string]any
	if err := json.Unmarshal(hJSON, &header); err != nil {
		return nil, fmt.Errorf("parse header: %w", err)
	}
	alg, _ := header["alg"].(string)
	allowed := false
	for _, a := range algorithms {
		if a == alg {
			allowed = true
			break
		}
	}
	if !allowed {
		return nil, fmt.Errorf("%w: alg=%q not in allowed %v", ErrInvalidAlgorithm, alg, algorithms)
	}

	// 2. 重算签名
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(hB64 + "." + pB64))
	expectedSig := mac.Sum(nil)

	actualSig, err := b64urlDecode(sigB64)
	if err != nil {
		return nil, fmt.Errorf("decode sig: %w", err)
	}
	// ★ hmac.Equal 防时序攻击
	if !hmac.Equal(expectedSig, actualSig) {
		return nil, ErrInvalidSignature
	}

	// 3. 解 payload + 时间校验
	pJSON, err := b64urlDecode(pB64)
	if err != nil {
		return nil, fmt.Errorf("decode payload: %w", err)
	}
	var payload Claims
	if err := json.Unmarshal(pJSON, &payload); err != nil {
		return nil, fmt.Errorf("parse payload: %w", err)
	}
	if exp, ok := payload["exp"].(float64); ok {
		if now.Unix() >= int64(exp) {
			return nil, fmt.Errorf("%w: exp=%v now=%v", ErrExpiredToken, int64(exp), now.Unix())
		}
	}
	if nbf, ok := payload["nbf"].(float64); ok {
		if now.Unix() < int64(nbf) {
			return nil, fmt.Errorf("%w: not before nbf=%v", ErrExpiredToken, int64(nbf))
		}
	}
	return payload, nil
}

// ─────────────── Demo 1: base64url ───────────────
func demoBase64URL() {
	fmt.Println(strings.Repeat("─", 65))
	fmt.Println("[Demo 1] base64url 编解码(RFC 7515 §2)")
	fmt.Println(strings.Repeat("─", 65))
	raw := []byte{0xfb, 0xff, 0xfe, 0x00, 0x01, 0x80, 0xab, 0xcd}
	enc := b64urlEncode(raw)
	dec, _ := b64urlDecode(enc)
	fmt.Printf("  原始(hex):     %x\n", raw)
	fmt.Printf("  base64url:     %s\n", enc)
	fmt.Printf("  标准 base64:   %s  ← 含 +/=\n", base64.StdEncoding.EncodeToString(raw))
	fmt.Printf("  解码回(hex):   %x\n", dec)
	fmt.Println("  → -_ 替代 +/,省略 = padding(URL/文件名安全)")
	fmt.Println()
}

var secret = []byte("32-byte-long-shared-secret-XYZabc123456")

func demoSignVerify() {
	fmt.Println(strings.Repeat("─", 65))
	fmt.Println("[Demo 2] HS256 签发 JWT")
	fmt.Println(strings.Repeat("─", 65))

	header := map[string]any{"typ": "JWT", "alg": "HS256"}
	payload := map[string]any{
		"iss": "demo-server",
		"sub": "alice",
		"aud": "demo-client",
		"exp": float64(time.Now().Add(1 * time.Hour).Unix()),
		"iat": float64(time.Now().Unix()),
	}
	token, _ := jwtEncodeHS256(header, payload, secret)
	fmt.Printf("  header:  %v\n", header)
	fmt.Printf("  payload: %v\n", payload)
	fmt.Printf("  token:   %s\n", token)
	fmt.Printf("  长度:    %d chars\n", len(token))
	fmt.Println()

	fmt.Println(strings.Repeat("─", 65))
	fmt.Println("[Demo 3] 验签 + exp 校验")
	fmt.Println(strings.Repeat("─", 65))

	// 合法
	claims, err := jwtDecodeHS256(token, secret, []string{"HS256"}, time.Now())
	if err == nil {
		fmt.Printf("  ✅ 合法 token: sub=%v, aud=%v\n", claims["sub"], claims["aud"])
	} else {
		fmt.Printf("  ❌ %v\n", err)
	}

	// 过期 token
	expiredPayload := map[string]any{
		"iss": "demo-server", "sub": "alice", "exp": float64(time.Now().Add(-100 * time.Second).Unix()),
	}
	expiredToken, _ := jwtEncodeHS256(header, expiredPayload, secret)
	_, err = jwtDecodeHS256(expiredToken, secret, []string{"HS256"}, time.Now())
	fmt.Printf("  🛑 过期 token: %v\n", err)
	fmt.Println()
}

// ─────────────── Demo 4: Algorithm Confusion ───────────────

func demoAlgConfusion() {
	fmt.Println(strings.Repeat("─", 65))
	fmt.Println("[Demo 4] ⚠️ Algorithm Confusion 攻击演示 + 防御")
	fmt.Println(strings.Repeat("─", 65))
	fmt.Println("  攻击场景:服务端用 RSA 公钥验签,但库按 header.alg 选算法")
	fmt.Println("  攻击者改 header.alg=HS256,用公钥当 HMAC secret 伪造签名")
	fmt.Println()

	// 模拟"RSA 公钥"
	fakePublicKey := []byte(`-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...
-----END PUBLIC KEY-----`)

	// 攻击者构造 token
	attackerToken, _ := jwtEncodeHS256(
		map[string]any{"typ": "JWT", "alg": "HS256"},
		map[string]any{"sub": "admin", "exp": float64(time.Now().Add(time.Hour).Unix())},
		fakePublicKey,
	)
	fmt.Printf("  攻击 token: %s...\n", attackerToken[:80])

	// ❌ 不安全:服务端按 header 选算法 + 误用公钥当 HS256 secret
	fmt.Println("  [不安全] 服务端误用公钥当 HS256 secret:")
	_, err := jwtDecodeHS256(attackerToken, fakePublicKey, []string{"HS256"}, time.Now())
	if err == nil {
		fmt.Println("    ⚠️ 攻击成功!")
	} else {
		fmt.Printf("    %s: %v\n", "阻断", err)
	}

	// ✅ 安全:服务端用真实共享 secret
	fmt.Println("  [安全]   服务端用真实共享 secret 验签:")
	_, err = jwtDecodeHS256(attackerToken, secret, []string{"HS256"}, time.Now())
	if errors.Is(err, ErrInvalidSignature) {
		fmt.Printf("    🛑 攻击被阻断: %v\n", err)
	}
	fmt.Println()
	fmt.Println("  RFC 8725 §3.1 BCP:服务端不按 header 选算法;按业务决定 alg 并检查 alg↔key 类型匹配")
	fmt.Println()
}

func demoRFC7519AppendixA() {
	fmt.Println(strings.Repeat("─", 65))
	fmt.Println("[Demo 5] RFC 7519 §A.1 HS256 示例向量验证")
	fmt.Println(strings.Repeat("─", 65))

	headerB64 := "eyJ0eXAiOiJKV1QiLA0KICJhbGciOiJIUzI1NiJ9"
	payloadB64 := "eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6Ly9leGFtcGxlLmNvbS9pc19yb290Ijp0cnVlfQ"
	expectedSigB64 := "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
	rfcSecret := []byte{
		3, 35, 53, 75, 43, 15, 165, 188, 131, 126, 6, 101, 119, 123, 166,
		143, 90, 179, 40, 230, 240, 84, 201, 40, 169, 15, 132, 178, 210, 80, 46,
		191, 211, 251, 90, 146, 210, 6, 71, 239, 150, 138, 180, 195, 119,
	}
	hJSON, _ := b64urlDecode(headerB64)
	pJSON, _ := b64urlDecode(payloadB64)
	hCRLF := b64urlEncode([]byte(strings.ReplaceAll(string(hJSON), "\n", "\r\n")))
	pCRLF := b64urlEncode([]byte(strings.ReplaceAll(string(pJSON), "\n", "\r\n")))

	mac := hmac.New(sha256.New, rfcSecret)
	mac.Write([]byte(hCRLF + "." + pCRLF))
	computed := b64urlEncode(mac.Sum(nil))

	fmt.Printf("  计算: %s\n  RFC:  %s\n  匹配: %s\n",
		computed, expectedSigB64,
		map[bool]string{true: "✅", false: "❌"}[computed == expectedSigB64])
	fmt.Println()
}

func demoTampering() {
	fmt.Println(strings.Repeat("─", 65))
	fmt.Println("[Demo 6] 篡改检测(改 payload / signature / secret / alg=none)")
	fmt.Println(strings.Repeat("─", 65))

	token, _ := jwtEncodeHS256(
		map[string]any{"typ": "JWT", "alg": "HS256"},
		map[string]any{"sub": "alice", "role": "user", "exp": float64(time.Now().Add(time.Hour).Unix())},
		secret,
	)
	fmt.Printf("  原始 token: %s\n\n", token)

	parts := strings.Split(token, ".")
	h, p, s := parts[0], parts[1], parts[2]

	tampered := h + "." + p[:len(p)-2] + "XX." + s
	fmt.Printf("  [1] 篡改 payload: %s\n", tampered)
	if _, err := jwtDecodeHS256(tampered, secret, []string{"HS256"}, time.Now()); err != nil {
		fmt.Printf("      🛑 %v\n", err)
	}

	tampered = h + "." + p + "." + s[:len(s)-2] + "XX"
	fmt.Printf("  [2] 篡改 sig:    %s\n", tampered)
	if _, err := jwtDecodeHS256(tampered, secret, []string{"HS256"}, time.Now()); err != nil {
		fmt.Printf("      🛑 %v\n", err)
	}

	tampered = h + "." + p + "."   // alg:none 攻击
	fmt.Printf("  [3] alg=none:   %s\n", tampered)
	if _, err := jwtDecodeHS256(tampered, secret, []string{"HS256"}, time.Now()); err != nil {
		fmt.Printf("      🛑 %v\n", err)
	}

	fakeSecret := []byte("attacker-guessed-secret-XXXXXXXX")
	fmt.Println("  [4] 错 secret:    " + "未改变 token")
	if _, err := jwtDecodeHS256(token, fakeSecret, []string{"HS256"}, time.Now()); err != nil {
		fmt.Printf("      🛑 %v\n", err)
	}
	fmt.Println()
}

func main() {
	fmt.Println(strings.Repeat("=", 65))
	fmt.Println("JWT 验证 (RFC 7519) — HS256 完整实现 + 攻击演示")
	fmt.Println(strings.Repeat("=", 65))
	fmt.Println()

	demoBase64URL()
	demoSignVerify()
	demoAlgConfusion()
	demoRFC7519AppendixA()
	demoTampering()

	fmt.Println(strings.Repeat("─", 65))
	fmt.Println("编译运行: cd JWT验证/go && go run jwt_demo.go")
	fmt.Println(strings.Repeat("─", 65))

