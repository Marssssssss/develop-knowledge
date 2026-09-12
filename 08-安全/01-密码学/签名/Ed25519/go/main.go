// Ed25519 签名/验签 demo — 使用 Go 标准库 crypto/ed25519
//
// 参考:
//   RFC 8032, "Edwards-Curve Digital Signature Algorithm (EdDSA)"
//   https://www.rfc-editor.org/rfc/rfc8032
//   Go 标准库 crypto/ed25519 内部即按 RFC 8032 §5.1.5/§5.1.6/§5.1.7 实现
//
// 运行: go run main.go
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/hex"
	"fmt"
)

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
	fmt.Println("[1] RFC 8032 §7.1 Test 1(seed 全 1 之后)")
	seed1 := mustHex("9d61b19deffd5a60ba844af492ec2cc4" +
		"4449c5697b326919703bac031cae7f60")
	expectedPk1 := mustHex("d75a980182b10ab7d54bfed3c964073a" +
		"0ee172f3daa62325af021a68f707511a")
	expectedSig1 := mustHex("e5564300c360ac729086e2cc806e828a" +
		"84877f1eb8e5d974d873e06522490155" +
		"5fb8821590a33bacc61e39701cf9b46b" +
		"d25bf5f0595bbe24655141438e7a100b")

	// crypto/ed25519.NewKeyFromSeed:输入 32B seed,返回 64B 私钥(前 32 是 seed,后 32 是 pk)
	priv1 := ed25519.NewKeyFromSeed(seed1)
	pub1 := priv1.Public().(ed25519.PublicKey)
	eqHex("TC1 公钥", []byte(pub1), expectedPk1)

	sig1, err := priv1.Sign(rand.Reader, []byte(""), ed25519.Hash(0, "")...) // PureEdDSA:不传 opts
	// PureEdDSA 时不传 options(默认 ed25519.Options{} 等价于空 HashFunc + 空 ctx)
	_ = err
	sig1, err = ed25519.Sign(priv1, nil) // 直接签名,内部即 PureEdDSA 流程
	if err != nil {
		panic(err)
	}
	eqHex("TC1 签名", sig1, expectedSig1)

	if !ed25519.Verify(pub1, nil, sig1) {
		panic("TC1 验签失败")
	}
	fmt.Println("  ✓ TC1 自验签通过")

	// 篡改消息
	if ed25519.Verify(pub1, []byte("x"), sig1) {
		panic("篡改消息未被检测")
	}
	fmt.Println("  ✓ 篡改消息被检测")

	// 篡改签名
	bad := append([]byte(nil), sig1...)
	bad[0] ^= 0x01
	if ed25519.Verify(pub1, nil, bad) {
		panic("篡改签名未被检测")
	}
	fmt.Println("  ✓ 篡改签名被检测")

	fmt.Println("\n[2] 随机密钥生成 + sign/verify + 跨语言互操作骨架")
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		panic(err)
	}
	fmt.Printf("  pub 长度 = %d,priv 长度 = %d\n", len(pub), len(priv))

	msg := []byte("hello Ed25519")
	sig, err := ed25519.Sign(priv, msg)
	if err != nil {
		panic(err)
	}
	if !ed25519.Verify(pub, msg, sig) {
		panic("随机密钥验签失败")
	}
	fmt.Println("  ✓ 随机密钥对 sign/verify 通过")

	fmt.Println("\n[3] 跨语言互操作骨架")
	fmt.Println("  ① 导出公钥(裸 32B):直接发送 []byte(pub)")
	fmt.Println("  ② 导出私钥(PKCS#8 / PEM):golang.org/x/crypto 提供的 ed25519.PrivateKey 可 marshall")
	fmt.Println("  ③ Python 端可用 cryptography.hazmat.primitives.serialization.load_*)")

	fmt.Println("\n全部 Ed25519 测试通过 ✓")

	_ = err // unused if branch never reached
}

func mustHex(s string) []byte {
	b, err := hex.DecodeString(s)
	if err != nil {
		panic(err)
	}
	return b
}