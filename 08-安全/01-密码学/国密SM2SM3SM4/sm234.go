package main

import (
	"encoding/hex"
	"fmt"
	"math/big"
)

// ------------------------------- SM3 -------------------------------
var sm3IV = [8]uint32{0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
	0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E}

func rotl(x uint32, n uint) uint32 { return (x << n) | (x >> (32 - n)) }

func p0(x uint32) uint32 { return x ^ rotl(x, 9) ^ rotl(x, 17) }
func p1(x uint32) uint32 { return x ^ rotl(x, 15) ^ rotl(x, 23) }

func ff(j int, x, y, z uint32) uint32 {
	if j < 16 {
		return x ^ y ^ z
	}
	return (x & y) | ((x | y) & z)
}

func gg(j int, x, y, z uint32) uint32 {
	if j < 16 {
		return x ^ y ^ z
	}
	return z ^ (x & (y ^ z))
}

// T_j：前 16 轮基值 79cc4519，其后 7a879d8a，各自循环左移 j 位
func tj(j int) uint32 {
	base := uint32(0x7A879D8A)
	if j < 16 {
		base = 0x79CC4519
	}
	return rotl(base, uint(j%32))
}

func sm3(data []byte) []byte {
	h := sm3IV
	m := append(append([]byte{}, data...), 0x80)
	for len(m)%64 != 56 {
		m = append(m, 0)
	}
	ml := uint64(len(data)) * 8
	for i := 0; i < 8; i++ {
		m = append(m, byte(ml>>uint(8*(7-i))))
	}
	for off := 0; off < len(m); off += 64 {
		w := make([]uint32, 68)
		for i := 0; i < 16; i++ {
			w[i] = uint32(m[off+4*i])<<24 | uint32(m[off+4*i+1])<<16 |
				uint32(m[off+4*i+2])<<8 | uint32(m[off+4*i+3])
		}
		for j := 16; j < 68; j++ {
			w[j] = p1(w[j-16]^w[j-9]^rotl(w[j-3], 15)) ^ rotl(w[j-13], 7) ^ w[j-6]
		}
		a, b, c, d, e, f, g, hh := h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7]
		for j := 0; j < 64; j++ {
			wp := w[j] ^ w[j+4] // W'_j：SM3 与 SHA-256 的关键差异
			a12 := rotl(a, 12)
			ss1 := rotl(a12+e+tj(j), 7)
			ss2 := ss1 ^ a12
			tt1 := ff(j, a, b, c) + d + ss2 + wp
			tt2 := gg(j, e, f, g) + hh + ss1 + w[j]
			d, c, b, a = c, rotl(b, 9), a, tt1
			hh, g, f, e = g, rotl(f, 19), e, p0(tt2)
		}
		v := [8]uint32{a, b, c, d, e, f, g, hh}
		for i := range h {
			h[i] ^= v[i] // 前馈是 XOR，不是加法
		}
	}
	out := make([]byte, 32)
	for i := 0; i < 8; i++ {
		out[4*i], out[4*i+1], out[4*i+2], out[4*i+3] =
			byte(h[i]>>24), byte(h[i]>>16), byte(h[i]>>8), byte(h[i])
	}
	return out
}

