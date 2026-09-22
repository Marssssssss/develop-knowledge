// Package webpush 复刻 Web Push 的端到端加密：RFC 8291 的密钥管理与
// RFC 8188 的 "aes128gcm" 内容编码。
//
// 与 Python 版是同一套算法的两个实现：Python 版额外手写了 P-256 与 AES-GCM，
// 用来对拍 RFC 8291 附录 A 的官方中间值；本文件使用标准库
//（crypto/ecdh / crypto/hmac / crypto/aes / crypto/cipher）以便直接落地。
package webpush

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdh"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"errors"
)

// 常量。
const (
	// HeaderFixed 是 salt(16) + rs(4) + idlen(1) 的固定长度。
	HeaderFixed = 21
	// DefaultRS 是 RFC 8291 §5 示例使用的记录大小。
	DefaultRS = 4096
	// MaxBody 是推送服务**至少**需要支持的 body 字节数（RFC 8030 §7.2）。
	MaxBody = 4096
	// TagLen 是 AEAD_AES_128_GCM 的认证标签长度。
	TagLen = 16
)

// 派生用的 info 串（RFC 8291 §3.4，均不以 NUL 结尾，拼接时自己补 0x00）。
var (
	keyInfoPrefix = []byte("WebPush: info")
	cekInfo       = []byte("Content-Encoding: aes128gcm")
	nonceInfo     = []byte("Content-Encoding: nonce")
)

// ErrRecordSize 表示 rs 装不下明文 + 分隔符 + 填充 + 标签。
var ErrRecordSize = errors.New("rs too small for plaintext + padding")

// ErrDelimiter 表示填充分隔符不是 0x02（RFC 8291 §4 要求丢弃整条消息）。
var ErrDelimiter = errors.New("padding delimiter must be 0x02")

// Header 是 aes128gcm 内容编码头（RFC 8188 §2.1）。
type Header struct {
	Salt  []byte
	RS    uint32
	KeyID []byte
}

// EncodeHeader 序列化 salt || rs || idlen || keyid。
func EncodeHeader(salt []byte, rs uint32, keyid []byte) []byte {
	out := make([]byte, 0, HeaderFixed+len(keyid))
	out = append(out, salt...)
	var b [4]byte
	binary.BigEndian.PutUint32(b[:], rs)
	out = append(out, b[:]...)
	out = append(out, byte(len(keyid)))
	return append(out, keyid...)
}

// DecodeHeader 解析头部；长度与 idlen 不一致时返回错误。
func DecodeHeader(raw []byte) (*Header, error) {
	if len(raw) < HeaderFixed {
		return nil, errors.New("header shorter than 21 octets")
	}
	idlen := int(raw[20])
	if len(raw) != HeaderFixed+idlen {
		return nil, errors.New("idlen disagrees with header length")
	}
	return &Header{
		Salt:  raw[0:16],
		RS:    binary.BigEndian.Uint32(raw[16:20]),
		KeyID: raw[HeaderFixed : HeaderFixed+idlen],
	}, nil
}

// Keys 是派生链路上的全部中间值，与 RFC 8291 附录 A 一一对应。
type Keys struct {
	ECDHSecret []byte
	PRKKey     []byte
	IKM        []byte
	PRK        []byte
	CEK        []byte
	Nonce      []byte
}

func hkdfExtract(salt, ikm []byte) []byte {
	m := hmac.New(sha256.New, salt)
	m.Write(ikm)
	return m.Sum(nil)
}

func hkdfExpandOne(prk, info []byte) []byte {
	m := hmac.New(sha256.New, prk)
	m.Write(info)
	m.Write([]byte{0x01})
	return m.Sum(nil)
}

