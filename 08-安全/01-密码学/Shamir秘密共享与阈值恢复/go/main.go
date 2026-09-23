package main

import (
	"fmt"
	"math/rand"
)

func main() {
	fmt.Println("1. 素数域 GF(2^31-1)：秘密是常数项 f(0)")
	rp := rand.New(rand.NewSource(3))
	shares, _ := splitPrime(1234567, 5, 3, rp)
	for _, s := range shares {
		fmt.Printf("   份额 x=%d  y=%d\n", s[0], s[1])
	}
	fmt.Println("   任意 3 份 ->", recoverPrime(shares[:3]))
	fmt.Println("   只给 2 份 ->", recoverPrime(shares[:2]), "（不是秘密）")

	fmt.Println()
	fmt.Println("2. GF(2^8)（Vault）：份额 = 逐字节份额 + 1 个 x 字节")
	rv := rand.New(rand.NewSource(11))
	vs, _ := SplitVault([]byte("hello shamir!!"), 5, 3, rv)
	for _, q := range vs {
		fmt.Printf("   份额 %x  x=%d\n", q[:6], q[len(q)-1])
	}
	got, err := CombineVault([][]byte{vs[0], vs[2], vs[4]})
	fmt.Println("   任意 3 份 ->", string(got), err)
	less, _ := CombineVault([][]byte{vs[0], vs[2]})
	fmt.Printf("   只给 2 份 -> %x\n", less)
	fmt.Printf("   0x57·0x83 -> 0x%02x（AES 域常量）\n", gMul(0x57, 0x83))

	fmt.Println()
	fmt.Println("3. SLIP-0039：秘密在 x=255、摘要在 x=254")
	r39 := rand.New(rand.NewSource(7))
	secret := []byte{0x9d, 0x1c, 0x2f, 0x88, 0xa3, 0x77, 0x01, 0xfe,
		0x5b, 0xcc, 0xd2, 0x40, 0x19, 0x6e, 0xab, 0x03}
	s39, err := SplitSLIP39(3, 5, secret, r39)
	if err != nil {
		fmt.Println("   split 失败:", err)
		return
	}
	for i := range s39 {
		fmt.Printf("   份额 %d  x=%d  %x…\n", i, s39[i].X, s39[i].Y[:8])
	}
	rec, err := RecoverSLIP39(3, []share{s39[0], s39[2], s39[4]})
	fmt.Printf("   取 0,2,4   -> %x  err=%v\n", rec, err)
	bad := append([]byte(nil), s39[1].Y...)
	bad[0] ^= 1
	_, err = RecoverSLIP39(3, []share{s39[0], {s39[1].X, bad}, s39[2]})
	fmt.Println("   篡改第 1 份 ->", err)

	fmt.Println()
	fmt.Println("4. RS1024 校验和（GF(1024)，3 个 10 位字）")
	data := []int{5, 6, 7, 8}
	cs := rs1024Checksum("shamir", data)
	fmt.Println("   checksum =", cs)
	fmt.Println("   verify   =", rs1024Verify("shamir", append(append([]int{}, data...), cs...)))
}