// ------------------------------- SM4 -------------------------------
var sbox = [256]byte{
	0xd6, 0x90, 0xe9, 0xfe, 0xcc, 0xe1, 0x3d, 0xb7, 0x16, 0xb6, 0x14, 0xc2, 0x28, 0xfb, 0x2c, 0x05,
	0x2b, 0x67, 0x9a, 0x76, 0x2a, 0xbe, 0x04, 0xc3, 0xaa, 0x44, 0x13, 0x26, 0x49, 0x86, 0x06, 0x99,
	0x9c, 0x42, 0x50, 0xf4, 0x91, 0xef, 0x98, 0x7a, 0x33, 0x54, 0x0b, 0x43, 0xed, 0xcf, 0xac, 0x62,
	0xe4, 0xb3, 0x1c, 0xa9, 0xc9, 0x08, 0xe8, 0x95, 0x80, 0xdf, 0x94, 0xfa, 0x75, 0x8f, 0x3f, 0xa6,
	0x47, 0x07, 0xa7, 0xfc, 0xf3, 0x73, 0x17, 0xba, 0x83, 0x59, 0x3c, 0x19, 0xe6, 0x85, 0x4f, 0xa8,
	0x68, 0x6b, 0x81, 0xb2, 0x71, 0x64, 0xda, 0x8b, 0xf8, 0xeb, 0x0f, 0x4b, 0x70, 0x56, 0x9d, 0x35,
	0x1e, 0x24, 0x0e, 0x5e, 0x63, 0x58, 0xd1, 0xa2, 0x25, 0x22, 0x7c, 0x3b, 0x01, 0x21, 0x78, 0x87,
	0xd4, 0x00, 0x46, 0x57, 0x9f, 0xd3, 0x27, 0x52, 0x4c, 0x36, 0x02, 0xe7, 0xa0, 0xc4, 0xc8, 0x9e,
	0xea, 0xbf, 0x8a, 0xd2, 0x40, 0xc7, 0x38, 0xb5, 0xa3, 0xf7, 0xf2, 0xce, 0xf9, 0x61, 0x15, 0xa1,
	0xe0, 0xae, 0x5d, 0xa4, 0x9b, 0x34, 0x1a, 0x55, 0xad, 0x93, 0x32, 0x30, 0xf5, 0x8c, 0xb1, 0xe3,
	0x1d, 0xf6, 0xe2, 0x2e, 0x82, 0x66, 0xca, 0x60, 0xc0, 0x29, 0x23, 0xab, 0x0d, 0x53, 0x4e, 0x6f,
	0xd5, 0xdb, 0x37, 0x45, 0xde, 0xfd, 0x8e, 0x2f, 0x03, 0xff, 0x6a, 0x72, 0x6d, 0x6c, 0x5b, 0x51,
	0x8d, 0x1b, 0xaf, 0x92, 0xbb, 0xdd, 0xbc, 0x7f, 0x11, 0xd9, 0x5c, 0x41, 0x1f, 0x10, 0x5a, 0xd8,
	0x0a, 0xc1, 0x31, 0x88, 0xa5, 0xcd, 0x7b, 0xbd, 0x2d, 0x74, 0xd0, 0x12, 0xb8, 0xe5, 0xb4, 0xb0,
	0x89, 0x69, 0x97, 0x4a, 0x0c, 0x96, 0x77, 0x7e, 0x65, 0xb9, 0xf1, 0x09, 0xc5, 0x6e, 0xc6, 0x84,
	0x18, 0xf0, 0x7d, 0xec, 0x3a, 0xdc, 0x4d, 0x20, 0x79, 0xee, 0x5f, 0x3e, 0xd7, 0xcb, 0x39, 0x48}

var fk = [4]uint32{0xA3B1BAC6, 0x56AA3350, 0x677D9197, 0xB27022DC}
var ck = [32]uint32{0x00070E15, 0x1C232A31, 0x383F464D, 0x545B6269, 0x70777E85, 0x8C939AA1, 0xA8AFB6BD, 0xC4CBD2D9,
	0xE0E7EEF5, 0xFC030A11, 0x181F262D, 0x343B4249, 0x50575E65, 0x6C737A81, 0x888F969D, 0xA4ABB2B9,
	0xC0C7CED5, 0xDCE3EAF1, 0xF8FF060D, 0x141B2229, 0x30373E45, 0x4C535A61, 0x686F767D, 0x848B9299,
	0xA0A7AEB5, 0xBCC3CAD1, 0xD8DFE6ED, 0xF4FB0209, 0x10171E25, 0x2C333A41, 0x484F565D, 0x646B7279}

func sboxW(x uint32) uint32 {
	return uint32(sbox[x>>24])<<24 | uint32(sbox[(x>>16)&0xFF])<<16 |
		uint32(sbox[(x>>8)&0xFF])<<8 | uint32(sbox[x&0xFF])
}

func lRound(b uint32) uint32 { return b ^ rotl(b, 2) ^ rotl(b, 10) ^ rotl(b, 18) ^ rotl(b, 24) }
func tRound(x uint32) uint32 { return lRound(sboxW(x)) }

// L'(B) = B ⊕ (B<<<13) ⊕ (B<<<23)：只用于密钥扩展，与轮函数的 L 不同
func tKey(x uint32) uint32 {
	b := sboxW(x)
	return b ^ rotl(b, 13) ^ rotl(b, 23)
}

func sm4RoundKeys(mk [4]uint32) [32]uint32 {
	var k [36]uint32
	var rk [32]uint32
	for i := 0; i < 4; i++ {
		k[i] = mk[i] ^ fk[i]
	}
	for i := 0; i < 32; i++ {
		k[i+4] = k[i] ^ tKey(k[i+1]^k[i+2]^k[i+3]^ck[i])
		rk[i] = k[i+4]
	}
	return rk
}

func sm4Block(x [4]uint32, rk [32]uint32) [4]uint32 {
	for i := 0; i < 32; i++ {
		x[0] ^= tRound(x[1] ^ x[2] ^ x[3] ^ rk[i]) // 非平衡 Feistel：只更新 1/4
		x[0], x[1], x[2], x[3] = x[1], x[2], x[3], x[0]
	}
	return [4]uint32{x[3], x[2], x[1], x[0]} // 反序变换 R
}

func unpack4(in []byte) [4]uint32 {
	var x [4]uint32
	for i := 0; i < 4; i++ {
		x[i] = uint32(in[4*i])<<24 | uint32(in[4*i+1])<<16 | uint32(in[4*i+2])<<8 | uint32(in[4*i+3])
	}
	return x
}

func pack4(out [4]uint32) []byte {
	b := make([]byte, 16)
	for i := 0; i < 4; i++ {
		b[4*i], b[4*i+1], b[4*i+2], b[4*i+3] =
			byte(out[i]>>24), byte(out[i]>>16), byte(out[i]>>8), byte(out[i])
	}
	return b
}

