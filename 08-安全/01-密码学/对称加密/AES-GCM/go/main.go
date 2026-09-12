// AES-128-GCM demo — 使用 Go 标准库 crypto/aes + crypto/cipher GCM
//
// 参考:
//   NIST SP 800-38D, "Recommendation for Block Cipher Modes of Operation:
//   Galois/Counter Mode (GCM) and GMAC", November 2007
//   https://nvlpubs.nist.gov/nistpubs/legacy/sp/nistspecialpublication800-38d.pdf
//   Go 标准库 crypto/cipher.NewGCM 实现即遵循 NIST 800-38D §7
//
// 运行: go run main.go
package main

import (
	"crypto/aes"
	"crypto/cipher"
	"encoding/hex"
	"fmt"
)

// hex2b: 把 hex 字符串解码成字节切片,出错直接 panic(测试向量固定,不应出错)。
func hex2b(s string) []byte {
	b, err := hex.DecodeString(s)
	if err != nil {
		panic(err)
	}
	return b
}

// eq: 比较两字节切片是否相等,不等则格式化报错。
func eq(name string, got, want []byte) {
	if hex.EncodeToString(got) != hex.EncodeToString(want) {
		panic(fmt.Sprintf("%s 失配:\n  got  = %x\n  want = %x", name, got, want))
	}
	fmt.Printf("  ✓ %s\n", name)
}

// runAEAD: 调用 stdlib GCM 完成 encrypt/decrypt,并验证 NIST 测试向量。
//
// AES-GCM 的内部流程(NIST SP 800-38D §7):
//   1. J0 = IV || 0x00000001(IV=12B 时 deterministic construction)
//   2. H = E_K(0^128),作为 GHASH 子密钥
//   3. C = GCTR_K(J0+1, P)         — 计数模式加密
//   4. S = GHASH_H(A || C || lenA*8 || lenC*8)
//   5. T = GCTR_K(J0, S)[:t]        — 认证 tag
//
// crypto/cipher.NewGCM 内部即按此实现;这里只演示其外部 API 行为。
func runAEAD(block cipher.Block, iv, pt, aad, wantCT, wantTag []byte, label string) {
	aead, err := cipher.NewGCM(block)
	if err != nil {
		panic(err)
	}
	// Seal 把 AAD、nonce、plaintext 一起喂给 GCM,返回 ciphertext || tag
	ctWithTag := aead.Seal(nil, iv, pt, aad)
	ct := ctWithTag[:len(pt)]
	tag := ctWithTag[len(pt):]
	eq(label+" ct", ct, wantCT)
	eq(label+" tag", tag, wantTag)

	// Open:验证 tag + 解密回 plaintext(常量时间比较在 stdlib 内部完成)
	ptBack, err := aead.Open(nil, iv, ctWithTag, aad)
	if err != nil {
		panic(fmt.Sprintf("%s Open 失败: %v", label, err))
	}
	eq(label+" decrypt round-trip", ptBack, pt)
}

func main() {
	fmt.Println("[1] AES-128-GCM KAT (NIST SP 800-38D Appendix B)")

	// Test Case 1:全零 key/iv,空 pt + 空 aad
	{
		block, _ := aes.NewCipher(make([]byte, 16))
		aead, _ := cipher.NewGCM(block)
		ctWithTag := aead.Seal(nil, make([]byte, 12), []byte(""), []byte(""))
		eq("TC1 ct (空)", ctWithTag[:0], []byte{})
		eq("TC1 tag", ctWithTag, hex2b("58e2fccefa7e3061367f1d57a4e7455a"))
	}

	// Test Case 2:全零 key/iv,16B 零 pt,空 aad
	{
		block, _ := aes.NewCipher(make([]byte, 16))
		runAEAD(block,
			make([]byte, 12), make([]byte, 16), nil,
			hex2b("0388dace60b6a392f328c2b971b2fe78"),
			hex2b("ab6e47d42cec13bdf53a67b21257bddf"),
			"TC2")
	}

	// Test Case 3:64B pt,空 aad
	{
		block, _ := aes.NewCipher(hex2b("feffe9928665731c6d6a8f9467308308"))
		runAEAD(block,
			hex2b("cafebabefacedbaddecaf888"),
			hex2b("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"+
				"1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b391aafd255"),
			nil,
			hex2b("42831ec2217774244b7221b784d0d49ce3aa212f2c02a4e035c17e2329aca12e"+
				"21d514b25466931c7d8f6a5aac84aa051ba30b396a0aac973d58e091473f5985"),
			hex2b("4d5c2af327cd64a62cf35abd2ba6fab4"),
			"TC3")
	}

	// Test Case 4:60B pt,20B AAD
	{
		block, _ := aes.NewCipher(hex2b("feffe9928665731c6d6a8f9467308308"))
		runAEAD(block,
			hex2b("cafebabefacedbaddecaf888"),
			hex2b("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"+
				"1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39"),
			hex2b("feedfacedeadbeeffeedfacedeadbeefabaddad2"),
			hex2b("42831ec2217774244b7221b784d0d49ce3aa212f2c02a4e035c17e2329aca12e"+
				"21d514b25466931c7d8f6a5aac84aa051ba30b396a0aac973d58e091"),
			hex2b("5bc94fbc3221a5db94fae95ae7121a47"),
			"TC4")
	}

	// [2] 篡改检测:Open 在 tag/AAD/CT 任一被改时返回 error
	fmt.Println("\n[2] AES-GCM 篡改检测")
	block, _ := aes.NewCipher(hex2b("feffe9928665731c6d6a8f9467308308"))
	aead, _ := cipher.NewGCM(block)
	iv := hex2b("cafebabefacedbaddecaf888")
	pt := []byte("hello world")
	aad := []byte("meta")
	ctWithTag := aead.Seal(nil, iv, pt, aad)
	bad := append([]byte(nil), ctWithTag...)
	bad[0] ^= 0x01 // 翻一个密文位
	if _, err := aead.Open(nil, iv, bad, aad); err == nil {
		panic("篡改密文未被检测")
	} else {
		fmt.Println("  ✓ 密文位翻转被检测")
	}
	// 改 AAD
	if _, err := aead.Open(nil, iv, ctWithTag, []byte("metA")); err == nil {
		panic("篡改 AAD 未被检测")
	} else {
		fmt.Println("  ✓ AAD 改动被检测")
	}
	// 改 tag 末字节
	badTag := append([]byte(nil), ctWithTag...)
	badTag[len(badTag)-1] ^= 0x01
	if _, err := aead.Open(nil, iv, badTag, aad); err == nil {
		panic("篡改 tag 未被检测")
	} else {
		fmt.Println("  ✓ tag 改动被检测")
	}

	fmt.Println("\n全部 AES-GCM 测试通过 ✓")
}