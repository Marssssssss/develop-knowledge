// IKEv2 + ESP 演示 (RFC 7296 + RFC 4303) — Go 实现
//
// 演示 IKEv2 控制面 + ESP 数据面:
//   - IKE_SA_INIT: X25519 DH + Nonce → SKEYSEED → 7 把子密钥 (§2.14)
//   - 派生 Child SA keymat(4 × 16 B, AES-128-GCM)
//   - 构造 ESP 包 + 解密还原
//
// 依赖: golang.org/x/crypto/curve25519
// 运行: cd go && go get golang.org/x/crypto/curve25519 && go run ikev2_demo.go

package main

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"

	"golang.org/x/crypto/curve25519"
)

// ============================================================
// 1. PRF + PRF+ (RFC 7296 §2.13)
// ============================================================

func prf(key, data []byte) []byte {
	h := hmac.New(sha256.New, key)
	h.Write(data)
	return h.Sum(nil)
}

func prfPlus(key, seed []byte, count int) []byte {
	if count <= 0 {
		return nil
	}
	out := make([]byte, 0, count*32)
	prev := []byte{}
	for i := 1; i <= count; i++ {
		buf := append([]byte{}, prev...)
		buf = append(buf, seed...)
		buf = append(buf, byte(i))
		prev = prf(key, buf)
		out = append(out, prev...)
	}
	return out
}

// ============================================================
// 2. X25519 (golang.org/x/crypto/curve25519)
// ============================================================

func x25519Keypair() (priv, pub [32]byte, err error) {
	_, err = rand.Read(priv[:])
	if err != nil {
		return
	}
	priv[0] &= 248
	priv[31] &= 127
	priv[31] |= 64
	curve25519.ScalarBaseMult(&pub, &priv)
	return
}

func x25519Shared(priv *[32]byte, peerPub *[32]byte) [32]byte {
	var s [32]byte
	curve25519.ScalarMult(&s, priv, peerPub)
	return s
}

// ============================================================
// 3. IKEv2 §2.14 SKEYSEED + 7 keys
// ============================================================

type IKEv2Keys struct {
	SKEYSEED []byte
	SK_d     []byte
	SK_pi    []byte
	SK_pr    []byte
	SK_ai    []byte
	SK_ar    []byte
	SK_ei    []byte
	SK_er    []byte
}

func deriveKeys(shared, ni, nr []byte) IKEv2Keys {
	seed := append(ni, nr...)
	skeyseed := prf(seed, shared)
	keys := IKEv2Keys{SKEYSEED: skeyseed}
	keys.SK_d = prfPlus(skeyseed, seed, 1)
	keys.SK_pi = prfPlus(skeyseed, seed, 1)
	keys.SK_pr = prfPlus(skeyseed, seed, 1)
	keys.SK_ai = prfPlus(skeyseed, seed, 1)
	keys.SK_ar = prfPlus(skeyseed, seed, 1)
	keys.SK_ei = prfPlus(skeyseed, seed, 1)
	keys.SK_er = prfPlus(skeyseed, seed, 1)
	return keys
}

type ChildSAKeys struct {
	SK_ei_child []byte // Initiator -> Responder ESP 加密 (16 B for AES-128-GCM)
	SK_ai_child []byte // Initiator -> Responder ESP HMAC
	SK_er_child []byte // Responder -> Initiator ESP 加密
	SK_ar_child []byte // Responder -> Initiator ESP HMAC
}

func deriveChildSAKeys(sk_d, ni, nr []byte) ChildSAKeys {
	seed := append(ni, nr...)
	keymat := prfPlus(sk_d, seed, 2)
	return ChildSAKeys{
		SK_ei_child: keymat[0:16],
		SK_ai_child: keymat[16:32],
		SK_er_child: keymat[32:48],
		SK_ar_child: keymat[48:64],
	}
}

// ============================================================
// 4. AES-128-GCM seal/open
// ============================================================

func aesGCMSeal(key, iv, plaintext, aad []byte) (ciphertext, tag []byte) {
	block, _ := aes.NewCipher(key)
	aesgcm, _ := cipher.NewGCMWithNonceSize(block, 12)
	out := aesgcm.Seal(nil, iv, plaintext, aad)
	return out[:len(out)-16], out[len(out)-16:]
}

func aesGCMOpen(key, iv, ciphertext, tag, aad []byte) ([]byte, bool) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, false
	}
	aesgcm, err := cipher.NewGCMWithNonceSize(block, 12)
	if err != nil {
		return nil, false
	}
	pt, err := aesgcm.Open(nil, iv, append(append([]byte{}, ciphertext...), tag...), aad)
	if err != nil {
		return nil, false
	}
	return pt, true
}

// ============================================================
// 5. ESP packet
// ============================================================