// Derive 按 RFC 8291 §3.4 逐步派生。
func Derive(ecdhSecret, authSecret, uaPublic, asPublic, salt []byte) *Keys {
	prkKey := hkdfExtract(authSecret, ecdhSecret)
	keyInfo := make([]byte, 0, len(keyInfoPrefix)+1+len(uaPublic)+len(asPublic))
	keyInfo = append(keyInfo, keyInfoPrefix...)
	keyInfo = append(keyInfo, 0x00)
	keyInfo = append(keyInfo, uaPublic...)
	keyInfo = append(keyInfo, asPublic...)
	ikm := hkdfExpandOne(prkKey, keyInfo)
	prk := hkdfExtract(salt, ikm)
	cek := hkdfExpandOne(prk, append(append([]byte{}, cekInfo...), 0x00))[:16]
	nonce := hkdfExpandOne(prk, append(append([]byte{}, nonceInfo...), 0x00))[:12]
	return &Keys{ecdhSecret, prkKey, ikm, prk, cek, nonce}
}

// SharedSecret 用 P-256 做 ECDH，返回共享点的 X 坐标。
func SharedSecret(priv *ecdh.PrivateKey, peerPublic []byte) ([]byte, error) {
	pub, err := ecdh.P256().NewPublicKey(peerPublic)
	if err != nil {
		return nil, err
	}
	secret, err := priv.ECDH(pub)
	if err != nil {
		return nil, err
	}
	return secret, nil
}

// NewKeyPair 生成一把一次性的应用服务器 ECDH 密钥。
func NewKeyPair() (*ecdh.PrivateKey, error) {
	return ecdh.P256().GenerateKey(rand.Reader)
}

// PaddingBudget 是在 rs 之下还能放多少填充字节。
func PaddingBudget(rs uint32, plaintextLen int) int {
	return int(rs) - TagLen - plaintextLen - 1
}

// MaxPlaintext 是 4096 字节 body 下的明文上限（RFC 8291 §4 给的 3993）。
func MaxPlaintext() int {
	return MaxBody - (HeaderFixed + 65) - 1 - TagLen
}

// Encrypt 应用服务器侧加密，返回 header || 密文 || tag。
func Encrypt(priv *ecdh.PrivateKey, uaPublic, authSecret, plaintext []byte,
	rs uint32, pad int) ([]byte, *Keys, error) {
	if PaddingBudget(rs, len(plaintext)) < pad {
		return nil, nil, ErrRecordSize
	}
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return nil, nil, err
	}
	asPublic := priv.PublicKey().Bytes()
	secret, err := SharedSecret(priv, uaPublic)
	if err != nil {
		return nil, nil, err
	}
	k := Derive(secret, authSecret, uaPublic, asPublic, salt)
	record := make([]byte, 0, len(plaintext)+1+pad)
	record = append(record, plaintext...)
	record = append(record, 0x02)
	record = append(record, make([]byte, pad)...)
	body, err := gcmSeal(k.CEK, k.Nonce, record)
	if err != nil {
		return nil, nil, err
	}
	out := append(EncodeHeader(salt, rs, asPublic), body...)
	return out, k, nil
}

// Decrypt 用户代理侧解密。
func Decrypt(priv *ecdh.PrivateKey, authSecret, body []byte) ([]byte, *Keys, error) {
	head, err := DecodeHeader(body[:HeaderFixed+65])
	if err != nil {
		return nil, nil, err
	}
	uaPublic := priv.PublicKey().Bytes()
	secret, err := SharedSecret(priv, head.KeyID)
	if err != nil {
		return nil, nil, err
	}
	k := Derive(secret, authSecret, uaPublic, head.KeyID, head.Salt)
	record, err := gcmOpen(k.CEK, k.Nonce, body[HeaderFixed+65:])
	if err != nil {
		return nil, nil, err
	}
	idx := -1
	for i := len(record) - 1; i >= 0; i-- {
		if record[i] == 0x02 {
			idx = i
			break
		}
	}
	if idx < 0 {
		return nil, nil, ErrDelimiter
	}
	return record[:idx], k, nil
}

func gcmSeal(key, nonce, plaintext []byte) ([]byte, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return gcm.Seal(nil, nonce, plaintext, nil), nil
}

func gcmOpen(key, nonce, data []byte) ([]byte, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return gcm.Open(nil, nonce, data, nil)
}
