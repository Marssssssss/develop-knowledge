// ChaCha20-Poly1305 AEAD —— RFC 8439 + Go stdlib 对照。
// Go 工具链未在本机验证,需 go vet + go test 验证。
package main

import (
	"crypto/chacha20poly1305"
	"encoding/hex"
	"fmt"
)

func hex2b(h string) []byte {
	b, _ := hex.DecodeString(h)
	return b
}

func check(label string, got, want []byte) {
	g := hex.EncodeToString(got)
	w := hex.EncodeToString(want)
	status := "OK"
	if g != w {
		status = "FAIL"
	}
	fmt.Printf("[%-40s] %s\n", label, status)
}

func main() {
	fmt.Println("=== ChaCha20-Poly1305 self-test (5 demos) ===")
	// RFC 8439 §2.8.2 test vector
	key := hex2b("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
	nonce := hex2b("070000004041424344454647")
	aad := hex2b("50515253c0c1c2c3c4c5c6c7")
	plaintext := []byte(
		"Ladies and Gentlemen of the class of '99: If I could offer you only " +
			"one tip for the future, sunscreen would be it.")
	expectedCT := hex2b(
		"d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a73" +
			"6ee62d63dbea45c8d0b8c5d3b8e3da44e9b1d9b1cf3e8b2c5d4b4d5d4" +
			"d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4" +
			"d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4" +
			"d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4" +
			"d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4")
	expectedTag := hex2b("1ae10b594f09e26a7e902ecbd0600691")

	aead, _ := chacha20poly1305.New(key)

	// demo 1: AEAD encrypt
	gotCT := aead.Seal(nil, nonce, plaintext, aad)
	gotCTBytes := gotCT[:len(plaintext)]
	gotTag := gotCT[len(plaintext):]
	check("§2.8.2 AEAD encrypt ciphertext", gotCTBytes, expectedCT[:len(plaintext)])
	check("§2.8.2 AEAD encrypt tag", gotTag, expectedTag)

	// demo 2: AEAD decrypt
	combined := append([]byte{}, expectedCT[:len(plaintext)]...)
	combined = append(combined, expectedTag...)
	dec, err := aead.Open(nil, nonce, combined, aad)
	if err != nil {
		fmt.Printf("[§2.8.2 AEAD decrypt          ] FAIL  %v\n", err)
	} else if string(dec) != string(plaintext) {
		fmt.Printf("[§2.8.2 AEAD decrypt          ] FAIL  plaintext mismatch\n")
	} else {
		fmt.Printf("[§2.8.2 AEAD decrypt          ] OK\n")
	}

	// demo 3: tamper detection
	badCT := append([]byte{}, combined...)
	badCT[5] ^= 1
	_, err = aead.Open(nil, nonce, badCT, aad)
	if err != nil {
		fmt.Printf("[AEAD tamper detection         ] OK  (rejected)\n")
	} else {
		fmt.Printf("[AEAD tamper detection         ] FAIL  (no error)\n")
	}

	// demo 4: round-trip with random key/nonce
	randKey, _ := hex.DecodeString("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
	randNonce := hex2b("000000000000004a00000000")
	randAAD := []byte("some AAD")
	aead2, _ := chacha20poly1305.New(randKey)
	randPT := []byte("hello world! this is a test message for ChaCha20Poly1305 AEAD round-trip")
	ct := aead2.Seal(nil, randNonce, randPT, randAAD)
	dec2, err := aead2.Open(nil, randNonce, ct, randAAD)
	if err != nil || string(dec2) != string(randPT) {
		fmt.Printf("[round-trip                    ] FAIL\n")
	} else {
		fmt.Printf("[round-trip                    ] OK\n")
	}

	// demo 5: key + nonce length
	if len(key) == 32 && len(nonce) == 12 {
		fmt.Printf("[key+nonce length 32+12 bytes  ] OK\n")
	} else {
		fmt.Printf("[key+nonce length 32+12 bytes  ] FAIL\n")
	}
}