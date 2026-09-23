package main

// HPKE（RFC 9180）的 KEM + 标签化 KDF 层。
// 与 Python 侧共用 RFC 9180 附录 A.1 的向量，便于两侧对拍。
// AES-GCM 只在 Python 侧实现（Go 侧不依赖它也能验证 KEM/KDF/Export 全部数值）。

import (
	"errors"
)

const (
	KEMID  = 0x0020
	KDFID  = 0x0001
	AEADID = 0x0001
	Nk     = 16
	Nn     = 12
	Nh     = 32
)

var (
	hpkeV1     = []byte("HPKE-v1")
	kemSuiteID = append([]byte("KEM"), byte(KEMID>>8), byte(KEMID))
	hpkeSuiteID = append(append([]byte("HPKE"), byte(KEMID>>8), byte(KEMID)),
		byte(KDFID>>8), byte(KDFID), byte(AEADID>>8), byte(AEADID))
)

// ModeBase / ModePSK / ModeAuth / ModeAuthPSK 是四种模式。
const (
	ModeBase = 0x00
	ModePSK  = 0x01
	ModeAuth = 0x02
)

// LabeledExtract / LabeledExpand 是 §4 的标签化 HKDF。
func LabeledExtract(salt, label, ikm, suiteID []byte) []byte {
	in := append([]byte{}, hpkeV1...)
	in = append(in, suiteID...)
	in = append(in, label...)
	in = append(in, ikm...)
	return HKDFExtract(salt, in)
}

func LabeledExpand(prk, label, info []byte, length int, suiteID []byte) []byte {
	in := []byte{byte(length >> 8), byte(length)}
	in = append(in, hpkeV1...)
	in = append(in, suiteID...)
	in = append(in, label...)
	in = append(in, info...)
	return HKDFExpand(prk, in, length)
}

// ExtractAndExpand 是 §4.1 DHKEM 的共享秘密派生。
func ExtractAndExpand(dh, kemContext []byte) []byte {
	eaePrk := LabeledExtract([]byte{}, []byte("eae_prk"), dh, kemSuiteID)
	return LabeledExpand(eaePrk, []byte("shared_secret"), kemContext, Nh, kemSuiteID)
}

// Encap 是 §4.1 的封装：返回 (shared_secret, enc)。
func Encap(pkR, skE []byte) ([]byte, []byte) {
	pkE := X25519Base(skE)
	dh := X25519(skE, pkR)
	kemContext := append(append([]byte{}, pkE...), pkR...)
	return ExtractAndExpand(dh, kemContext), pkE
}

// Decap 是 §4.1 的解封装。
func Decap(enc, skR []byte) []byte {
	pkR := X25519Base(skR)
	dh := X25519(skR, enc)
	kemContext := append(append([]byte{}, enc...), pkR...)
	return ExtractAndExpand(dh, kemContext)
}

// KeyScheduleResult 是 §5.1 的派生结果。
type KeyScheduleResult struct {
	Key               []byte
	BaseNonce         []byte
	ExporterSecret    []byte
	Secret            []byte
	KeyScheduleCtx    []byte
}

// VerifyPSKInputs 是 §5.1：判据是「是否等于默认空串」，不是「是否为 nil」。
func VerifyPSKInputs(mode int, psk, pskID []byte) error {
	gotPSK := len(psk) != 0
	gotID := len(pskID) != 0
	if gotPSK != gotID {
		return errors.New("inconsistent PSK inputs")
	}
	if gotPSK && (mode == ModeBase || mode == ModeAuth) {
		return errors.New("PSK input provided when not needed")
	}
	return nil
}

// KeySchedule 是 §5.1。
func KeySchedule(mode int, sharedSecret, info, psk, pskID []byte) (*KeyScheduleResult, error) {
	if err := VerifyPSKInputs(mode, psk, pskID); err != nil {
		return nil, err
	}
	pskIDHash := LabeledExtract([]byte{}, []byte("psk_id_hash"), pskID, hpkeSuiteID)
	infoHash := LabeledExtract([]byte{}, []byte("info_hash"), info, hpkeSuiteID)
	ksc := append([]byte{byte(mode)}, pskIDHash...)
	ksc = append(ksc, infoHash...)
	secret := LabeledExtract(sharedSecret, []byte("secret"), psk, hpkeSuiteID)
	return &KeyScheduleResult{
		Key:            LabeledExpand(secret, []byte("key"), ksc, Nk, hpkeSuiteID),
		BaseNonce:      LabeledExpand(secret, []byte("base_nonce"), ksc, Nn, hpkeSuiteID),
		ExporterSecret: LabeledExpand(secret, []byte("exp"), ksc, Nh, hpkeSuiteID),
		Secret:         secret,
		KeyScheduleCtx: ksc,
	}, nil
}

// Context 是 §5.2 的加密上下文（这里只保留与 AEAD 无关的部分）。
type Context struct {
	BaseNonce      []byte
	ExporterSecret []byte
	Seq            uint64
}

// ComputeNonce 是 base_nonce XOR I2OSP(seq, 12)。
func (c *Context) ComputeNonce(seq uint64) []byte {
	sb := make([]byte, Nn)
	for i := 0; i < Nn; i++ {
		sb[Nn-1-i] = byte(seq >> (8 * i))
	}
	out := make([]byte, Nn)
	for i := 0; i < Nn; i++ {
		out[i] = c.BaseNonce[i] ^ sb[i]
	}
	return out
}

// Export 是 §5.3 的秘密导出，标签是 "sec"。
func (c *Context) Export(exporterContext []byte, length int) []byte {
	return LabeledExpand(c.ExporterSecret, []byte("sec"), exporterContext, length, hpkeSuiteID)
}
