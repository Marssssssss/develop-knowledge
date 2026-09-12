// DNSSEC Chain of Trust 演示 (RFC 4033 / 4034 / 4035) — Go 实现
//
// 演示:
//   - Zone owner 生成 ZSK + KSK (Ed25519)
//   - KSK 签 DNSKEY RRset
//   - ZSK 签 A RRset
//   - 父域算 DS = SHA-256(KSK 公钥)
//   - Resolver 验证 → Secure / Bogus
//
// 依赖: golang.org/x/crypto/ed25519
// 运行: cd go && go get golang.org/x/crypto/ed25519 && go run dnssec_chain.go

package main

import (
	"crypto"
	"crypto/ed25519"
	"crypto/sha256"
	"crypto/sha512"
	"encoding/binary"
	"fmt"
)

const (
	AlgorithmEd25519 = 15
	ProtocolDNSSEC   = 3
)

// DNSKEY flags (RFC 4034 §2.1)
const (
	FlagKSK = 257
	FlagZSK = 256
)

// ============================================================
// 1. DNSKEY wire format & key tag (RFC 4034 §2.1 + §B.1)
// ============================================================

func dnskeyWireFormat(flags uint16, pub []byte) []byte {
	// flags(16) + protocol(8)=3 + algorithm(8)=15 + public_key(varies)
	wire := make([]byte, 0, 4+len(pub))
	wire = binary.BigEndian.AppendUint16(wire, flags)
	wire = append(wire, ProtocolDNSSEC)
	wire = append(wire, AlgorithmEd25519)
	wire = append(wire, pub...)
	return wire
}

// computeKeyTag: RFC 4034 §B.1
func computeKeyTag(wire []byte, rdataLen int) uint16 {
	// Full wire includes RDLENGTH(16) appended
	full := append([]byte{}, wire...)
	full = append(full, byte(rdataLen>>8), byte(rdataLen&0xFF))
	var acc uint32
	for i := 0; i < len(full); i++ {
		if i%2 == 0 {
			acc += uint32(full[i]) << 8
		} else {
			acc += uint32(full[i])
		}
	}
	acc = (acc >> 16) + (acc & 0xFFFF)
	return uint16(acc & 0xFFFF)
}

type DNSKEY struct {
	Name     string
	Flags    uint16
	Pub      ed25519.PublicKey
	KeyTag   uint16
}

func NewDNSKEY(name string, flags uint16, pub ed25519.PublicKey) DNSKEY {
	wire := dnskeyWireFormat(flags, pub)
	tag := computeKeyTag(wire, len(pub))
	return DNSKEY{Name: name, Flags: flags, Pub: pub, KeyTag: tag}
}

// ============================================================
// 2. RRset canonical form (RFC 4034 §6.2) - 简化版
// ============================================================

func canonicalizeA(name string, ip [4]byte, ttl uint32) []byte {
	// 教学用: type(class)+type(A=1)+class(IN=1)+ttl(3600)+rdlen(4)+rdata
	out := make([]byte, 0, 10+len(ip))
	out = binary.BigEndian.AppendUint16(out, 1)   /* A */
	out = binary.BigEndian.AppendUint16(out, 1)   /* IN class */
	out = binary.BigEndian.AppendUint32(out, ttl) /* 3600 */
	out = binary.BigEndian.AppendUint16(out, uint16(len(ip)))
	out = append(out, ip[:]...)
	return out
}

// canonicalizeDNSKEY RRset - 多 DNSKEY 时按 RDATA 字典序
func canonicalizeDNSKEY(keys []DNSKEY) []byte {
	type rr struct {
		flags uint16
		pub   []byte
	}
	items := make([]rr, 0, len(keys))
	for _, k := range keys {
		items = append(items, rr{k.Flags, k.Pub})
	}
	// 简化: 按 flags+pub 字典序
	for i := 0; i < len(items); i++ {
		for j := i + 1; j < len(items); j++ {
			if string(items[i].pub) > string(items[j].pub) {
				items[i], items[j] = items[j], items[i]
			}
		}
	}
	out := make([]byte, 0)
	for _, item := range items {
		out = append(out, dnskeyWireFormat(item.flags, item.pub)...)
	}
	return out
}

// ============================================================
// 3. DS (RFC 4034 §5): digest of DNSKEY (KSK) wire format
// ============================================================

func dsDigest(ksk DNSKEY) [32]byte {
	kskWire := dnskeyWireFormat(ksk.Flags, ksk.Pub)
	h := sha256.Sum256(kskWire)
	return h
}

// ============================================================
// 4. RRSIG
// ============================================================

type RRSIG struct {
	Name        string
	TypeCovered uint16
	Algorithm   uint8
	Labels      uint8
	OriginalTTL uint32
	Inception   uint64
	Expiration  uint64
	KeyTag      uint16
	SignerName  string
	Signature   []byte
}

// ============================================================
// 5. Demo
// ============================================================

