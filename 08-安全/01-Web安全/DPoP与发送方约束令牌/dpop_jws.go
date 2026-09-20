// Package main 实现 DPoP（RFC 9449）与互 TLS 证书绑定（RFC 8705）最小模型。
//
//
package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"sort"
	"strings"
)

// ---------------------------------------------------------------- base64url
func b64u(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }

func b64uDec(s string) ([]byte, bool) {
	b, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(s, "="))
	return b, err == nil
}

// ------------------------------------------------------------- 教科书 RSA-FDH
// KeyPair 是教学用 RSA 密钥对（非生产强度）。
type KeyPair struct {
	N, E, D *big.Int
}

// NewKeyPair 用给定种子确定性地生成密钥对。
func NewKeyPair(bits int, seed int64) *KeyPair {
	src := big.NewInt(seed)
	e := big.NewInt(65537)
	for i := 0; i < 10000; i++ {
		p, errP := randPrime(src, bits)
		q, errQ := randPrime(src, bits)
		if errP != nil || errQ != nil || p.Cmp(q) == 0 {
			continue
		}
		n := new(big.Int).Mul(p, q)
		p1 := new(big.Int).Sub(p, big.NewInt(1))
		q1 := new(big.Int).Sub(q, big.NewInt(1))
		phi := new(big.Int).Mul(p1, q1)
		if new(big.Int).Mod(phi, e).Sign() == 0 {
			continue
		}
		d := new(big.Int).ModInverse(e, phi)
		if d == nil {
			continue
		}
		return &KeyPair{N: n, E: e, D: d}
	}
	return nil
}

// randPrime 用简单 LCG 造候选数并做 Miller-Rabin，保证跨运行确定性。
func randPrime(seed *big.Int, bits int) (*big.Int, error) {
	x := new(big.Int).Set(seed)
	mod := new(big.Int).Lsh(big.NewInt(1), uint(bits))
	for i := 0; i < 200000; i++ {
		x.Mul(x, big.NewInt(6364136223846793005))
		x.Add(x, big.NewInt(1442695040888963407))
		cand := new(big.Int).Mod(x, mod)
		cand.SetBit(cand, bits-1, 1)
		cand.SetBit(cand, 0, 1)
		if cand.ProbablyPrime(24) {
			return cand, nil
		}
	}
	return nil, errNoPrime
}

var errNoPrime = &noPrimeError{}

type noPrimeError struct{}

func (e *noPrimeError) Error() string { return "no prime found" }

// SignDigestInt 返回 h^d mod n。
func (k *KeyPair) SignDigestInt(h *big.Int) *big.Int {
	return new(big.Int).Exp(h, k.D, k.N)
}

// Recover 返回 sig^e mod n。
func (k *KeyPair) Recover(sig *big.Int) *big.Int {
	return new(big.Int).Exp(sig, k.E, k.N)
}

// JWKPublic 返回 RFC 7517 的公钥表示（RSA 只需 e 与 n）。
func JWKPublic(k *KeyPair) map[string]string {
	return map[string]string{
		"kty": "RSA",
		"e":   b64u(k.E.Bytes()),
		"n":   b64u(k.N.Bytes()),
	}
}

// JWKThumbprint 实现 RFC 7638 §3.2：成员名字典序、无空白的 JSON 的
// SHA-256 再 base64url 无填充。
func JWKThumbprint(jwk map[string]string) string {
	keys := make([]string, 0, len(jwk))
	for k := range jwk {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var sb strings.Builder
	sb.WriteByte('{')
	for i, k := range keys {
		if i > 0 {
			sb.WriteByte(',')
		}
		sb.WriteByte('"')
		sb.WriteString(k)
		sb.WriteString(`":"`)
		sb.WriteString(jwk[k])
		sb.WriteByte('"')
	}
	sb.WriteByte('}')
	sum := sha256.Sum256([]byte(sb.String()))
	return b64u(sum[:])
}

// ----------------------------------------------------------------- JWS 编解码
// AsymAlgs 是 RFC 9449 §4.3(5) 可接受的非对称算法集合（节选）。
var AsymAlgs = map[string]bool{
	"RS256": true, "RS384": true, "RS512": true,
	"ES256": true, "ES384": true, "ES512": true,
	"PS256": true, "PS384": true, "PS512": true, "EdDSA": true,
}

// MakeProof 生成一个 DPoP proof JWT。leakD 用于演示 §4.3(7) 的禁止情形。
func MakeProof(k *KeyPair, jti, htm, htu string, iat int64, ath, nonce,
	typ, alg string, withJWK, leakD bool) string {
	header := map[string]string{"typ": typ, "alg": alg}
	if withJWK {
		jwk := JWKPublic(k)
		if leakD {
			jwk["d"] = b64u(k.D.Bytes())
		}
		h, _ := json.Marshal(jwk)
		header["jwk"] = string(h)
	}
	payload := map[string]interface{}{"jti": jti, "htm": htm, "htu": htu, "iat": iat}
	if ath != "" {
		payload["ath"] = ath
	}
	if nonce != "" {
		payload["nonce"] = nonce
	}
	hb, _ := json.Marshal(header)
	pb, _ := json.Marshal(payload)
	head := b64u(hb)
	body := b64u(pb)
	signingInput := head + "." + body
	sum := sha256.Sum256([]byte(signingInput))
	h := new(big.Int).SetBytes(sum[:])
	h.Mod(h, k.N)
	sig := k.SignDigestInt(h)
	return head + "." + body + "." + b64u(sig.Bytes())
}

// JWSVerify 验证 JWS，返回 (是否通过, 原因, header, payload)。
func JWSVerify(compact string) (bool, string, map[string]string, map[string]interface{}) {
	parts := strings.Split(compact, ".")
	if len(parts) != 3 {
		return false, "JWS 结构不合法", nil, nil
	}
	hb, ok1 := b64uDec(parts[0])
	pb, ok2 := b64uDec(parts[1])
	sb, ok3 := b64uDec(parts[2])
	if !ok1 || !ok2 || !ok3 {
		return false, "base64url 不可解码", nil, nil
	}
	var header map[string]string
	var payload map[string]interface{}
	if json.Unmarshal(hb, &header) != nil || json.Unmarshal(pb, &payload) != nil {
		return false, "JSON 不可解析", nil, nil
	}
	var jwk map[string]string
	if raw, has := header["jwk"]; has {
		if json.Unmarshal([]byte(raw), &jwk) != nil {
			return false, "jwk 不可解析", header, payload
		}
	}
	if jwk == nil || jwk["kty"] != "RSA" {
		return false, "jwk 缺失或不是 RSA 公钥", header, payload
	}
	nb, ok4 := b64uDec(jwk["n"])
	eb, ok5 := b64uDec(jwk["e"])
	if !ok4 || !ok5 {
		return false, "jwk/签名不可解码", header, payload
	}
	n := new(big.Int).SetBytes(nb)
	e := new(big.Int).SetBytes(eb)
	sig := new(big.Int).SetBytes(sb)
	if sig.Cmp(n) >= 0 {
		return false, "签名越界", header, payload
	}
	sum := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	want := new(big.Int).SetBytes(sum[:])
	want.Mod(want, n)
	if new(big.Int).Exp(sig, e, n).Cmp(want) != 0 {
		return false, "签名不验证", header, payload
	}
	return true, "ok", header, payload
}
