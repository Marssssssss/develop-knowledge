// TLS 1.3 密钥调度（RFC 8446 §7.1-§7.3）的 Go 实现。
//
// 哈希/HMAC 使用标准库 crypto/sha256 与 crypto/hmac；HKDF-Extract/Expand、
// HKDF-Expand-Label、Derive-Secret 与五层 Secret 调度按 RFC 8446 逐条实现，
// 不引入任何第三方依赖。
package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"

	"fmt"
)

const hashLen = 32

var zero = make([]byte, hashLen)

func hkdfExtract(salt, ikm []byte) []byte {
	m := hmac.New(sha256.New, salt)
	m.Write(ikm)
	return m.Sum(nil)
}

func hkdfExpand(prk, info []byte, length int) []byte {
	out := make([]byte, 0, length)
	t := []byte{}
	for i := byte(1); len(out) < length; i++ {
		m := hmac.New(sha256.New, prk)
		m.Write(t)
		m.Write(info)
		m.Write([]byte{i})
		t = m.Sum(nil)
		out = append(out, t...)
	}
	return out[:length]
}

// HkdfLabel = uint16 length || len(label) || "tls13 "+Label || len(context) || context
func hkdfLabel(length int, label string, context []byte) []byte {
	lab := append([]byte("tls13 "), []byte(label)...)
	buf := []byte{byte(length >> 8), byte(length)}
	buf = append(buf, byte(len(lab)))
	buf = append(buf, lab...)
	buf = append(buf, byte(len(context)))
	return append(buf, context...)
}

func expandLabel(secret []byte, label string, context []byte, length int) []byte {
	return hkdfExpand(secret, hkdfLabel(length, label, context), length)
}

func deriveSecret(secret []byte, label string, transcriptHash []byte) []byte {
	return expandLabel(secret, label, transcriptHash, hashLen)
}

func transcriptHash(msgs ...[]byte) []byte {
	h := sha256.New()
	for _, m := range msgs {
		h.Write(m)
	}
	return h.Sum(nil)
}

type schedule struct {
	earlySecret, binderExt, binderRes, clientEarly, earlyExporter []byte
	handshakeSecret, clientHS, serverHS, masterSecret            []byte
	clientApp0, serverApp0, exporterMaster, resumptionMaster     []byte
}

func keySchedule(psk, dhe, ch, sh, sf []byte) schedule {
	if psk == nil {
		psk = zero
	}
	if dhe == nil {
		dhe = zero
	}
	var s schedule
	s.earlySecret = hkdfExtract(zero, psk)
	s.binderExt = deriveSecret(s.earlySecret, "ext binder", nil)
	s.binderRes = deriveSecret(s.earlySecret, "res binder", nil)
	chHash := transcriptHash(ch)
	s.clientEarly = deriveSecret(s.earlySecret, "c e traffic", chHash)
	s.earlyExporter = deriveSecret(s.earlySecret, "e exp master", chHash)

	derived := deriveSecret(s.earlySecret, "derived", nil)
	s.handshakeSecret = hkdfExtract(derived, dhe)
	hsCtx := transcriptHash(ch, sh)
	s.clientHS = deriveSecret(s.handshakeSecret, "c hs traffic", hsCtx)
	s.serverHS = deriveSecret(s.handshakeSecret, "s hs traffic", hsCtx)

	s.masterSecret = hkdfExtract(deriveSecret(s.handshakeSecret, "derived", nil), zero)
	apCtx := transcriptHash(ch, sh, sf)
	s.clientApp0 = deriveSecret(s.masterSecret, "c ap traffic", apCtx)
	s.serverApp0 = deriveSecret(s.masterSecret, "s ap traffic", apCtx)
	s.exporterMaster = deriveSecret(s.masterSecret, "exp master", apCtx)
	s.resumptionMaster = deriveSecret(s.masterSecret, "res master", apCtx)
	return s
}

func trafficKeys(secret []byte, keyLen, ivLen int) ([]byte, []byte) {
	return expandLabel(secret, "key", nil, keyLen), expandLabel(secret, "iv", nil, ivLen)
}

