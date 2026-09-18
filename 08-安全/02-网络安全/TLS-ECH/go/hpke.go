// hpke.go —— HPKE base mode（RFC 9180）：HKDF 标签化、DHKEM(X25519) 与密钥计划
//
// 套件固定为 §7.1 的 DHKEM(X25519, HKDF-SHA256) + HKDF-SHA256 + ChaCha20-Poly1305
// （kem_id=0x0020 / kdf_id=0x0001 / aead_id=0x0003），正好对上附录 A.2 的官方向量。
//
// 三个最容易抄错的地方，全部在下面显式写死：
//
//  1. **两条 suite_id 长度不同**：KEM 的是 "KEM"|kem_id（5 字节），HPKE 的是
//     "HPKE"|kem_id|kdf_id|aead_id（10 字节）。用错不会报错，只会让密钥完全不同。
//  2. **标签化前后缀不对称**：LabeledExtract 是 Extract(salt, "HPKE-v1"|suite_id|label|ikm)，
//     而 LabeledExpand 把 I2OSP(L,2) 放在**最前面**。
//  3. **密钥计划里三个 LabeledExtract 的盐不同**：psk_id_hash / info_hash 用空串，
//     secret 用 shared_secret。
//
// 另附 TLS 1.3 的 HKDF-Expand-Label（RFC 8446 §7.1）：它和 LabeledExpand 长得像但编码
// 完全不同（HkdfLabel + "tls13 " 前缀，没有 "HPKE-v1"/suite_id），ECH 的
// accept_confirmation 用的是前者。
package main

import (
	"crypto/ecdh"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"errors"
)

const (
	hpkeNH      = 32 // SHA-256 输出长度
	hpkeNK      = 32 // ChaCha20-Poly1305 密钥
	hpkeNN      = 12 // nonce
	hpkeNSecret = 32 // DHKEM 的 shared_secret

	kemIDX25519            = 0x0020
	kdfIDHKDFSHA256        = 0x0001
	aeadIDChaCha20Poly1305 = 0x0003
	modeBase               = 0x00
)

var (
	errHpkeAuth = errors.New("hpke: AEAD 认证失败")

	kemSuiteID  = append([]byte("KEM"), 0x00, 0x20)
	hpkeSuiteID = append([]byte("HPKE"), 0x00, 0x20, 0x00, 0x01, 0x00, 0x03)
)

// ---------------------------------------------------------------- HKDF（RFC 5869）

func hkdfExtract(salt, ikm []byte) []byte {
	if len(salt) == 0 {
		salt = make([]byte, sha256.Size) // 空盐按 Hash.length 个零字节处理
	}
	m := hmac.New(sha256.New, salt)
	m.Write(ikm)
	return m.Sum(nil)
}

func hkdfExpand(prk, info []byte, length int) []byte {
	out := make([]byte, 0, length)
	var t []byte
	for ctr := byte(1); len(out) < length; ctr++ {
		m := hmac.New(sha256.New, prk)
		m.Write(t)
		m.Write(info)
		m.Write([]byte{ctr})
		t = m.Sum(nil)
		out = append(out, t...)
	}
	return out[:length]
}

func labeledExtract(salt, suiteID []byte, label string, ikm []byte) []byte {
	buf := append([]byte("HPKE-v1"), suiteID...)
	buf = append(buf, label...)
	buf = append(buf, ikm...)
	return hkdfExtract(salt, buf)
}

func labeledExpand(prk, suiteID []byte, label string, info []byte, length int) []byte {
	buf := make([]byte, 0, 2+7+len(suiteID)+len(label)+len(info))
	buf = append(buf, byte(length>>8), byte(length)) // I2OSP(L,2) 在最前
	buf = append(buf, "HPKE-v1"...)
	buf = append(buf, suiteID...)
	buf = append(buf, label...)
	buf = append(buf, info...)
	return hkdfExpand(prk, buf, length)
}

// ---------------------------------------------------------------- DHKEM（§4.1 + §7.1.3）

func x25519Base() []byte {
	b := make([]byte, 32)
	b[0] = 9
	return b
}

// x25519 —— 私钥的 clamping 由 crypto/ecdh 在标量乘里完成（RFC 7748 §5）
func x25519(sk, pk []byte) ([]byte, error) {
	priv, err := ecdh.X25519().NewPrivateKey(sk)
	if err != nil {
		return nil, err
	}
	pub, err := ecdh.X25519().NewPublicKey(pk)
	if err != nil {
		return nil, err
	}
	return priv.ECDH(pub)
}

func kemExtractAndExpand(dh, kemContext []byte) []byte {
	eaePrk := labeledExtract(nil, kemSuiteID, "eae_prk", dh)
	return labeledExpand(eaePrk, kemSuiteID, "shared_secret", kemContext, hpkeNSecret)
}

