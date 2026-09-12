// RSA-PSS 签名/验签 demo — 使用 Go 标准库 crypto/rsa + crypto/rand
//
// 参考:
//   RFC 8017, "PKCS #1: RSA Cryptography Specifications Version 2.2", §8.1
//   https://www.rfc-editor.org/rfc/rfc8017
//   Go 标准库 crypto/rsa SignPSS / VerifyPSS 即遵循 RFC 8017 §8.1.1 / §8.1.2
//
// 运行: go run main.go
package main

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"fmt"
)

// eqHex: 断言两字节切片相等,否则 panic。
func eqHex(name string, got, want []byte) {
	if len(got) != len(want) {
		panic(fmt.Sprintf("%s 长度不等:got %d, want %d", name, len(got), len(want)))
	}
	for i := range got {
		if got[i] != want[i] {
			panic(fmt.Sprintf("%s 在 byte %d 失配:got %02x, want %02x", name, i, got[i], want[i]))
		}
	}
	fmt.Printf("  ✓ %s\n", name)
}

func main() {
	fmt.Println("[1] 生成 2048-bit RSA 私钥…")
	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		panic(err)
	}
	pub := &priv.PublicKey
	fmt.Printf("  n = %d bits, e = %d\n", pub.N.BitLen(), pub.E)

	msg := []byte("hello RSA-PSS world")
	hashed := sha256.Sum256(msg)

	fmt.Println("\n[2] SignPSS / VerifyPSS(SHA-256, MGF1-SHA256, salt = 32B)")
	opts := &rsa.PSSOptions{
		SaltLength: rsa.PSSSaltLengthAuto, // == hLen = 32
		Hash:       crypto.SHA256,
	}
	sig, err := rsa.SignPSS(rand.Reader, priv, crypto.SHA256, hashed[:], opts)
	if err != nil {
		panic(err)
	}
	fmt.Printf("  签名长度 = %d bytes(== 2048/8 = 256)\n", len(sig))

	// 正常签名 → 验签通过
	if err := rsa.VerifyPSS(pub, crypto.SHA256, hashed[:], sig, opts); err != nil {
		panic(fmt.Sprintf("正常验签失败: %v", err))
	}
	fmt.Println("  ✓ 正常验签通过")

	// 篡改消息 → 验签失败
	badHash := sha256.Sum256(append(msg, '!'))
	if err := rsa.VerifyPSS(pub, crypto.SHA256, badHash[:], sig, opts); err == nil {
		panic("篡改消息未被检测")
	} else {
		fmt.Println("  ✓ 篡改消息被检测:", err)
	}

	// 篡改签名 → 验签失败
	badSig := append([]byte(nil), sig...)
	badSig[0] ^= 0x01
	if err := rsa.VerifyPSS(pub, crypto.SHA256, hashed[:], badSig, opts); err == nil {
		panic("篡改签名未被检测")
	} else {
		fmt.Println("  ✓ 篡改签名被检测:", err)
	}

	// 概率签名:两次签名结果不同(salt 随机)
	sig2, _ := rsa.SignPSS(rand.Reader, priv, crypto.SHA256, hashed[:], opts)
	same := true
	for i := range sig {
		if sig[i] != sig2[i] {
			same = false
			break
		}
	}
	if same {
		panic("PSS 应是概率签名,两次结果相同(异常)")
	}
	fmt.Println("  ✓ 两次签名 salt 不同(PSS 概率性质)")

	// 跨语种互操作:用 Python (n, e) + Go 验签
	// 这里演示:把 Go 的公钥导出为 PKIX,再让 Python 解析(留作 hook 注释)
	fmt.Println("\n[3] 跨语言互操作骨架(PKIX 序列化):")
	fmt.Println("  x509.MarshalPKIXPublicKey(pub) → []byte (SubjectPublicKeyInfo, RFC 5280)")
	fmt.Println("  → 可用 cryptography.hazmat 加载或 openssl 解析")

	fmt.Println("\n全部 RSA-PSS 测试通过 ✓")

	// 静默 unused 检查
	_ = eqHex
}