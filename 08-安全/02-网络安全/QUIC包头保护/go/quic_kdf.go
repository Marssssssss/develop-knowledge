// quic_kdf.go —— HKDF-SHA256 与 TLS 1.3 的 HKDF-Expand-Label（RFC 5869 / RFC 8446 §7.1）
//
// QUIC 的包保护密钥沿用 TLS 1.3 的流量密钥派生：AEAD key / IV / 头保护 key 三者
// 用不同标签（"quic key" / "quic iv" / "quic hp"）从同一个 secret 派生，实现密钥分离；
// 密钥更新只换 "quic ku"。Initial 级别的 secret 例外，它由固定盐 +
// 「客户端首个 Initial 包的 DCID」推出（见 initialSecrets）。
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
)

const hashLen = 32

// RFC 9001 §5.2 的固定盐
var initialSalt = mustHex("38762cf7f55934b34d179ae6a4c80cadccbb7f0a")

func mustHex(s string) []byte {
	b, err := hex.DecodeString(s)
	if err != nil {
		panic(err)
	}
	return b
}

func hkdfExtract(salt, ikm []byte) []byte {
	if len(salt) == 0 {
		salt = make([]byte, hashLen)
	}
	m := hmac.New(sha256.New, salt)
	m.Write(ikm)
	return m.Sum(nil)
}

// hkdfExpand：T(i) = HMAC(PRK, T(i-1) | info | i)
func hkdfExpand(prk, info []byte, length int) []byte {
	var out, prev []byte
	for i := byte(1); len(out) < length; i++ {
		m := hmac.New(sha256.New, prk)
		m.Write(prev)
		m.Write(info)
		m.Write([]byte{i})
		prev = m.Sum(nil)
		out = append(out, prev...)
	}
	return out[:length]
}

// expandLabel：HkdfLabel = uint16(length) | opaque label<7..255> | opaque context<0..255>
// 标签必须带 "tls13 " 前缀；QUIC 的所有用途 context 都为空串。
func expandLabel(secret []byte, label string, context []byte, length int) []byte {
	full := append([]byte("tls13 "), label...)
	info := []byte{byte(length >> 8), byte(length), byte(len(full))}
	info = append(info, full...)
	info = append(info, byte(len(context)))
	info = append(info, context...)
	return hkdfExpand(secret, info, length)
}

// initialSecrets：由客户端首个 Initial 包的 DCID 派生两个加密级别的 secret
func initialSecrets(dcid []byte) (client, server []byte) {
	prk := hkdfExtract(initialSalt, dcid)
	return expandLabel(prk, "client in", nil, hashLen),
		expandLabel(prk, "server in", nil, hashLen)
}

// packetKeys：一个 secret 派生 (AEAD key, IV, 头保护 key)；IV 至少 8 字节
func packetKeys(secret []byte, keyLen, ivLen, hpLen int) (key, iv, hp []byte) {
	return expandLabel(secret, "quic key", nil, keyLen),
		expandLabel(secret, "quic iv", nil, max(ivLen, 8)),
		expandLabel(secret, "quic hp", nil, hpLen)
}

func nextSecret(secret []byte) []byte {
	return expandLabel(secret, "quic ku", nil, hashLen)
}