// deriveKeyPair —— §7.1.3 对 X25519 不做拒绝采样，直接 LabeledExpand 出 sk
func deriveKeyPair(ikm []byte) (sk, pk []byte, err error) {
	dkpPrk := labeledExtract(nil, kemSuiteID, "dkp_prk", ikm)
	sk = labeledExpand(dkpPrk, kemSuiteID, "sk", nil, 32)
	pk, err = x25519(sk, x25519Base())
	return sk, pk, err
}

// encap —— 返回的 enc 是**发送方**的临时公钥，不是接收方的；ikm 为空则用 crypto/rand
func encap(pkR, ikm []byte) (enc, ss []byte, err error) {
	var skE []byte
	if ikm != nil {
		skE, enc, err = deriveKeyPair(ikm)
	} else {
		skE = make([]byte, 32)
		if _, err = rand.Read(skE); err != nil {
			return nil, nil, err
		}
		enc, err = x25519(skE, x25519Base())
	}
	if err != nil {
		return nil, nil, err
	}
	dh, err := x25519(skE, pkR)
	if err != nil {
		return nil, nil, err
	}
	kemCtx := append(append([]byte{}, enc...), pkR...)
	return enc, kemExtractAndExpand(dh, kemCtx), nil
}

func decap(enc, skR, pkR []byte) ([]byte, error) {
	dh, err := x25519(skR, enc)
	if err != nil {
		return nil, err
	}
	kemCtx := append(append([]byte{}, enc...), pkR...)
	return kemExtractAndExpand(dh, kemCtx), nil
}

// ---------------------------------------------------------------- 密钥计划与上下文（§5.1 / §6）

type keySchedule struct {
	KeyScheduleContext []byte
	Secret             []byte
	Key                []byte
	BaseNonce          []byte
	ExporterSecret     []byte
}

func newKeySchedule(sharedSecret, info []byte) *keySchedule {
	pskIDHash := labeledExtract(nil, hpkeSuiteID, "psk_id_hash", nil)
	infoHash := labeledExtract(nil, hpkeSuiteID, "info_hash", info)
	ksc := append([]byte{modeBase}, pskIDHash...)
	ksc = append(ksc, infoHash...)
	secret := labeledExtract(sharedSecret, hpkeSuiteID, "secret", nil)
	return &keySchedule{
		KeyScheduleContext: ksc,
		Secret:             secret,
		Key:                labeledExpand(secret, hpkeSuiteID, "key", ksc, hpkeNK),
		BaseNonce:          labeledExpand(secret, hpkeSuiteID, "base_nonce", ksc, hpkeNN),
		ExporterSecret:     labeledExpand(secret, hpkeSuiteID, "exp", ksc, hpkeNH),
	}
}

type hpkeContext struct {
	ks  *keySchedule
	Seq uint64
}

// nonce = base_nonce XOR I2OSP(序号, Nn)，序号每 Seal/Open 一次自增
func (c *hpkeContext) nonce() []byte {
	n := append([]byte(nil), c.ks.BaseNonce...)
	for i := 0; i < 8; i++ {
		n[hpkeNN-1-i] ^= byte(c.Seq >> (8 * uint(i)))
	}
	return n
}

func (c *hpkeContext) Seal(aad, pt []byte) ([]byte, error) {
	out, err := aeadSeal(c.ks.Key, c.nonce(), aad, pt)
	if err != nil {
		return nil, err
	}
	c.Seq++
	return out, nil
}

func (c *hpkeContext) Open(aad, sealed []byte) ([]byte, error) {
	out, err := aeadOpen(c.ks.Key, c.nonce(), aad, sealed)
	if err != nil {
		return nil, err
	}
	c.Seq++
	return out, nil
}

func (c *hpkeContext) Export(ctxInfo []byte, length int) []byte {
	return labeledExpand(c.ks.ExporterSecret, hpkeSuiteID, "sec", ctxInfo, length)
}

func setupBaseS(pkR, info, ikm []byte) (enc []byte, ctx *hpkeContext, err error) {
	enc, ss, err := encap(pkR, ikm)
	if err != nil {
		return nil, nil, err
	}
	return enc, &hpkeContext{ks: newKeySchedule(ss, info)}, nil
}

func setupBaseR(enc, skR, pkR, info []byte) (*hpkeContext, error) {
	ss, err := decap(enc, skR, pkR)
	if err != nil {
		return nil, err
	}
	return &hpkeContext{ks: newKeySchedule(ss, info)}, nil
}

// ---------------------------------------------------------------- TLS 1.3 版（RFC 8446 §7.1）

func tlsExpandLabel(secret []byte, label string, context []byte, length int) []byte {
	buf := make([]byte, 0, 3+6+len(label)+1+len(context))
	buf = append(buf, byte(length>>8), byte(length))
	buf = append(buf, byte(len(label)+6)) // "tls13 " 前缀计入长度
	buf = append(buf, "tls13 "...)
	buf = append(buf, label...)
	buf = append(buf, byte(len(context)))
	buf = append(buf, context...)
	return hkdfExpand(secret, buf, length)
}
