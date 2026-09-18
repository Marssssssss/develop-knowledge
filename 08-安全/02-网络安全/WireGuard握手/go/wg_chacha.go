// ChaCha20 / Poly1305 / AEAD_CHACHA20_POLY1305 / XChaCha20-Poly1305 的纯标准库实现。
//
// Go 标准库没有 ChaCha20-Poly1305（x/crypto 属第三方，本仓库不引入），而 WireGuard
// 用它做握手字段加密与传输加密；cookie 机制还用到 XChaCha20 的 24 字节随机 nonce。
// 全部按 RFC 8439 §2 与 draft-irtf-cfrg-xchacha §2 实现，Poly1305 用 math/big 写
// 以求与规范公式一一对应（教学优先，不做位运算优化）。
package main

import (
	"crypto/subtle"
	"encoding/binary"
	"math/big"
	"math/bits"
)

var chachaSigma = [4]uint32{0x61707865, 0x3320646E, 0x79622D32, 0x6B206574}

// chachaQR 是 RFC 8439 §2.1 的四分之一轮：4 次模 2^32 加、4 次异或、4 次循环左移。
// 用 bits.RotateLeft32 而不是手写移位，避免把「先左移再右移」误当成循环移位。
func chachaQR(s *[16]uint32, a, b, c, d int) {
	s[a] += s[b]
	s[d] = bits.RotateLeft32(s[d]^s[a], 16)
	s[c] += s[d]
	s[b] = bits.RotateLeft32(s[b]^s[c], 12)
	s[a] += s[b]
	s[d] = bits.RotateLeft32(s[d]^s[a], 8)
	s[c] += s[d]
	s[b] = bits.RotateLeft32(s[b]^s[c], 7)
}

func chachaRounds(s *[16]uint32) {
	for i := 0; i < 10; i++ {
		chachaQR(s, 0, 4, 8, 12)
		chachaQR(s, 1, 5, 9, 13)
		chachaQR(s, 2, 6, 10, 14)
		chachaQR(s, 3, 7, 11, 15)
		chachaQR(s, 0, 5, 10, 15)
		chachaQR(s, 1, 6, 11, 12)
		chachaQR(s, 2, 7, 8, 13)
		chachaQR(s, 3, 4, 9, 14)
	}
}

// chacha20Block 生成一个 64 字节密钥流块：初态 = 常量(4) ‖ key(8) ‖ counter(1) ‖ nonce(3)。
func chacha20Block(key []byte, counter uint32, nonce []byte) []byte {
	var st [16]uint32
	copy(st[0:4], chachaSigma[:])
	for i := 0; i < 8; i++ {
		st[4+i] = binary.LittleEndian.Uint32(key[i*4:])
	}
	st[12] = counter
	st[13] = binary.LittleEndian.Uint32(nonce[0:])
	st[14] = binary.LittleEndian.Uint32(nonce[4:])
	st[15] = binary.LittleEndian.Uint32(nonce[8:])
	w := st
	chachaRounds(&w)
	out := make([]byte, 64)
	for i := 0; i < 16; i++ {
		binary.LittleEndian.PutUint32(out[i*4:], w[i]+st[i])
	}
	return out
}

func chacha20Xor(key []byte, counter uint32, nonce, data []byte) []byte {
	out := make([]byte, len(data))
	for off := 0; off < len(data); off += 64 {
		ks := chacha20Block(key, counter+uint32(off/64), nonce)
		end := off + 64
		if end > len(data) {
			end = len(data)
		}
		for i := off; i < end; i++ {
			out[i] = data[i] ^ ks[i-off]
		}
	}
	return out
}

// hchacha20 只做 20 轮、不做末轮加初态，输出 state 的第 0~3 与 12~15 个字。
func hchacha20(key, nonce16 []byte) []byte {
	var st [16]uint32
	copy(st[0:4], chachaSigma[:])
	for i := 0; i < 8; i++ {
		st[4+i] = binary.LittleEndian.Uint32(key[i*4:])
	}
	for i := 0; i < 4; i++ {
		st[12+i] = binary.LittleEndian.Uint32(nonce16[i*4:])
	}
	w := st
	chachaRounds(&w)
	out := make([]byte, 32)
	for i := 0; i < 4; i++ {
		binary.LittleEndian.PutUint32(out[i*4:], w[i])
		binary.LittleEndian.PutUint32(out[(4+i)*4:], w[12+i])
	}
	return out
}

var polyP = new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), 130), big.NewInt(5))

