// PBKDF2: 基于口令的密钥派生(RFC 8018 §5.2), 向量取自 RFC 6070。
// F(P,S,c,i): U1=PRF(P,S||INT(i)), Uj=PRF(P,U(j-1)), F=U1^...^Uc; DK=拼接截断。
// 自测 5 组: c=1/2 / c=4096 / 长口令长盐 dkLen=25 / NUL 字节 / 确定性与雪崩。
package main

import (
	"crypto/hmac"
	"crypto/sha1"
	"fmt"
	"os"
)

func pbkdf2(password, salt []byte, iterations, dkLen int) []byte {
	prfLen := sha1.Size
	blocks := (dkLen + prfLen - 1) / prfLen
	var dk []byte
	for i := 1; i <= blocks; i++ {
		INTi := []byte{byte(i >> 24), byte(i >> 16), byte(i >> 8), byte(i)}
		m := hmac.New(sha1.New, password)
		m.Write(append(append([]byte{}, salt...), INTi...))
		u := m.Sum(nil)
		f := append([]byte{}, u...)
		for j := 1; j < iterations; j++ {
			m := hmac.New(sha1.New, password)
			m.Write(u)
			u = m.Sum(nil)
			for k := range f {
				f[k] ^= u[k]
			}
		}
		dk = append(dk, f...)
	}
	return dk[:dkLen]
}

func hex2bin(s string) []byte {
	b := make([]byte, len(s)/2)
	for i := range b {
		fmt.Sscanf(s[2*i:2*i+2], "%02x", &b[i])
	}
	return b
}

func main() {
	fail := 0
	check := func(name string, got, want []byte) {
		if string(got) != string(want) {
			fmt.Printf("FAIL %s\n  got  %x\n  want %x\n", name, got, want)
			fail++
		}
	}
	// demo1: c=1 / c=2
	check("c=1", pbkdf2([]byte("password"), []byte("salt"), 1, 20),
		hex2bin("0c60c80f961f0e71f3a9b524af6012062fe037a6"))
	check("c=2", pbkdf2([]byte("password"), []byte("salt"), 2, 20),
		hex2bin("ea6c014dc72d6f8ccd1ed92ace1d41f0d8de8957"))
	fmt.Println("demo1 RFC 6070 c=1 / c=2 基本向量: PASS")
	// demo2: c=4096
	check("c=4096", pbkdf2([]byte("password"), []byte("salt"), 4096, 20),
		hex2bin("4b007901b765489abead49d926f721d065a429c1"))
	fmt.Println("demo2 RFC 6070 c=4096 标准迭代: PASS")
	// demo3: 长口令长盐 dkLen=25
	check("dkLen=25", pbkdf2([]byte("passwordPASSWORDpassword"),
		[]byte("saltSALTsaltSALTsaltSALTsaltSALTsalt"), 4096, 25),
		hex2bin("3d2eec4fe41c849b80c8d83662c0e44a8b291a964cf2f07038"))
	fmt.Println("demo3 RFC 6070 长口令长盐 dkLen=25: PASS")
	// demo4: NUL 字节用例
	check("NUL", pbkdf2([]byte("pass\x00word"), []byte("sa\x00lt"), 4096, 16),
		hex2bin("56fa6aa75548099dcc37d7f03425e0c3"))
	fmt.Println("demo4 RFC 6070 含 NUL 字节口令/盐: PASS")
	// demo5: 确定性/迭代敏感/口令雪崩/盐隔离
	a := pbkdf2([]byte("pw"), []byte("salt"), 100, 20)
	b := pbkdf2([]byte("pw"), []byte("salt"), 100, 20)
	c := pbkdf2([]byte("pw"), []byte("salt"), 101, 20)
	d := pbkdf2([]byte("px"), []byte("salt"), 100, 20)
	e := pbkdf2([]byte("pw"), []byte("slat"), 100, 20)
	if string(a) == string(b) || string(a) == string(c) || string(a) == string(d) ||
		string(a) == string(e) {
		fmt.Println("FAIL demo5")
		fail++
	} else {
		fmt.Println("demo5 确定性/迭代敏感/口令雪崩/盐隔离: PASS")
	}
	if fail > 0 {
		os.Exit(1)
	}
	fmt.Println("ALL PASS")
}
