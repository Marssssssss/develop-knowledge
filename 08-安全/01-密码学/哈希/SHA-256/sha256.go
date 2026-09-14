// SHA-256 教学实现 —— RFC 6234 / FIPS 180-4 严格对照。
// 单文件 Go,无外部依赖。Go 工具链未在本机验证,需 go vet + go test 验证。
package main

import (
	"encoding/hex"
	"fmt"
)

// Initial hash value FIPS 180-4 §5.3.3
var hInit = [8]uint32{
	0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
	0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19,
}

// K[64] FIPS 180-4 §4.2.2
var k = [64]uint32{
	0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
	0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
	0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
	0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
	0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
	0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
	0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
	0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
	0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
	0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
	0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
	0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
	0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
	0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
	0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
	0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
}

func rotr(x uint32, n uint) uint32 { return (x >> n) | (x << (32 - n)) }
func ch(x, y, z uint32) uint32     { return (x & y) ^ ((^x) & z) }
func maj(x, y, z uint32) uint32    { return (x & y) ^ (x & z) ^ (y & z) }
func bsig0(x uint32) uint32 {
	return rotr(x, 2) ^ rotr(x, 13) ^ rotr(x, 22)
}
func bsig1(x uint32) uint32 {
	return rotr(x, 6) ^ rotr(x, 11) ^ rotr(x, 25)
}
func ssig0(x uint32) uint32 { return rotr(x, 7) ^ rotr(x, 18) ^ (x >> 3) }
func ssig1(x uint32) uint32 { return rotr(x, 17) ^ rotr(x, 19) ^ (x >> 10) }

type ctx struct {
	H      [8]uint32
	buf    [64]byte
	buflen int
	bitlen uint64
}

func newCtx() *ctx { return &ctx{H: hInit} }

func (c *ctx) update(data []byte) {
	c.bitlen += uint64(len(data)) * 8
	for len(data) > 0 {
		n := copy(c.buf[c.buflen:], data)
		c.buflen += n
		data = data[n:]
		if c.buflen == 64 {
			c.compress(c.buf[:])
			c.buflen = 0
		}
	}
}

func (c *ctx) compress(block []byte) {
	var W [64]uint32
	for t := 0; t < 16; t++ {
		W[t] = uint32(block[t*4])<<24 |
			uint32(block[t*4+1])<<16 |
			uint32(block[t*4+2])<<8 |
			uint32(block[t*4+3])
	}
	for t := 16; t < 64; t++ {
		W[t] = ssig1(W[t-2]) + W[t-7] + ssig0(W[t-15]) + W[t-16]
	}
	a, b, cc, d := c.H[0], c.H[1], c.H[2], c.H[3]
	e, f, g, h := c.H[4], c.H[5], c.H[6], c.H[7]
	for t := 0; t < 64; t++ {
		T1 := h + bsig1(e) + ch(e, f, g) + k[t] + W[t]
		T2 := bsig0(a) + maj(a, b, cc)
		h, g, f, e = g, f, e, d+T1
		d, cc, b, a = cc, b, a, T1+T2
	}
	c.H[0] += a
	c.H[1] += b
	c.H[2] += cc
	c.H[3] += d
	c.H[4] += e
	c.H[5] += f
	c.H[6] += g
	c.H[7] += h
}

func (c *ctx) digest() [32]byte {
	c.buf[c.buflen] = 0x80
	c.buflen++
	if c.buflen > 56 {
		for c.buflen < 64 {
			c.buf[c.buflen] = 0
			c.buflen++
		}
		c.compress(c.buf[:])
		c.buflen = 0
	}
	for c.buflen < 56 {
		c.buf[c.buflen] = 0
		c.buflen++
	}
	bl := c.bitlen
	for i := 7; i >= 0; i-- {
		c.buf[56+i] = byte(bl & 0xFF)
		bl >>= 8
	}
	c.compress(c.buf[:])
	var out [32]byte
	for i := 0; i < 8; i++ {
		out[i*4] = byte(c.H[i] >> 24)
		out[i*4+1] = byte(c.H[i] >> 16)
		out[i*4+2] = byte(c.H[i] >> 8)
		out[i*4+3] = byte(c.H[i])
	}
	return out
}

func sha256(data []byte) [32]byte {
	c := newCtx()
	c.update(data)
	return c.digest()
}

func check(label string, got [32]byte, want string) {
	g := hex.EncodeToString(got[:])
	status := "OK"
	if g != want {
		status = "FAIL"
	}
	fmt.Printf("[%-22s] %s  %s\n", label, status, g)
	if status == "FAIL" {
		fmt.Printf("  expected: %s\n", want)
	}
}

func main() {
	fmt.Println("=== SHA-256 self-test (5 demos + 1 streaming) ===")
	check("empty", sha256([]byte("")),
		"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
	check("abc", sha256([]byte("abc")),
		"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
	check("448-bit B.2", sha256([]byte(
		"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq")),
		"248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1")
	check("896-bit B.3", sha256([]byte(
		"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmn"+
			"hijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu")),
		"cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1")

	// million 'a' via streaming
	c := newCtx()
	for i := 0; i < 1000000; i++ {
		c.update([]byte("a"))
	}
	check("million 'a'", c.digest(),
		"cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0")

	// streaming equivalence
	c1, c2 := newCtx(), newCtx()
	data := make([]byte, 1000)
	for i := range data {
		data[i] = 'x'
	}
	c1.update(data)
	for i := 0; i < len(data); i += 7 {
		end := i + 7
		if end > len(data) {
			end = len(data)
		}
		c2.update(data[i:end])
	}
	g1, g2 := c1.digest(), c2.digest()
	status := "OK"
	if g1 != g2 {
		status = "FAIL"
	}
	fmt.Printf("[streaming equivalence] %s  %s\n", status,
		hex.EncodeToString(g1[:]))
}