func sm4EncryptBlock(mk [4]uint32, in []byte) []byte {
	return pack4(sm4Block(unpack4(in), sm4RoundKeys(mk)))
}

func sm4DecryptBlock(mk [4]uint32, in []byte) []byte {
	rk := sm4RoundKeys(mk)
	for i, j := 0, 31; i < j; i, j = i+1, j-1 {
		rk[i], rk[j] = rk[j], rk[i] // 解密 = 轮密钥反序，算法结构完全不变
	}
	return pack4(sm4Block(unpack4(in), rk))
}

func main() {
	ok := 0
	chk := func(cond bool, msg string) {
		if !cond {
			panic("断言失败: " + msg)
		}
		ok++
	}
	hx := hex.EncodeToString

	chk(hx(sm3([]byte("abc"))) ==
		"66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0", "SM3(abc)")
	chk(hx(sm3([]byte("abcdabcdabcdabcdabcdabcdabcdabcdabcdabcdabcdabcdabcdabcdabcdabcd"))) ==
		"debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732", "SM3 512-bit 向量")

	mk := [4]uint32{0x01234567, 0x89ABCDEF, 0xFEDCBA98, 0x76543210}
	rk := sm4RoundKeys(mk)
	chk(rk[0] == 0xF12186F9 && rk[31] == 0x9124A012, "SM4 轮密钥首/末向量")
	pt16, _ := hex.DecodeString("0123456789abcdeffedcba9876543210")
	ct := sm4EncryptBlock(mk, pt16)
	chk(hx(ct) == "681edf34d206965e86b3e94f536e4246", "SM4 加密向量")
	chk(hx(sm4DecryptBlock(mk, ct)) == hx(pt16), "SM4 解密还原")
	chk(hx(sm4DecryptBlock(mk, pt16)) != hx(ct), "解密 ≠ 加密（轮密钥反序）")

	g := &point{sm2GX, sm2GY}
	chk(ptMul(sm2N, g) == nil, "nG = 无穷远点")
	d, _ := new(big.Int).SetString("128B2FA8BD433C6C068C8D803DFF7979B89CA6C1C41E7E36C4B0E77C8D6E1A5", 16)
	pub := ptMul(d, g)
	// 点仍在曲线上：y² ≡ x³ + ax + b (mod p)
	lhs := new(big.Int).Mul(pub.y, pub.y)
	rhs := new(big.Int).Mul(pub.x, new(big.Int).Mul(pub.x, pub.x))
	rhs.Add(rhs, new(big.Int).Mul(sm2A, pub.x)).Add(rhs, sm2B)
	chk(new(big.Int).Mod(lhs, sm2P).Cmp(new(big.Int).Mod(rhs, sm2P)) == 0, "公钥在曲线上")

	k, _ := new(big.Int).SetString("6CB28D99385C175C94F94E934817663FC176D925DD72B727260DBAAE1FB2F96F", 16)
	id := []byte("1234567812345678")
	r, s := sm2Sign(d, []byte("message digest"), k, id)
	chk(r.Sign() > 0 && r.Cmp(sm2N) < 0 && s.Sign() > 0 && s.Cmp(sm2N) < 0, "r,s ∈ [1,n-1]")
	chk(sm2Verify(pub, []byte("message digest"), id, r, s), "SM2 签名自验通过")
	chk(!sm2Verify(pub, []byte("message digese"), id, r, s), "消息改一字节 ⇒ 失败")
	chk(!sm2Verify(pub, []byte("message digest"), []byte("ALICE123"), r, s), "ID 不一致 ⇒ 失败")

	// k 重用：r 之差 = 消息摘要之差，进而可反解私钥
	m1, m2 := []byte("order-001"), []byte("order-002")
	r1, s1 := sm2Sign(d, m1, k, id)
	r2, s2 := sm2Sign(d, m2, k, id)
	z := sm2Z(id, pub)
	e1 := new(big.Int).SetBytes(sm3(append(z, m1...)))
	e2 := new(big.Int).SetBytes(sm3(append(z, m2...)))
	chk(new(big.Int).Sub(r2, r1).Mod(new(big.Int).Sub(r2, r1), sm2N).Cmp(
		new(big.Int).Sub(e2, e1).Mod(new(big.Int).Sub(e2, e1), sm2N)) == 0,
		"k 重用时 r 之差 = 摘要之差")
	den := new(big.Int).Sub(r2, r1)
	den.Sub(den, s1).Add(den, s2).Mod(den, sm2N)
	rec := new(big.Int).Sub(s1, s2)
	rec.Mul(rec, new(big.Int).ModInverse(den, sm2N)).Mod(rec, sm2N)
	chk(rec.Cmp(d) == 0, "k 重用反解私钥成功")

	fmt.Printf("国密 SM2/SM3/SM4 Go 自检通过：%d 项断言\n", ok)
}
