// blake2s 的纯标准库实现（RFC 7693），支持 keyed 模式与可变输出长度。
//
// Go 标准库没有 BLAKE2s（x/crypto/blake2s 属于第三方依赖，本仓库不引入），
// 而 WireGuard 的 MAC1/MAC2 需要 keyed-BLAKE2s、HKDF 需要 HMAC-BLAKE2s，
// 因此这里自己实现。参数块与压缩函数严格按 RFC 7693 §2.5/§3.2。
package main

import (
	"encoding/binary"
	"math/bits"
)

var b2sIV = [8]uint32{
	0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
	0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19,
}

var b2sSigma = [10][16]byte{
	{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15},
	{14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3},
	{11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4},
	{7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8},
	{9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13},
	{2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9},
	{12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11},
	{13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10},
	{6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5},
	{10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0},
}

// blake2sCompress 对 h 做一轮压缩：v[0..7] 为链值、v[8..15] 为 IV 派生量，
// v[12]/v[13] 混入计数器、v[14] 标记最后一块、v[15] 编码输出长度。
func blake2sCompress(h *[8]uint32, block []byte, counter uint64, last bool, outLen byte) {
	var m [16]uint32
	var v [16]uint32
	for i := 0; i < 16; i++ {
		m[i] = binary.LittleEndian.Uint32(block[i*4:])
	}
	copy(v[:8], h[:])
	copy(v[8:], b2sIV[:])
	v[12] ^= uint32(counter)
	v[13] ^= uint32(counter >> 32)
	if last {
		v[14] ^= 0xFFFFFFFF
	}
	v[15] ^= uint32(outLen)
	g := func(a, b, c, d int, x, y uint32) {
		v[a] += v[b] + x
		v[d] = bits.RotateLeft32(v[d]^v[a], -16)
		v[c] += v[d]
		v[b] = bits.RotateLeft32(v[b]^v[c], -12)
		v[a] += v[b] + y
		v[d] = bits.RotateLeft32(v[d]^v[a], -8)
		v[c] += v[d]
		v[b] = bits.RotateLeft32(v[b]^v[c], -7)
	}
	for r := 0; r < 10; r++ {
		s := b2sSigma[r]
		g(0, 4, 8, 12, m[s[0]], m[s[1]])
		g(1, 5, 9, 13, m[s[2]], m[s[3]])
		g(2, 6, 10, 14, m[s[4]], m[s[5]])
		g(3, 7, 11, 15, m[s[6]], m[s[7]])
		g(0, 5, 10, 15, m[s[8]], m[s[9]])
		g(1, 6, 11, 12, m[s[10]], m[s[11]])
		g(2, 7, 8, 13, m[s[12]], m[s[13]])
		g(3, 4, 9, 14, m[s[14]], m[s[15]])
	}
	for i := 0; i < 8; i++ {
		h[i] ^= v[i] ^ v[8+i]
	}
}

// blake2s 计算 BLAKE2s 摘要。key 为空即未键控模式；outLen 取值 1..32。
func blake2s(data, key []byte, outLen int) []byte {
	h := b2sIV
	var param [64]byte
	param[0] = byte(outLen)
	param[1] = byte(len(key))
	param[2] = 1 // fanout
	param[3] = 1 // depth
	blake2sCompress(&h, param[:], 0, false, byte(outLen))

	var counter uint64
	if len(key) > 0 {
		var kb [64]byte
		copy(kb[:], key)
		counter += 64
		blake2sCompress(&h, kb[:], counter, false, byte(outLen))
	}
	// 除最后一块外都按整块压缩：循环条件用 > 保证「恰好一块」时走最后一块分支
	for len(data) > 64 {
		counter += 64
		blake2sCompress(&h, data[:64], counter, false, byte(outLen))
		data = data[64:]
	}
	var last [64]byte
	copy(last[:], data)
	counter += uint64(len(data))
	blake2sCompress(&h, last[:], counter, true, byte(outLen))

	out := make([]byte, outLen)
	for i := 0; i < outLen; i++ {
		out[i] = byte(h[i/4] >> (8 * (i % 4)))
	}
	return out
}

// hmacBlake2s 是块长 64 的 HMAC-BLAKE2s，等价于内核 noise.c 的 hmac()。
// 超长密钥先哈希一次（HMAC 规范），空密钥也必须补足到块长。
func hmacBlake2s(key, msg []byte) []byte {
	var ipad, opad [64]byte
	k := key
	if len(k) > 64 {
		k = blake2s(k, nil, 32)
	}
	copy(ipad[:], k)
	copy(opad[:], k)
	for i := 0; i < 64; i++ {
		ipad[i] ^= 0x36
		opad[i] ^= 0x5C
	}
	inner := blake2s(append(ipad[:], msg...), nil, 32)
	return blake2s(append(opad[:], inner...), nil, 32)
}

// kdf 是 HKDF(BLAKE2s)：Extract 后按 0x01/0x02/0x03 顺序 Expand。
// n 表示要几个输出（Noise 的 MixKey 取 2、MixKeyAndHash 取 3、mix_ephemeral 取 1）。
func kdf(chainingKey, data []byte, n int) [][]byte {
	secret := hmacBlake2s(chainingKey, data)
	outs := make([][]byte, 0, n)
	prev := []byte{}
	for i := 1; i <= n; i++ {
		prev = hmacBlake2s(secret, append(append([]byte{}, prev...), byte(i)))
		outs = append(outs, prev)
	}
	return outs
}
