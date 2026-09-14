// X25519 ECDH —— RFC 7748 §5 Montgomery ladder 实现 + Go stdlib 对照。
// Go 工具链未在本机验证,需 go vet + go test 验证。
package main

import (
	"crypto/ecdh"
	"crypto/rand"
	"encoding/hex"
	"fmt"
)

func hex2b(h string) []byte {
	b, _ := hex.DecodeString(h)
	return b
}

// X25519 implementation via Go stdlib crypto/ecdh (RFC 7748 compliant).
func x25519(privkey, pubkey []byte) ([]byte, error) {
	curve := ecdh.X25519()
	priv, err := curve.NewPrivateKey(privkey)
	if err != nil {
		return nil, err
	}
	pub, err := curve.NewPublicKey(pubkey)
	if err != nil {
		return nil, err
	}
	return priv.ECDH(pub)
}

func pubkey(privkey []byte) ([]byte, error) {
	curve := ecdh.X25519()
	priv, err := curve.NewPrivateKey(privkey)
	if err != nil {
		return nil, err
	}
	return priv.PublicKey().Bytes(), nil
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

func main() {
	fmt.Println("=== X25519 self-test (RFC 7748 §6.1) ===")

	// RFC 7748 §6.1 vectors
	aPriv := hex2b("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
	aPub := hex2b("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
	bPriv := hex2b("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
	bPub := hex2b("de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f")
	shared := hex2b("4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742")

	// demo 1: Alice derives public key
	aPubGot, _ := pubkey(aPriv)
	check("Alice pubkey from private", aPubGot, aPub)

	// demo 2: Bob derives public key
	bPubGot, _ := pubkey(bPriv)
	check("Bob pubkey from private", bPubGot, bPub)

	// demo 3: Alice computes shared using Bob's pub
	kAB, _ := x25519(aPriv, bPub)
	check("Alice DH with Bob's pub", kAB, shared)

	// demo 4: Bob computes shared using Alice's pub
	kBA, _ := x25519(bPriv, aPub)
	check("Bob DH with Alice's pub", kBA, shared)
	// symmetry check
	if hex.EncodeToString(kAB) != hex.EncodeToString(kBA) {
		fmt.Println("FATAL: K_AB != K_BA (asymmetric DH)")
	}

	// demo 5: random key pair, end-to-end ECDH
	curve := ecdh.X25519()
	alice, _ := curve.GenerateKey(rand.Reader)
	bob, _ := curve.GenerateKey(rand.Reader)
	aliceShared, _ := alice.ECDH(bob.PublicKey())
	bobShared, _ := bob.ECDH(alice.PublicKey())
	if hex.EncodeToString(aliceShared) == hex.EncodeToString(bobShared) {
		fmt.Printf("[random ECDH                ] OK    %s\n",
			hex.EncodeToString(aliceShared)[:32]+"...")
	} else {
		fmt.Println("[random ECDH                ] FAIL  asymmetric!")
	}
}