func makeESP(spi, seqno uint32, key, iv []byte, inner []byte) []byte {
	hdr := []byte{
		byte(spi >> 24), byte(spi >> 16), byte(spi >> 8), byte(spi),
		byte(seqno >> 24), byte(seqno >> 16), byte(seqno >> 8), byte(seqno),
	}
	ct, tag := aesGCMSeal(key, iv, inner, nil)
	pkt := make([]byte, 0, len(hdr)+len(iv)+len(ct)+len(tag))
	pkt = append(pkt, hdr...)
	pkt = append(pkt, iv...)
	pkt = append(pkt, ct...)
	pkt = append(pkt, tag...)
	return pkt
}

func parseESP(pkt []byte, key []byte) (spi, seqno uint32, inner []byte, ok bool) {
	if len(pkt) < 8+12+16 {
		return 0, 0, nil, false
	}
	spi = uint32(pkt[0])<<24 | uint32(pkt[1])<<16 | uint32(pkt[2])<<8 | uint32(pkt[3])
	seqno = uint32(pkt[4])<<24 | uint32(pkt[5])<<16 | uint32(pkt[6])<<8 | uint32(pkt[7])
	iv := pkt[8:20]
	ct := pkt[20 : len(pkt)-16]
	tag := pkt[len(pkt)-16:]
	pt, ok := aesGCMOpen(key, iv, ct, tag, nil)
	return spi, seqno, pt, ok
}

// ============================================================
// 6. Demo
// ============================================================

func main() {
	fmt.Println("================================================================")
	fmt.Println(" IKEv2 密钥协商 (RFC 7296) + ESP 数据面 (RFC 4303) — Go")
	fmt.Println("================================================================")

	// Phase 1: IKE_SA_INIT
	fmt.Println("\n[Phase 1] IKE_SA_INIT")
	iPriv, iPub, _ := x25519Keypair()
	rPriv, rPub, _ := x25519Keypair()
	ni := make([]byte, 32)
	nr := make([]byte, 32)
	rand.Read(ni)
	rand.Read(nr)

	sharedI := x25519Shared(&iPriv, &rPub)
	sharedR := x25519Shared(&rPriv, &iPub)

	if sharedI != sharedR {
		panic("DH mismatch!")
	}
	fmt.Printf("  Initiator pub = %x…\n", iPub[:8])
	fmt.Printf("  Responder pub = %x…\n", rPub[:8])
	fmt.Printf("  ECDHE g^ir    = %x…\n", sharedI[:8])

	keys := deriveKeys(sharedI[:], ni, nr)
	fmt.Printf("\n[Key Schedule]\n")
	fmt.Printf("  SKEYSEED = %x…\n", hex.EncodeToString(keys.SKEYSEED)[:16])
	fmt.Printf("  SK_ei (enc init) = %x…\n", hex.EncodeToString(keys.SK_ei)[:16])
	fmt.Printf("  SK_d  (KEYMAT)  = %x…\n", hex.EncodeToString(keys.SK_d)[:16])

	// Phase 2: Child SA
	childKeys := deriveChildSAKeys(keys.SK_d, ni, nr)
	fmt.Println("\n[Phase 2] Child SA keys (AES-128-GCM):")
	fmt.Printf("  SK_ei_child (I→R ESP encrypt, 16 B) = %x…\n", childKeys.SK_ei_child[:8])

	// 构建 ESP 包
	fmt.Println("\n[ESP] Initiator → Responder:")
	spi := uint32(0x12345678)
	seqno := uint32(1)
	iv1 := make([]byte, 12)
	rand.Read(iv1)
	inner := []byte("GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
	esp := makeESP(spi, seqno, childKeys.SK_ei_child, iv1, inner)
	fmt.Printf("  ESP 包大小 = %d B (HDR 8 + IV 12 + CT %d + ICV 16)\n",
		len(esp), len(esp)-36)
	fmt.Printf("  SPI = 0x%08X SeqNo = %d\n", spi, seqno)
	fmt.Printf("  inner payload: %s", inner)

	// Responder 解密
	fmt.Println("\n[ESP] Responder 解密:")
	gotSpi, gotSeq, pt, ok := parseESP(esp, childKeys.SK_ei_child)
	if ok && gotSpi == spi && gotSeq == seqno && string(pt) == string(inner) {
		fmt.Printf("  ✓ SeqNo = %d, decrypted:\n", gotSeq)
		fmt.Printf("    %s", pt)
	} else {
		fmt.Println("  ✗ 解密失败")
	}

	// Rekey
	fmt.Println("\n[Rekey] CREATE_CHILD_SA: 双方新 X25519 (PFS):")
	iPriv2, _, _ := x25519Keypair()
	rPriv2, _, _ := x25519Keypair()
	sharedI2 := x25519Shared(&iPriv2, &rPub)
	sharedR2 := x25519Shared(&rPriv2, &iPub)
	if sharedI2 != sharedR2 {
		panic("Rekey DH mismatch")
	}
	fmt.Printf("  New g^ir = %x… (与第一次独立 → 完美前向保密)\n", sharedI2[:8])

	fmt.Println("\n================================================================")
	fmt.Println(" IKEv2 + ESP 演示完成 ✓")
	fmt.Println("================================================================")
}
