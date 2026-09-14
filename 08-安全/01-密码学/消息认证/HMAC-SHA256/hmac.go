// HMAC-SHA256 教学实现 —— RFC 2104 §2 + RFC 4231 测试向量。
// Go 工具链未在本机验证,需 go vet + go test 验证。
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
)

const BLOCK = 64

func hmacSHA256(key, msg []byte) []byte {
	k := make([]byte, BLOCK)
	if len(key) > BLOCK {
		h := sha256.Sum256(key)
		copy(k, h[:])
	} else {
		copy(k, key)
	}
	ipad := make([]byte, BLOCK)
	opad := make([]byte, BLOCK)
	for i := 0; i < BLOCK; i++ {
		ipad[i] = k[i] ^ 0x36
		opad[i] = k[i] ^ 0x5C
	}
	inner := sha256.New()
	inner.Write(ipad)
	inner.Write(msg)
	hInner := inner.Sum(nil)
	outer := sha256.New()
	outer.Write(opad)
	outer.Write(hInner)
	return outer.Sum(nil)
}

func check(label string, got, want []byte) {
	g := hex.EncodeToString(got)
	w := hex.EncodeToString(want)
	status := "OK"
	if g != w {
		status = "FAIL"
	}
	fmt.Printf("[%-40s] %s  %s\n", label, status, g)
}

func hex2b(h string) []byte {
	b, _ := hex.DecodeString(h)
	return b
}

func main() {
	fmt.Println("=== HMAC-SHA256 self-test (RFC 4231) ===")
	// Case 1
	k1 := make([]byte, 20)
	for i := range k1 {
		k1[i] = 0x0b
	}
	check("Case 1: 20×0x0b + Hi There",
		hmacSHA256(k1, []byte("Hi There")),
		hex2b("b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"))
	// Case 2
	check("Case 2: key=Jefe",
		hmacSHA256([]byte("Jefe"), []byte("what do ya want for nothing?")),
		hex2b("5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"))
	// Case 3
	k3 := make([]byte, 20)
	for i := range k3 {
		k3[i] = 0xaa
	}
	d3 := make([]byte, 50)
	for i := range d3 {
		d3[i] = 0xdd
	}
	check("Case 3: 20×0xaa + 50×0xdd",
		hmacSHA256(k3, d3),
		hex2b("773ea91e36800e46854db8ebd09181a72959098b3ef8c122d9635514ced565fe"))
	// Case 6 — key > B triggers H(K) reduction
	k6 := make([]byte, 131)
	for i := range k6 {
		k6[i] = 0xaa
	}
	check("Case 6: key=131×0xaa (H(K) reduction)",
		hmacSHA256(k6, []byte("Test Using Larger Than Block-Size Key - Hash Key First")),
		hex2b("60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54"))
	// Case 7 — both key > B and data > B
	check("Case 7: key=131×0xaa + long data",
		hmacSHA256(k6, []byte(
			"This is a test using a larger than block-size key and a larger "+
				"than block-size data. The key needs to be hashed before "+
				"being used by the HMAC algorithm.")),
		hex2b("9b09ffa71b942fcb27635fbcd5b0e944bfdc63644f0713938a7f51535c3a35e2"))
}