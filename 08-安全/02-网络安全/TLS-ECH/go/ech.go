// ech.go —— ECH 的客户端 / 服务端流程与接受确认（RFC 9849 §6、§7）
//
// 客户端（§6.1）：先按密文长度填等长零的占位 payload 算出 ClientHelloOuterAAD，
// 再 final = HPKE.Seal(AAD, EncodedClientHelloInner)，最后把占位换成真密文 ——
// 两段等长，所以不用重算任何长度前缀。
//
// 服务端（§7.1）：把 payload 换成等长零重建 AAD → Open → 校验尾部填充全零 →
// 展开 ech_outer_extensions → legacy_session_id 从 ClientHelloOuter 抄回来。
//
// 错误用哨兵值区分原因，自检里用 errors.Is 判 —— 与 C 版的返回码、Python 版的
// 异常类型一一对应（见 README 的跨语言一致性表）。
package main

import (
	"crypto/sha256"
	"crypto/subtle"
	"errors"
)

var (
	errEchParse    = errors.New("ech: 解析或 AAD 构造失败")
	errEchConfigID = errors.New("ech: config_id 或套件不匹配（服务端应换下一个 ECHConfig）")
)

func (ch *clientHello) setOuterExt(o *outerExt) error {
	raw, err := encodeOuterExt(o)
	if err != nil {
		return err
	}
	ch.setExt(echExtType, raw)
	return nil
}

// clientEncrypt —— §6.1.1~§6.1.3；ikm 传 nil 时用 crypto/rand 生成临时密钥
func clientEncrypt(inner, outerTpl *clientHello, cfg *echConfig,
	compressNames []uint16) (*clientHello, *hpkeContext, []byte, error) {
	if cfg.KeyConfig.KemID != kemIDX25519 || len(cfg.KeyConfig.CipherSuites) == 0 {
		return nil, nil, nil, errEchParse
	}
	suite := cfg.KeyConfig.CipherSuites[0]
	if suite[0] != kdfIDHKDFSHA256 || suite[1] != aeadIDChaCha20Poly1305 {
		return nil, nil, nil, errEchParse
	}
	squeezed, err := compressInner(inner, outerTpl, compressNames)
	if err != nil {
		return nil, nil, nil, err
	}
	pad, err := namePadding(inner, int(cfg.MaxNameLength))
	if err != nil {
		return nil, nil, nil, err
	}
	encoded, err := encodedClientHelloInner(squeezed, pad)
	if err != nil {
		return nil, nil, nil, err
	}
	encoded = append(encoded, make([]byte, roundTo32(len(encoded)))...)

	enc, ctx, err := setupBaseS(cfg.KeyConfig.PublicKey, nil, nil)
	if err != nil {
		return nil, nil, nil, err
	}

	// 占位：与密文等长的零，写进 AAD 用的那一份 ClientHelloOuter
	filler := len(encoded) + aeadTagLen
	aadCh := outerTpl.copy()
	if err := aadCh.setOuterExt(newOuterExt(cfg, enc, make([]byte, filler))); err != nil {
		return nil, nil, nil, err
	}
	aad, err := aadCh.Encode()
	if err != nil {
		return nil, nil, nil, err
	}
	sealed, err := ctx.Seal(aad, encoded)
	if err != nil {
		return nil, nil, nil, err
	}
	if len(sealed) != filler {
		return nil, nil, nil, errEchParse
	}
	out := outerTpl.copy()
	if err := out.setOuterExt(newOuterExt(cfg, enc, sealed)); err != nil {
		return nil, nil, nil, err
	}
	return out, ctx, encoded, nil
}

func newOuterExt(cfg *echConfig, enc, payload []byte) *outerExt {
	suite := cfg.KeyConfig.CipherSuites[0]
	return &outerExt{
		KdfID:    suite[0],
		AeadID:   suite[1],
		ConfigID: cfg.KeyConfig.ConfigID,
		Enc:      enc,
		Payload:  payload,
	}
}

func serverDecrypt(outer *clientHello, skR, pkR []byte, cfg *echConfig) (*clientHello, error) {
	at := outer.extIndex(echExtType)
	if at < 0 {
		return nil, errEchParse
	}
	o, err := decodeOuterExt(outer.Exts[at].Data)
	if err != nil {
		return nil, errEchParse
	}
	if o.ConfigID != cfg.KeyConfig.ConfigID {
		return nil, errEchConfigID // 试解密失败：服务端换下一个 ECHConfig 再试
	}
	if o.KdfID != kdfIDHKDFSHA256 || o.AeadID != aeadIDChaCha20Poly1305 ||
		len(o.Enc) != 32 || len(o.Payload) < aeadTagLen {
		return nil, errEchConfigID
	}
	ctx, err := setupBaseR(o.Enc, skR, pkR, nil)
	if err != nil {
		return nil, errEchParse
	}

	// 重建 AAD：payload 换成等长的零，长度前缀一个字节都不动
	aadCh := outer.copy()
	if err := aadCh.setOuterExt(&outerExt{
		KdfID: o.KdfID, AeadID: o.AeadID, ConfigID: o.ConfigID, Enc: o.Enc,
		Payload: make([]byte, len(o.Payload)),
	}); err != nil {
		return nil, errEchParse
	}
	aad, err := aadCh.Encode()
	if err != nil {
		return nil, errEchParse
	}
	plain, err := ctx.Open(aad, o.Payload)
	if err != nil {
		return nil, errHpkeAuth // 认证失败 = ClientHelloOuter 被改过
	}
	_, consumed, err := decodeClientHello(plain)
	if err != nil {
		return nil, errEchParse
	}
	for _, b := range plain[consumed:] {
		if b != 0 {
			return nil, errEchPadding // §5.1：填充必须全零
		}
	}
	got, err := decompressInner(plain[:consumed], outer)
	if err != nil {
		return nil, err
	}
	if got.extIndex(echExtType) < 0 {
		return nil, errEchParse
	}
	got.SessionID = clone(outer.SessionID)
	return got, nil
}

// ---------------------------------------------------------------- 接受确认（§7.2）

// acceptConfirmation —— HKDF-Extract(0, ClientHelloInner.random) 之后再走
// RFC 8446 §7.1 的 HKDF-Expand-Label（**不是** HPKE 的 LabeledExpand）
func acceptConfirmation(innerRandom, transcript []byte) []byte {
	return tlsExpandLabel(hkdfExtract(nil, innerRandom), "ech accept confirmation",
		transcript, 8)
}

func hrrAcceptConfirmation(innerRandom, transcript []byte) []byte {
	return tlsExpandLabel(hkdfExtract(nil, innerRandom), "hrr ech accept confirmation",
		transcript, 8)
}

func transcriptHash(messages ...[]byte) []byte {
	h := sha256.New()
	for _, m := range messages {
		h.Write(m)
	}
	return h.Sum(nil)
}

func constantTimeEq(a, b []byte) bool { return subtle.ConstantTimeCompare(a, b) == 1 }
