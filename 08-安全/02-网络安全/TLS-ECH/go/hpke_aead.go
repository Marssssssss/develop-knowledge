// hpke_aead.go —— ChaCha20-Poly1305（RFC 8439）的纯标准库实现
//
// 为什么要自己写：Go 标准库只有 AES-GCM，ChaCha20-Poly1305 在 golang.org/x/crypto 里，
// 而本仓库约定不引入第三方依赖。Python 版同样自己实现了这一层，两边可逐字节对拍。
//
// 结构照 RFC 8439 §2.6/§2.8：
//
//	counter=0 的密钥流前 32 字节做 Poly1305 的一次性密钥（otk）；
//	明文从 counter=1 开始异或；
//	MAC 输入 = aad | pad16 | ct | pad16 | len(aad) | len(ct)，两个长度都是**小端** u64。
//
// Poly1305 用 math/big 写（模 2^130-5、r 的 22 个高位清零），比手写 26 位分肢少一大类
// 进位 bug；本 demo 的报文只有几百字节，性能不是约束。
package main

import (
	"crypto/subtle"
	"encoding/binary"
	"errors"
	"math/big"
	"math/bits"
)

const (
	aeadKeyLen   = 32
	aeadNonceLen = 12
	aeadTagLen   = 16
)

func mustBigHex(s string) *big.Int {
	v, ok := new(big.Int).SetString(s, 16)
	if !ok {
		panic("poly1305: 掩码解析失败")
	}
	return v
}

var (
	polyMask = mustBigHex("0ffffffc0ffffffc0ffffffc0fffffff")
	polyMod  = new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), 130), big.NewInt(5))
	polyTwoN = new(big.Int).Lsh(big.NewInt(1), 128)
)

// reverseBytes —— Poly1305 的整数是**小端**，big.Int 走大端，两头转一次最省事
func reverseBytes(b []byte) []byte {
	out := make([]byte, len(b))
	for i := range b {
		out[len(b)-1-i] = b[i]
	}
	return out
}

func chachaQuarter(s *[16]uint32, a, b, c, d int) {
	s[a] += s[b]
	s[d] = bits.RotateLeft32(s[d]^s[a], 16)
	s[c] += s[d]
	s[b] = bits.RotateLeft32(s[b]^s[c], 12)
	s[a] += s[b]
	s[d] = bits.RotateLeft32(s[d]^s[a], 8)
	s[c] += s[d]
	s[b] = bits.RotateLeft32(s[b]^s[c], 7)
}

// chachaBlock —— RFC 8439 §2.3.2：20 轮（10 次双轮）置换后加回初始状态
func chachaBlock(key, nonce []byte, counter uint32) [64]byte {
	var s [16]uint32
	s[0], s[1], s[2], s[3] = 0x61707865, 0x3320646e, 0x79622d32, 0x6b206574
	for i := 0; i < 8; i++ {
		s[4+i] = binary.LittleEndian.Uint32(key[4*i:])
	}
	s[12] = counter
	for i := 0; i < 3; i++ {
		s[13+i] = binary.LittleEndian.Uint32(nonce[4*i:])
	}
	w := s
	for i := 0; i < 10; i++ {
		chachaQuarter(&w, 0, 4, 8, 12)
		chachaQuarter(&w, 1, 5, 9, 13)
		chachaQuarter(&w, 2, 6, 10, 14)
		chachaQuarter(&w, 3, 7, 11, 15)
		chachaQuarter(&w, 0, 5, 10, 15)
		chachaQuarter(&w, 1, 6, 11, 12)
		chachaQuarter(&w, 2, 7, 8, 13)
		chachaQuarter(&w, 3, 4, 9, 14)
	}
	var out [64]byte
	for i := 0; i < 16; i++ {
		binary.LittleEndian.PutUint32(out[4*i:], w[i]+s[i])
	}
	return out
}