// poly1305MAC 是 RFC 8439 §2.5 的一次性认证器：r 先钳位，逐块累加后取低 128 位。
func poly1305MAC(key, msg []byte) []byte {
	rBytes := make([]byte, 16)
	copy(rBytes, key[:16])
	// 钳位：清掉 r 的 4 个特定比特（RFC 8439 §2.5 的 r &= 0x0ffffffc0ffffffc0ffffffc0fffffff）
	rBytes[3] &= 15
	rBytes[7] &= 15
	rBytes[11] &= 15
	rBytes[15] &= 15
	rBytes[4] &= 252
	rBytes[8] &= 252
	rBytes[12] &= 252
	r := new(big.Int).SetBytes(reverseBytes(rBytes))
	s := new(big.Int).SetBytes(reverseBytes(key[16:32]))

	acc := new(big.Int)
	for i := 0; i < len(msg); i += 16 {
		end := i + 16
		if end > len(msg) {
			end = len(msg)
		}
		blk := append([]byte{}, msg[i:end]...)
		blk = append(blk, 0x01) // 每块尾部补一个 1 字节的高位
		n := new(big.Int).SetBytes(reverseBytes(blk))
		acc.Add(acc, n)
		acc.Mul(acc, r)
		acc.Mod(acc, polyP)
	}
	acc.Add(acc, s)
	mask := new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), 128), big.NewInt(1))
	acc.And(acc, mask)
	out := make([]byte, 16)
	b := acc.Bytes() // big.Int 是大端，补足 16 字节后反转成小端
	for i := 0; i < len(b); i++ {
		out[i] = b[len(b)-1-i]
	}
	return out
}

func reverseBytes(in []byte) []byte {
	out := make([]byte, len(in))
	for i := range in {
		out[i] = in[len(in)-1-i]
	}
	return out
}

func pad16(b []byte) []byte {
	if len(b)%16 == 0 {
		return nil
	}
	return make([]byte, 16-len(b)%16)
}

// aeadSeal 返回密文 ‖ 16 字节 tag：密钥流派生用 counter=0，正文从 counter=1 起。
func aeadSeal(key, nonce, aad, plaintext []byte) []byte {
	otk := chacha20Block(key, 0, nonce)[:32]
	ct := chacha20Xor(key, 1, nonce, plaintext)
	mac := make([]byte, 0, len(aad)+len(ct)+32)
	mac = append(mac, aad...)
	mac = append(mac, pad16(aad)...)
	mac = append(mac, ct...)
	mac = append(mac, pad16(ct)...)
	var lens [16]byte
	binary.LittleEndian.PutUint64(lens[0:], uint64(len(aad)))
	binary.LittleEndian.PutUint64(lens[8:], uint64(len(ct)))
	mac = append(mac, lens[:]...)
	return append(ct, poly1305MAC(otk, mac)...)
}

// aeadOpen 认证失败时返回 ok=false（对应内核 chacha20poly1305_decrypt 返回 false）。
func aeadOpen(key, nonce, aad, sealed []byte) ([]byte, bool) {
	if len(sealed) < 16 {
		return nil, false
	}
	ct, tag := sealed[:len(sealed)-16], sealed[len(sealed)-16:]
	otk := chacha20Block(key, 0, nonce)[:32]
	mac := make([]byte, 0, len(aad)+len(ct)+32)
	mac = append(mac, aad...)
	mac = append(mac, pad16(aad)...)
	mac = append(mac, ct...)
	mac = append(mac, pad16(ct)...)
	var lens [16]byte
	binary.LittleEndian.PutUint64(lens[0:], uint64(len(aad)))
	binary.LittleEndian.PutUint64(lens[8:], uint64(len(ct)))
	mac = append(mac, lens[:]...)
	if subtle.ConstantTimeCompare(poly1305MAC(otk, mac), tag) != 1 {
		return nil, false
	}
	return chacha20Xor(key, 1, nonce, ct), true
}

// xchacha20poly1305Seal：子密钥 = HChaCha20(key, nonce[0:16])，
// 内层 nonce = 4 个零字节 ‖ nonce[16:24]（draft-irtf-cfrg-xchacha §2.3）。
func xchacha20poly1305Seal(key, nonce, aad, plaintext []byte) []byte {
	sub := hchacha20(key, nonce[:16])
	return aeadSeal(sub, append(make([]byte, 4), nonce[16:24]...), aad, plaintext)
}

func xchacha20poly1305Open(key, nonce, aad, sealed []byte) ([]byte, bool) {
	sub := hchacha20(key, nonce[:16])
	return aeadOpen(sub, append(make([]byte, 4), nonce[16:24]...), aad, sealed)
}
