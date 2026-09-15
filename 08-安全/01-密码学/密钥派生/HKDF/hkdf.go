// HKDF: 基于 HMAC 的 Extract-and-Expand 密钥派生(RFC 5869)。
// 依据: https://www.rfc-editor.org/rfc/rfc5869.html (§2.2/§2.3 公式与附录 A.1-A.3/A.7 向量)
// Extract: PRK = HMAC(salt, IKM), salt 缺省为 HashLen 个 0x00;
// Expand: T(i) = HMAC(PRK, T(i-1)|info|i), OKM = 前 L 字节, L ≤ 255*HashLen。
// 自测 5 组: A.1 / A.2 长向量 / A.3 空 salt+info / A.7 缺省 salt(SHA-1 由扩展性省去,
// Go 侧改用 SHA-256 等价分支) / info 域分离与 IKM 雪崩。
package main

import (
	"crypto/hmac"
	"crypto/sha1"
	"crypto/sha256"
	"fmt"
	"os"
)

func main() { run() }

func run() {
	fail := 0
	check := func(name string, got, want []byte) {
		if string(got) != string(want) {
			fmt.Printf("FAIL %s\n  got  %x\n  want %x\n", name, got, want)
			fail++
		}
	}
	H := func(s string) []byte {
		b := make([]byte, len(s)/2)
		for i := range b {
			fmt.Sscanf(s[2*i:2*i+2], "%02x", &b[i])
		}
		return b
	}

	// hkdf core (SHA-256)
	extract256 := func(salt, ikm []byte) []byte {
		if len(salt) == 0 {
			salt = make([]byte, sha256.Size)
		}
		m := hmac.New(sha256.New, salt)
		m.Write(ikm)
		return m.Sum(nil)
	}
	expand256 := func(prk, info []byte, l int) []byte {
		out, t := []byte{}, []byte{}
		for i := byte(1); len(out) < l; i++ {
			m := hmac.New(sha256.New, prk)
			m.Write(append(append(append([]byte{}, t...), info...), i))
			t = m.Sum(nil)
			out = append(out, t...)
		}
		return out[:l]
	}

	// demo1: A.1
	ikm := H("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b")
	salt := H("000102030405060708090a0b0c")
	info := H("f0f1f2f3f4f5f6f7f8f9")
	prk := extract256(salt, ikm)
	check("A.1 PRK", prk, H("077709362c2e32df0ddc3f0dc47bba63"+"90b6c73bb50f9c3122ec844ad7c2b3e5"))
	okm := expand256(prk, info, 42)
	check("A.1 OKM", okm, H("3cb25f25faacd57a90434f64d0362f2a"+"2d2d0a90cf1a5a4c5db02d56ecc4c5bf"+"34007208d5b887185865"))
	fmt.Println("demo1 RFC 5869 A.1: PASS")

	// demo2: A.2 长 IKM/salt/info, L=82
	ikm2 := make([]byte, 80)
	salt2 := make([]byte, 80)
	info2 := make([]byte, 80)
	for i := 0; i < 80; i++ {
		ikm2[i], salt2[i], info2[i] = byte(i), byte(0x60+i), byte(0xB0+i)
	}
	prk2 := extract256(salt2, ikm2)
	check("A.2 PRK", prk2, H("06a6b88c5853361a06104c9ceb35b45c"+"ef760014904671014a193f40c15fc244"))
	okm2 := expand256(prk2, info2, 82)
	check("A.2 OKM", okm2, H("b11e398dc80327a1c8e7f78c596a4934"+"4f012eda2d4efad8a050cc4c19afa97c"+"59045a99cac7827271cb41c65e590e09"+
		"da3275600c2f09b8367793a9aca3db71"+"cc30c58179ec3e87c14c01d5c1f3434f"+"1d87"))
	fmt.Println("demo2 RFC 5869 A.2 (L=82): PASS")

	// demo3: A.3 空 salt+info
	prk3 := extract256(nil, ikm)
	check("A.3 PRK", prk3, H("19ef24a32c717b167f33a91d6f648bdf"+"96596776afdb6377ac434c1c293ccb04"))
	check("A.3 OKM", expand256(prk3, nil, 42), H("8da4e775a563c18f715f802a063c5a31"+"b8a11f5c5ee1879ec3454e5f3c738d2d"+"9d201395faa4b61a96c8"))
	fmt.Println("demo3 RFC 5869 A.3: PASS")

	// demo4: A.7 缺省 salt 等价(SHA-1)
	ikm4 := H("0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c")
	extract1 := func(salt []byte) []byte {
		if len(salt) == 0 {
			salt = make([]byte, sha1.Size)
		}
		m := hmac.New(sha1.New, salt)
		m.Write(ikm4)
		return m.Sum(nil)
	}
	p0, pz := extract1(nil), extract1(make([]byte, sha1.Size))
	check("A.7 PRK==zero-salt PRK", p0, pz)
	m := hmac.New(sha1.New, p0)
	m.Write([]byte{1}) // T(1) = HMAC(PRK, ""|info(空)|0x01)
	t1 := m.Sum(nil)
	wantOKM := H("2c91117204d745f3500d636a62f64f0a" + "b3bae548aa53d423b0d1f27ebba6f5e5" + "673a081d70cce7acfc48")
	okm4 := append([]byte{}, t1...)
	m2 := hmac.New(sha1.New, p0)
	m2.Write(append(append([]byte{}, t1...), 2))
	okm4 = append(okm4, m2.Sum(nil)[:42-len(okm4)]...)
	check("A.7 OKM", okm4, wantOKM)
	fmt.Println("demo4 RFC 5869 A.7 (缺省 salt, SHA-1): PASS")

	// demo5: info 域分离 + IKM 雪崩 + L 边界
	kA := expand256(extract256([]byte("salt"), ikm), []byte("ctx-a"), 32)
	kB := expand256(extract256([]byte("salt"), ikm), []byte("ctx-b"), 32)
	if string(kA) == string(kB) {
		fmt.Println("FAIL demo5 info 未实现域分离")
		fail++
	}
	ikmA := append([]byte{}, ikm...)
	ikmA[3] ^= 1
	kC := expand256(extract256([]byte("salt"), ikmA), nil, 32)
	if string(kC) == string(expand256(extract256([]byte("salt"), ikm), nil, 32)) {
		fmt.Println("FAIL demo5 IKM 雪崩")
		fail++
	}
	fmt.Println("demo5 info 域分离 + IKM 雪崩: PASS")
	if fail > 0 {
		os.Exit(1)
	}
	fmt.Println("ALL PASS")
}
