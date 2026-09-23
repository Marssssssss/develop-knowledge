package main

// AES 密钥包装演示（Go 版）：与 python/main.py 输出一一对应。

import (
	"encoding/hex"
	"fmt"
	"strings"
)

func hx(b []byte) string { return strings.ToUpper(hex.EncodeToString(b)) }

func line(t string) {
	fmt.Println("\n" + t)
	n := 2 * len([]rune(t))
	if n < 60 {
		n = 60
	}
	fmt.Println(strings.Repeat("-", n))
}

func main() {
	line("1. AES 分组密码（FIPS 197，本 demo 自带）")
	fmt.Printf("SBOX[0x00]=%02X SBOX[0x01]=%02X SBOX[0x53]=%02X\n", sbox[0], sbox[1], sbox[0x53])
	key := make([]byte, 16)
	for i := range key {
		key[i] = byte(i)
	}
	blk, _ := hex.DecodeString("00112233445566778899aabbccddeeff")
	c := encryptBlock(key, blk)
	fmt.Printf("AES-128 %s -> %s\n", hx(blk), hx(c))
	fmt.Printf("        解密还原 %s\n", hx(decryptBlock(key, c)))

	line(fmt.Sprintf("2. RFC 3394 AES-KW：默认 IV = %016X", uint64(defaultIV)))
	kek, _ := hex.DecodeString("000102030405060708090a0b0c0d0e0f")
	pt, _ := hex.DecodeString("00112233445566778899aabbccddeeff")
	ct, err := aesWrap(kek, pt, defaultIV)
	if err != nil {
		panic(err)
	}
	fmt.Printf("KEK  %s\n", hx(kek))
	fmt.Printf("明文 %s  (%d 位)\n", hx(pt), len(pt)*8)
	fmt.Printf("密文 %s  (多出 64 位)\n", hx(ct))
	back, err := aesUnwrap(kek, ct, defaultIV)
	fmt.Printf("解包 %s err=%v\n", hx(back), err)
	bad := append([]byte(nil), ct...)
	bad[3] ^= 0x01
	if _, err := aesUnwrap(kek, bad, defaultIV); err != nil {
		fmt.Printf("篡改 1 位 -> 解包拒绝：%v\n", err)
	}

	line("3. RFC 5649 AES-KWP：AIV = A65959A6 || MLI")
	kek3, _ := hex.DecodeString("5840df6e29b02af1ab493b705bf16ea1ae8338f4dcc176a8")
	for _, m := range []int{7, 20} {
		pt3 := make([]byte, m)
		for i := range pt3 {
			pt3[i] = byte((i*7 + 0x30) & 0xFF)
		}
		ct3, err := kwpWrap(kek3, pt3)
		if err != nil {
			panic(err)
		}
		a, padded, _ := unwrapCore(kek3, ct3)
		fmt.Printf("m=%2d -> 补齐 %2d 字节 (n=%d) -> 密文 %d 字节\n",
			m, len(padded), len(padded)/8, len(ct3))
		fmt.Printf("     A = %016X  高32位=%08X 低32位(MLI)=%d\n", a, a>>32, a&0xFFFFFFFF)
		out, _ := kwpUnwrap(kek3, ct3)
		fmt.Printf("     往返 %s\n", hx(out))
	}

	line("4. AIV 三条完整性检查（RFC 5649 §3）")
	fmt.Printf("1) MSB(32,A) == %08X\n", uint64(aivConst))
	fmt.Println("2) 8*(n-1) <  LSB(32,A) <= 8*n")
	fmt.Println("3) 右端 b = 8*n - MLI 个补齐字节全为零")
	kek4, _ := hex.DecodeString("000102030405060708090a0b0c0d0e0f")
	body := make([]byte, 16)
	for i := range body {
		body[i] = 0x11
	}
	okCT, _ := aesWrap(kek4, body, aiv(16))
	out, _ := kwpUnwrap(kek4, okCT)
	fmt.Printf("MSB 正确     -> %s\n", hx(out))
	cases := []struct {
		name string
		iv   uint64
		last byte
	}{
		{"MSB 错", 0x11223344<<32 | 16, 0x00},
		{"MLI=17 越界", aiv(17), 0x00},
		{"补齐非零", aiv(15), 0x01},
	}
	for _, cs := range cases {
		b2 := make([]byte, 16)
		for i := 0; i < 15; i++ {
			b2[i] = 0x11
		}
		b2[15] = cs.last
		ct4, _ := aesWrap(kek4, b2, cs.iv)
		if _, err := kwpUnwrap(kek4, ct4); err != nil {
			fmt.Printf("%-12s -> 拒绝：%v\n", cs.name, err)
		}
	}

	line("5. 与 RFC 3394 的边界")
	fmt.Println("AES-KW 要求 n >= 2（明文至少 16 字节）；n == 1 是 KWP 独有的单块 ECB 分支")
	fmt.Println("AES-KWP 明文长度 1 .. 2^32-1 字节，密文恒为 8*(ceil(m/8)+1) 字节")
	for _, m := range []int{8, 9, 16, 17} {
		ct5, _ := kwpWrap(kek4, make([]byte, m))
		fmt.Printf("    m=%2d -> 密文 %d 字节\n", m, len(ct5))
	}
}