// §5.3：per-record nonce = (左补零到 iv_length 的序列号) XOR 静态 IV
func aeadNonce(iv []byte, seq uint64) []byte {
	n := make([]byte, len(iv))
	for i := 0; i < len(iv); i++ {
		shift := uint(8 * (len(iv) - 1 - i))
		n[i] = iv[i] ^ byte(seq>>shift)
	}
	return n
}

// §7.2：application_traffic_secret_N+1 = Expand-Label(., "traffic upd", "", Hash.length)
func keyUpdate(secret []byte) []byte {
	return expandLabel(secret, "traffic upd", nil, hashLen)
}

func main() {
	ok := 0
	chk := func(cond bool, msg string) {
		if !cond {
			panic("断言失败: " + msg)
		}
		ok++
	}


	// HkdfLabel 编码
	lab := hkdfLabel(32, "derived", nil)
	chk(len(lab) == 2+1+13+1, "HkdfLabel 长度 = 2+1+len(tls13 derived)+1")
	chk(lab[0] == 0x00 && lab[1] == 0x20, "uint16 大端长度")
	chk(lab[2] == 13 && string(lab[3:16]) == "tls13 derived", "标签带 tls13 前缀")
	chk(lab[len(lab)-1] == 0, "空 context 也要写 1 字节长度 0")

	dhe := make([]byte, 32)
	for i := range dhe {
		dhe[i] = byte(i)
	}
	ks := keySchedule(nil, dhe, []byte("CH"), []byte("SH"), []byte("SF"))
	chk(bytes.Equal(ks.earlySecret, hkdfExtract(zero, zero)), "无 PSK 时 Early Secret = Extract(0,0)")
	chk(!bytes.Equal(ks.binderExt, ks.binderRes), "ext/res binder 标签分离")
	chk(!bytes.Equal(ks.clientHS, ks.serverHS), "c/s hs traffic 分离")

	ks2 := keySchedule(nil, dhe, []byte("CH2"), []byte("SH"), []byte("SF"))
	chk(bytes.Equal(ks.handshakeSecret, ks2.handshakeSecret), "Handshake Secret 不绑定 transcript")
	chk(!bytes.Equal(ks.clientHS, ks2.clientHS), "traffic secret 绑定 transcript")
	chk(!bytes.Equal(ks.clientApp0, ks2.clientApp0), "ap traffic 绑定 transcript")

	key, iv := trafficKeys(ks.clientApp0, 16, 12)
	chk(len(key) == 16 && len(iv) == 12, "AES-128-GCM: key 16 / iv 12")
	chk(bytes.Equal(aeadNonce(iv, 0), iv), "seq=0 时 nonce = IV")
	chk(bytes.Equal(aeadNonce(iv, 1), append(append([]byte{}, iv[:11]...), iv[11]^1)), "nonce = seq XOR IV")

	n1 := keyUpdate(ks.clientApp0)
	chk(!bytes.Equal(n1, ks.clientApp0), "KeyUpdate 换新 secret")
	chk(bytes.Equal(keyUpdate(ks.clientApp0), n1), "KeyUpdate 确定性")
	n2 := keyUpdate(n1)
	chk(!bytes.Equal(n2, n1), "更新链单调推进")
	k1, v1 := trafficKeys(n1, 16, 12)
	chk(!bytes.Equal(k1, key) && !bytes.Equal(v1, iv), "换 secret 后 key/iv 全重算")

	ks5 := keySchedule([]byte{0x11}, dhe, []byte("CH"), []byte("SH"), []byte("SF"))
	ks6 := keySchedule([]byte{0x11}, dhe, []byte("CH"), []byte("SH2"), []byte("SF2"))
	chk(bytes.Equal(ks5.clientEarly, ks6.clientEarly), "0-RTT 密钥不依赖 ServerHello")

	fmt.Printf("TLS 1.3 密钥调度 Go 自检通过：%d 项断言\n", ok)
}
