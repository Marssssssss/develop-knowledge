// JOSE 的 JWS / JWE 紧凑序列化（Go 标准库版，与 python/ 下的从零实现同题对照）。

package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
)

func b64u(raw []byte) string {
	return base64.RawURLEncoding.EncodeToString(raw)
}

func unb64u(s string) ([]byte, error) {
	return base64.RawURLEncoding.DecodeString(s)
}

func hs256(key, msg []byte) []byte {
	m := hmac.New(sha256.New, key)
	m.Write(msg)
	return m.Sum(nil)
}

// ------------------------------------------------------------------------ JWS

// JWSSign 紧凑 JWS：签名输入是 ASCII(BASE64URL(头) ‖ '.' ‖ BASE64URL(载荷))。
func JWSSign(protected map[string]interface{}, payload []byte, key []byte,
	allowed []string) (string, error) {
	alg, _ := protected["alg"].(string)
	if !contains(allowed, alg) {
		return "", fmt.Errorf("alg 不在允许集合内: %q", alg)
	}
	raw, err := json.Marshal(protected)
	if err != nil {
		return "", err
	}
	p, q := b64u(raw), b64u(payload)
	si := []byte(p + "." + q)
	var sig []byte
	switch alg {
	case "HS256":
		sig = hs256(key, si)
	case "none":
		sig = nil
	default:
		return "", fmt.Errorf("unsupported alg")
	}
	return p + "." + q + "." + b64u(sig), nil
}

// JWSVerify 的三条防线与 Python 版一致：alg 白名单、密钥自带 alg 比对、常量时间比较。
func JWSVerify(token string, key []byte, allowed []string, keyAlg string) ([]byte, error) {
	parts := splitDots(token)
	if len(parts) != 3 {
		return nil, errors.New("紧凑 JWS 必须是 3 段")
	}
	raw, err := unb64u(parts[0])
	if err != nil {
		return nil, err
	}
	var hdr map[string]interface{}
	if err := json.Unmarshal(raw, &hdr); err != nil {
		return nil, err
	}
	alg, _ := hdr["alg"].(string)
	if !contains(allowed, alg) {
		return nil, fmt.Errorf("alg 不在允许集合内: %q", alg)
	}
	if keyAlg != "" && keyAlg != alg {
		return nil, fmt.Errorf("密钥的 alg=%q 与头部的 alg=%q 不一致", keyAlg, alg)
	}
	si := []byte(parts[0] + "." + parts[1])
	if alg == "none" {
		if parts[2] != "" {
			return nil, errors.New("alg=none 时签名必须为空")
		}
		return unb64u(parts[1])
	}
	got, err := unb64u(parts[2])
	if err != nil {
		return nil, err
	}
	if !hmac.Equal(hs256(key, si), got) {
		return nil, errors.New("签名不匹配")
	}
	return unb64u(parts[1])
}

// ------------------------------------------------------------------------ JWE

// JWEEncrypt 紧凑 JWE：AAD 是 ASCII(BASE64URL(Protected))。
func JWEEncrypt(protected map[string]interface{}, plain, kek, cek, iv []byte) (string, error) {
	raw, _ := json.Marshal(protected)
	p := b64u(raw)
	aad := []byte(p)
	alg, _ := protected["alg"].(string)
	ek := []byte{}
	switch alg {
	case "dir":
		if len(cek) != 32 {
			return "", errors.New("dir needs a 256-bit CEK")
		}
	case "A128KW":
		var err error
		ek, err = AESKeyWrap(kek, cek)
		if err != nil {
			return "", err
		}
	default:
		return "", errors.New("unsupported alg")
	}
	ct, tag, err := A128CBCHS256Encrypt(cek, iv, aad, plain)
	if err != nil {
		return "", err
	}
	return p + "." + b64u(ek) + "." + b64u(iv) + "." + b64u(ct) + "." + b64u(tag), nil
}

func JWEDecrypt(token string, kek, cek []byte) ([]byte, error) {
	parts := splitDots(token)
	if len(parts) != 5 {
		return nil, errors.New("紧凑 JWE 必须是 5 段")
	}
	raw, _ := unb64u(parts[0])
	var hdr map[string]interface{}
	json.Unmarshal(raw, &hdr)
	alg, _ := hdr["alg"].(string)
	if alg == "A128KW" {
		var err error
		cek, err = AESKeyUnwrap(kek, mustB64(parts[1]))
		if err != nil {
			return nil, err
		}
	}
	iv, ct, tag := mustB64(parts[2]), mustB64(parts[3]), mustB64(parts[4])
	return A128CBCHS256Decrypt(cek, iv, []byte(parts[0]), ct, tag)
}

func mustB64(s string) []byte {
	b, err := unb64u(s)
	if err != nil {
		panic(err)
	}
	return b
}

func splitDots(s string) []string {
	out := []string{}
	cur := ""
	for _, c := range s {
		if c == '.' {
			out = append(out, cur)
			cur = ""
			continue
		}
		cur += string(c)
	}
	return append(out, cur)
}

func contains(xs []string, v string) bool {
	for _, x := range xs {
		if x == v {
			return true
		}
	}
	return false
}
