// quic_chacha.go —— QUIC 包保护所需的对称原语（ChaCha20 / Poly1305 / AEAD），纯标准库实现
//
// 为什么手写而不用现成库：Go 标准库里**没有** ChaCha20-Poly1305（那在
// golang.org/x/crypto），本仓库的约定是不引入任何第三方依赖，所以按 RFC 8439
// 自己实现。多精度运算用 math/big，避免手写 130 位模乘引入静默错误。
package main

import (
	"encoding/binary"
	"math/big"
	"math/bits"
)

var chachaSigma = [4]uint32{0x61707865, 0x3320646e, 0x79622d32, 0x6b206574}

// polyMod = 2^130 - 5（RFC 8439 §2.5 的模数）
var polyMod = new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), 130), big.NewInt(5))

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

// chachaBlock 产生一个 64 字节密钥流块。ChaCha20 的 IV 是 counter(4 字节小端)||nonce(12)。
func chachaBlock(key []byte, counter uint32, nonce []byte) []byte {
	var st [16]uint32
	copy(st[:4], chachaSigma[:])
	for i := 0; i < 8; i++ {
		st[4+i] = binary.LittleEndian.Uint32(key[i*4:])
	}
	st[12] = counter
	for i := 0; i < 3; i++ {
		st[13+i] = binary.LittleEndian.Uint32(nonce[i*4:])
	}
	w := st
	for i := 0; i < 10; i++ {
		chachaQR(&w, 0, 4, 8, 12)
		chachaQR(&w, 1, 5, 9, 13)
		chachaQR(&w, 2, 6, 10, 14)
		chachaQR(&w, 3, 7, 11, 15)
		chachaQR(&w, 0, 5, 10, 15)
		chachaQR(&w, 1, 6, 11, 12)
		chachaQR(&w, 2, 7, 8, 13)
		chachaQR(&w, 3, 4, 9, 14)
	}
	out := make([]byte, 64)
	for i := 0; i < 16; i++ {
		binary.LittleEndian.PutUint32(out[i*4:], w[i]+st[i])
	}
	return out
}

func chacha20XOR(key []byte, counter uint32, nonce, data []byte) []byte {
	out := make([]byte, len(data))
	for off := 0; off < len(data); off += 64 {
		ks := chachaBlock(key, counter+uint32(off/64), nonce)
		end := min(off+64, len(data))
		for i := off; i < end; i++ {
			out[i] = data[i] ^ ks[i-off]
		}
	}
	return out
}

func reverseBytes(b []byte) []byte {
	out := make([]byte, len(b))
	for i := range b {
		out[len(b)-1-i] = b[i]
	}
	return out
}

// poly1305MAC：key 为 32 字节（r||s），r 需按 RFC 8439 做 clamp。
func poly1305MAC(key, msg []byte) []byte {
	clamp, _ := new(big.Int).SetString("0ffffffc0ffffffc0ffffffc0fffffff", 16)
	r := new(big.Int).And(new(big.Int).SetBytes(reverseBytes(key[:16])), clamp)
	s := new(big.Int).SetBytes(reverseBytes(key[16:32]))
	acc := new(big.Int)
	for i := 0; i < len(msg); i += 16 {
		blk := msg[i:min(i+16, len(msg))]
		n := new(big.Int).SetBytes(reverseBytes(append(append([]byte{}, blk...), 1)))
		acc.Add(acc, n)
		acc.Mul(acc, r)
		acc.Mod(acc, polyMod)
	}
	acc.Add(acc, s)
	acc.And(acc, new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), 128), big.NewInt(1)))
	buf := acc.FillBytes(make([]byte, 16))
	return reverseBytes(buf)
}

func pad16(b []byte) []byte {
	if len(b)%16 == 0 {
		return nil
	}
	return make([]byte, 16-len(b)%16)
}

// aeadSeal 输出 密文 || 16 字节标签（ChaCha20-Poly1305，RFC 8439 §2.8）
func aeadSeal(key, nonce, aad, plaintext []byte) []byte {
	otk := chachaBlock(key, 0, nonce)[:32]
	ct := chacha20XOR(key, 1, nonce, plaintext)
	mac := aeadMACInput(aad, ct)
	tag := poly1305MAC(otk, mac)
	return append(ct, tag...)
}

func aeadMACInput(aad, ct []byte) []byte {
	buf := append([]byte{}, aad...)
	buf = append(buf, pad16(aad)...)
	buf = append(buf, ct...)
	buf = append(buf, pad16(ct)...)
	var lens [16]byte
	binary.LittleEndian.PutUint64(lens[0:], uint64(len(aad)))
	binary.LittleEndian.PutUint64(lens[8:], uint64(len(ct)))
	return append(buf, lens[:]...)
}

// aeadOpen 认证失败返回 false 且不返回明文（等价于 Python 版抛异常）
func aeadOpen(key, nonce, aad, sealed []byte) ([]byte, bool) {
	if len(sealed) < 16 {
		return nil, false
	}
	ct := sealed[:len(sealed)-16]
	tag := sealed[len(sealed)-16:]
	otk := chachaBlock(key, 0, nonce)[:32]
	want := poly1305MAC(otk, aeadMACInput(aad, ct))
	for i := range want {
		if want[i] != tag[i] {
			return nil, false
		}
	}
	return chacha20XOR(key, 1, nonce, ct), true
}