func chacha20XOR(key, nonce []byte, counter uint32, data []byte) []byte {
	out := make([]byte, len(data))
	for off := 0; off < len(data); off += 64 {
		ks := chachaBlock(key, nonce, counter+uint32(off/64))
		n := len(data) - off
		if n > 64 {
			n = 64
		}
		for i := 0; i < n; i++ {
			out[off+i] = data[off+i] ^ ks[i]
		}
	}
	return out
}

func poly1305MAC(key, msg []byte) [16]byte {
	r := new(big.Int).SetBytes(reverseBytes(key[:16]))
	r.And(r, polyMask)
	s := new(big.Int).SetBytes(reverseBytes(key[16:32]))
	acc := new(big.Int)
	for i := 0; i < len(msg); i += 16 {
		n := len(msg) - i
		if n > 16 {
			n = 16
		}
		// 每块隐含补一个 2^(8n) 的最高位。**大端表示下这一位落在最前面的字节**：
		// 放到末尾只会给这一块加 1，而且 seal 与 open 同时错、往返测试照样过 ——
		// 只有 RFC 9180 附录 A.2 的官方向量能把它揪出来。
		blk := make([]byte, n+1)
		blk[0] = 1
		copy(blk[1:], reverseBytes(msg[i:i+n]))
		acc.Add(acc, new(big.Int).SetBytes(blk))
		acc.Mul(acc, r)
		acc.Mod(acc, polyMod)
	}
	acc.Add(acc, s)
	acc.Mod(acc, polyTwoN) // tag = (acc + s) mod 2^128
	raw := make([]byte, 16)
	acc.FillBytes(raw)
	var tag [16]byte
	copy(tag[:], reverseBytes(raw))
	return tag
}

func aeadPad16(b []byte) []byte {
	out := make([]byte, len(b))
	copy(out, b)
	if n := (16 - len(b)%16) % 16; n > 0 {
		out = append(out, make([]byte, n)...)
	}
	return out
}

func aeadMACData(aad, ct []byte) []byte {
	buf := aeadPad16(aad)
	buf = append(buf, aeadPad16(ct)...)
	var lens [16]byte
	binary.LittleEndian.PutUint64(lens[0:8], uint64(len(aad)))
	binary.LittleEndian.PutUint64(lens[8:16], uint64(len(ct)))
	return append(buf, lens[:]...)
}

// aeadSeal —— 返回 ciphertext || tag（RFC 8439 §2.8）
func aeadSeal(key, nonce, aad, plaintext []byte) ([]byte, error) {
	if len(key) != aeadKeyLen || len(nonce) != aeadNonceLen {
		return nil, errors.New("chacha20poly1305: 密钥或 nonce 长度不对")
	}
	otk := chachaBlock(key, nonce, 0)
	ct := chacha20XOR(key, nonce, 1, plaintext)
	tag := poly1305MAC(otk[:32], aeadMACData(aad, ct))
	return append(ct, tag[:]...), nil
}

// aeadOpen —— 校验失败返回 errHpkeAuth，在 ECH 里这条就是「ClientHelloOuter 被改过」
func aeadOpen(key, nonce, aad, sealed []byte) ([]byte, error) {
	if len(key) != aeadKeyLen || len(nonce) != aeadNonceLen {
		return nil, errors.New("chacha20poly1305: 密钥或 nonce 长度不对")
	}
	if len(sealed) < aeadTagLen {
		return nil, errHpkeAuth
	}
	ct := sealed[:len(sealed)-aeadTagLen]
	tag := sealed[len(sealed)-aeadTagLen:]
	otk := chachaBlock(key, nonce, 0)
	want := poly1305MAC(otk[:32], aeadMACData(aad, ct))
	if subtle.ConstantTimeCompare(want[:], tag) != 1 {
		return nil, errHpkeAuth
	}
	return chacha20XOR(key, nonce, 1, ct), nil
}
