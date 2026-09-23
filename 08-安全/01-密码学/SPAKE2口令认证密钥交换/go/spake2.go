package main

// SPAKE2（RFC 9382）P256-SHA256-HKDF-HMAC 套件。

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"math/big"
)

const (
	hashLen       = 32
	fieldLen      = 32
	confirmInfo   = "ConfirmationKeys"
	p256MCompressed = "02886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f"
	p256NCompressed = "03d8bbd6c639c62937b04d997f38c3770719c629d7014d49a24b4f98baa1292b49"
)

var (
	spakeM, spakeN *Point
	spakeH         = big.NewInt(int64(p256H))
)

func init() {
	var err error
	spakeM, err = p256Decode(unhexOrPanic(p256MCompressed))
	if err != nil {
		panic(err)
	}
	spakeN, err = p256Decode(unhexOrPanic(p256NCompressed))
	if err != nil {
		panic(err)
	}
}

// ------------------------------------------------------------------ HKDF/HMAC

func hkdfExtract(salt, ikm []byte) []byte {
	if salt == nil {
		salt = make([]byte, hashLen)
	}
	m := hmac.New(sha256.New, salt)
	m.Write(ikm)
	return m.Sum(nil)
}

func hkdfExpand(prk, info []byte, length int) ([]byte, error) {
	if length > 255*hashLen {
		return nil, errors.New("hkdf: output too long")
	}
	out := make([]byte, 0, length)
	t := []byte{}
	for counter := byte(1); len(out) < length; counter++ {
		m := hmac.New(sha256.New, prk)
		m.Write(t)
		m.Write(info)
		m.Write([]byte{counter})
		t = m.Sum(nil)
		out = append(out, t...)
	}
	return out[:length], nil
}

func spakeKDF(ikm, salt, info []byte, length int) ([]byte, error) {
	return hkdfExpand(hkdfExtract(salt, ikm), info, length)
}

func spakeMAC(key, msg []byte) []byte {
	m := hmac.New(sha256.New, key)
	m.Write(msg)
	return m.Sum(nil)
}

// ------------------------------------------------------------------ transcript

func lenPrefix(s []byte) []byte {
	b := make([]byte, 8)
	binary.LittleEndian.PutUint64(b, uint64(len(s))) // RFC 9382 §3.2：8 字节小端
	return b
}

func encodeW(w *big.Int) []byte {
	b := make([]byte, fieldLen)
	w.FillBytes(b) // 大端、左补零到 p 的字节长度
	return b
}

func buildTT(identA, identB, pA, pB, K []byte, w *big.Int) []byte {
	we := encodeW(w)
	out := []byte{}
	out = append(out, lenPrefix(identA)...)
	out = append(out, identA...)
	out = append(out, lenPrefix(identB)...)
	out = append(out, identB...)
	out = append(out, lenPrefix(pA)...)
	out = append(out, pA...)
	out = append(out, lenPrefix(pB)...)
	out = append(out, pB...)
	out = append(out, lenPrefix(K)...)
	out = append(out, K...)
	out = append(out, lenPrefix(we)...)
	out = append(out, we...)
	return out
}

// ------------------------------------------------------------------ 协议

// Party 是参与方；role 为 "A" 或 "B"。
type Party struct {
	Role                 string
	IdentSelf, IdentPeer string
	W, Rand              *big.Int
	Pub                  []byte
	K                    []byte
	TT, Ke, Ka           []byte
	KcSelf, KcPeer       []byte
	Confirm              []byte
}

func (p *Party) blind() *Point {
	if p.Role == "A" {
		return spakeM
	}
	return spakeN
}

// Start 计算并发布 pA = w*M + X（B 侧用 N）。
func (p *Party) Start() []byte {
	p.W = new(big.Int).Mod(p.W, p256N)
	p.Rand = new(big.Int).Mod(p.Rand, p256N)
	X := p256Mul(p.Rand, p256G())
	pub := p256Add(p256Mul(p.W, p.blind()), X)
	p.Pub = p256Encode(pub)
	return p.Pub
}

// Finish 用对端的公开值算 K，再走 §4 的密钥调度。
func (p *Party) Finish(peerPub, aad []byte) ([]byte, []byte, error) {
	var blind *Point
	if p.Role == "A" {
		blind = spakeN
	} else {
		blind = spakeM
	}
	peer, err := p256Decode(peerPub)
	if err != nil {
		return nil, nil, err
	}
	inner := p256Add(peer, p256Neg(p256Mul(p.W, blind)))
	kpt := p256Mul(p.Rand, inner)
	p.K = p256Encode(p256Mul(spakeH, kpt))

	if p.Role == "A" {
		p.TT = buildTT([]byte(p.IdentSelf), []byte(p.IdentPeer), p.Pub, peerPub, p.K, p.W)
	} else {
		p.TT = buildTT([]byte(p.IdentPeer), []byte(p.IdentSelf), peerPub, p.Pub, p.K, p.W)
	}
	digest := sha256.Sum256(p.TT)
	p.Ke = digest[:16]
	p.Ka = digest[16:]
	info := append([]byte(confirmInfo), aad...)
	kc, err := spakeKDF(p.Ka, []byte{}, info, 32)
	if err != nil {
		return nil, nil, err
	}
	if p.Role == "A" {
		p.KcSelf, p.KcPeer = kc[:16], kc[16:]
	} else {
		p.KcSelf, p.KcPeer = kc[16:], kc[:16]
	}
	p.Confirm = spakeMAC(p.KcSelf, p.TT)
	return p.Ke, p.Confirm, nil
}

// ConfirmExpected 期望从对端收到的确认消息。
func (p *Party) ConfirmExpected() []byte { return spakeMAC(p.KcPeer, p.TT) }

// Verify 常数时间比对。
func (p *Party) Verify(peerConfirm []byte) bool {
	return hmac.Equal(p.ConfirmExpected(), peerConfirm)
}

func spakeRun(identA, identB string, w, x, y *big.Int, aad []byte) ([]byte, []byte, []byte, bool, error) {
	a := &Party{Role: "A", IdentSelf: identA, IdentPeer: identB, W: w, Rand: x}
	b := &Party{Role: "B", IdentSelf: identB, IdentPeer: identA, W: w, Rand: y}
	pA := a.Start()
	pB := b.Start()
	if _, _, err := a.Finish(pB, aad); err != nil {
		return nil, nil, nil, false, err
	}
	if _, _, err := b.Finish(pA, aad); err != nil {
		return nil, nil, nil, false, err
	}
	return a.Ke, a.Confirm, b.Confirm, a.Verify(b.Confirm) && b.Verify(a.Confirm), nil
}