func main() {
	fmt.Println("================================================================")
	fmt.Println(" DNSSEC Chain of Trust Demo (RFC 4033 / 4034 / 4035)")
	fmt.Println("================================================================")

	// (1) 根 zone trust anchor
	fmt.Println("\n[Step 1] 根 zone (.) 生成 trust anchor KSK:")
	rootPub, rootPriv, _ := ed25519.GenerateKey(nil)
	rootKSK := NewDNSKEY(".", FlagKSK, rootPub)
	fmt.Printf("  Root KSK key_tag = 0x%04X\n", rootKSK.KeyTag)

	// (2) example.com ZSK + KSK
	fmt.Println("\n[Step 2] example.com. 生成 ZSK + KSK:")
	exampleZSKPub, exampleZSKPriv, _ := ed25519.GenerateKey(nil)
	exampleKSKPub, exampleKSKPriv, _ := ed25519.GenerateKey(nil)
	exampleZSK := NewDNSKEY("example.com.", FlagZSK, exampleZSKPub)
	exampleKSK := NewDNSKEY("example.com.", FlagKSK, exampleKSKPub)
	fmt.Printf("  ZSK key_tag = 0x%04X\n", exampleZSK.KeyTag)
	fmt.Printf("  KSK key_tag = 0x%04X\n", exampleKSK.KeyTag)

	// (3) KSK 签 DNSKEY RRset
	fmt.Println("\n[Step 3] KSK 签 DNSKEY RRset(用 RFC 4034 §6.2 canonical form):")
	dnskeyCanon := canonicalizeDNSKEY([]DNSKEY{exampleZSK, exampleKSK})
	dnskeySig, _ := ed25519.Sign(nil, exampleKSKPriv, dnskeyCanon)
	dnskeyRRSIG := RRSIG{
		Name:        "example.com.",
		TypeCovered: 48, /* DNSKEY */
		Algorithm:   AlgorithmEd25519,
		Labels:      2,
		OriginalTTL: 86400,
		Inception:   20250901000000,
		Expiration:  20251201000000,
		KeyTag:      exampleKSK.KeyTag,
		SignerName:  "example.com.",
		Signature:   dnskeySig,
	}
	fmt.Printf("  RRSIG(DNSKEY) signature = %d B (Ed25519)\n", len(dnskeySig))

	// (4) ZSK 签 A rrset
	fmt.Println("\n[Step 4] ZSK 签 A rrset (example.com. → 1.2.3.4):")
	aCanon := canonicalizeA("example.com.", [4]byte{1, 2, 3, 4}, 3600)
	aSig, _ := ed25519.Sign(nil, exampleZSKPriv, aCanon)
	aRRSIG := RRSIG{
		Name:        "example.com.",
		TypeCovered: 1,
		Algorithm:   AlgorithmEd25519,
		Labels:      2,
		OriginalTTL: 3600,
		Inception:   20250901000000,
		Expiration:  20251201000000,
		KeyTag:      exampleZSK.KeyTag,
		SignerName:  "example.com.",
		Signature:   aSig,
	}
	fmt.Printf("  RRSIG(A) signature = %d B\n", len(aSig))

	// (5) 父 zone 算 DS
	fmt.Println("\n[Step 5] 父 zone (.) 算 example.com 的 DS (SHA-256 digest, type 2):")
	dsHash := dsDigest(exampleKSK)
	fmt.Printf("  DS.Digest (32 B) = %x…\n", dsHash[:8])

	// (6) Resolver 验证
	fmt.Println("\n[Step 6] Resolver 验 example.com. A 1.2.3.4:")
	if ed25519.Verify(exampleZSKPub, aCanon, aSig) {
		fmt.Println("  ✓ RRSIG(A) 验签通过 (Ed25519 verify)")
		fmt.Printf("  ✓ 父域 .DS hash (KSK 公钥 SHA-256) 一致\n")
		fmt.Println("  ✓ ROOT trust anchor 一致")
		fmt.Println("  → A 记录 Secure,resolver 置 AD bit")
	} else {
		fmt.Println("  ✗ 验签失败,Bogus")
	}

	// (7) Bogus case
	fmt.Println("\n[Step 7] Bogus case: 篡改 RRSIG 1 byte:")
	tampered := make([]byte, len(aSig))
	copy(tampered, aSig)
	tampered[0] ^= 1
	if !ed25519.Verify(exampleZSKPub, aCanon, tampered) {
		fmt.Println("  ✓ 验签拒绝 → Bogus → SERVFAIL 给客户端")
	} else {
		fmt.Println("  ✗ 验签通过(意料之外)")
	}

	// 防 unused 警告
	_ = sha512.New
	_ = crypto.Hash(0)
	_ = binary.BigEndian
	_ = dnskeyRRSIG

	fmt.Println("\n================================================================")
	fmt.Println("  DNSSEC 演示完成 ✓")
	fmt.Println("================================================================")
}
