// HOTP(RFC 4226)/TOTP(RFC 6238): 基于 HMAC 的一次性口令。
// 依据: RFC 4226 §5.3(动态截断) + 附录 D(count 0-9 向量);
//       RFC 6238 §4(T=floor((unix-T0)/X)) + 附录 B(SHA1/256/512 向量)。
// 自测 5 组: 附录 D / 附录 B / §5.4 截断细节 / 步进+resync 窗口 / 密钥雪崩。
package main

import (
	"crypto/hmac"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/sha512"
	"fmt"
	"hash"
	"os"
)

func hotp(key []byte, counter uint64, digits int, newHash func() hash.Hash) int {
	c := make([]byte, 8)
	for i := 0; i < 8; i++ { // 8 字节大端计数器
		c[7-i] = byte(counter >> (8 * uint(i)))
	}
	m := hmac.New(newHash, key)
	m.Write(c)
	hs := m.Sum(nil)
	off := hs[len(hs)-1] & 0xF // 动态截断: 末字节低 4 位
	code := (uint32(hs[off]&0x7F) << 24) | (uint32(hs[off+1]) << 16) |
		(uint32(hs[off+2]) << 8) | uint32(hs[off+3])
	mod := uint64(1)
	for i := 0; i < digits; i++ {
		mod *= 10
	}
	return int(uint64(code) % mod)
}

func totp(key []byte, unix int64, digits int, newHash func() hash.Hash) int {
	return hotp(key, uint64(unix/30), digits, newHash)
}

func verifyWide(key []byte, code int, unix int64, digits int, newHash func() hash.Hash) bool {
	t := unix / 30
	for dt := int64(-1); dt <= 1; dt++ { // ±1 步 resync 窗口
		if hotp(key, uint64(t+dt), digits, newHash) == code {
			return true
		}
	}
	return false
}

func main() {
	fail := 0
	secret20 := []byte("12345678901234567890")
	secret32 := []byte("12345678901234567890123456789012")
	secret64 := []byte("1234567890123456789012345678901234567890123456789012345678901234")

	// demo1: RFC 4226 附录 D
	want := []int{755224, 287082, 359152, 969429, 338314,
		254676, 287922, 162583, 399871, 520489}
	for c := 0; c < 10; c++ {
		if hotp(secret20, uint64(c), 6, sha1.New) != want[c] {
			fmt.Printf("FAIL demo1 HOTP(%d)\n", c)
			fail++
		}
	}
	if fail == 0 {
		fmt.Println("demo1 RFC 4226 附录 D (count 0-9): PASS")
	}

	// demo2: RFC 6238 附录 B(6 时间点 × 3 算法)
	type vec struct {
		t          int64
		v1, v256, v512 int
	}
	vecs := []vec{
		{59, 94287082, 46119246, 90693936},
		{1111111109, 7081804, 68084774, 25091201},
		{1111111111, 14050471, 67062674, 99943326},
		{1234567890, 89005924, 91819424, 93441116},
		{2000000000, 69279037, 90698825, 38618901},
		{20000000000, 65353130, 77737706, 47863826},
	}
	for _, v := range vecs {
		if totp(secret20, v.t, 8, sha1.New) != v.v1 ||
			totp(secret32, v.t, 8, sha256.New) != v.v256 ||
			totp(secret64, v.t, 8, sha512.New) != v.v512 {
			fmt.Printf("FAIL demo2 TOTP @%d\n", v.t)
			fail++
		}
	}
	if fail == 0 {
		fmt.Println("demo2 RFC 6238 附录 B (SHA1/256/512): PASS")
	}

	// demo3: §5.4 截断细节(offset=0xa -> 872921)
	hs := []byte{0x1f, 0x86, 0x98, 0x69, 0x0e, 0x02, 0xca, 0x16, 0x61, 0x85,
		0x50, 0xef, 0x7f, 0x19, 0xda, 0x8e, 0x94, 0x5b, 0x55, 0x5a}
	off := hs[19] & 0xF
	dbc := (uint32(hs[off]&0x7F) << 24) | (uint32(hs[off+1]) << 16) |
		(uint32(hs[off+2]) << 8) | uint32(hs[off+3])
	if off != 0xA || dbc != 0x50EF7F19 || dbc%1000000 != 872921 {
		fmt.Println("FAIL demo3 截断细节")
		fail++
	} else {
		fmt.Println("demo3 §5.4 动态截断细节(offset=0xa -> 872921): PASS")
	}

	// demo4: 时间步边界 + resync 窗口
	c59 := totp(secret20, 59, 8, sha1.New)
	if totp(secret20, 60, 8, sha1.New) == c59 ||
		!verifyWide(secret20, c59, 59+30, 8, sha1.New) ||
		verifyWide(secret20, c59, 59+60, 8, sha1.New) {
		fmt.Println("FAIL demo4 步进/窗口")
		fail++
	} else {
		fmt.Println("demo4 时间步边界 + ±1 步 resync 窗口: PASS")
	}

	// demo5: 密钥雪崩
	key2 := append([]byte{}, secret20...)
	key2[7] ^= 1
	same := 0
	for c := 0; c < 10; c++ {
		if hotp(key2, uint64(c), 6, sha1.New) == want[c] {
			same++
		}
	}
	if same > 0 {
		fmt.Println("FAIL demo5 密钥雪崩")
		fail++
	} else {
		fmt.Println("demo5 密钥雪崩(变 1 bit 输出全变): PASS")
	}
	if fail > 0 {
		os.Exit(1)
	}
	fmt.Println("ALL PASS")
}
