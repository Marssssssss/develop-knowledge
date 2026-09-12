// TLS 1.3 握手核心机制 (RFC 8446 §7.1 Key Schedule + §4.1.2 1-RTT)
//
// 本程序演示:
//   - Client/Server 各自生成 X25519 临时密钥对 (golang.org/x/crypto/curve25519)
//   - ECDHE → shared secret
//   - HKDF-Extract + HKDF-Expand-Label 派生 handshake_secret/traffic secret/write_key
//   - 用 AES-128-GCM 加密 Certificate + Finished 并由 Client 解密
//
// 依赖:golang.org/x/crypto (curve25519 + hkdf)
//
// 运行:
//   cd go && go mod init tls13handshake 2>/dev/null || true
//   go get golang.org/x/crypto/curve25519
//   go run tls13_handshake.go

package main

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"io"

	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"

	"golang.org/x/crypto/curve25519"
	"golang.org/x/crypto/hkdf"
)

// ============================================================
// 1. X25519 ECDH (RFC 7748)
// ============================================================

func x25519Keypair() (priv, pub [32]byte, err error) {
	_, err = rand.Read(priv[:])
	if err != nil {
		return
	}
	// X25519 clamp (RFC 7748 §5)
	priv[0] &= 248
	priv[31] &= 127
	priv[31] |= 64

	curve25519.ScalarBaseMult(&pub, &priv)
	return
}

func x25519Shared(priv, peerPub *[32]byte) [32]byte {
	var s [32]byte
	curve25519.ScalarMult(&s, priv, peerPub)
	return s
}

// ============================================================
// 2. HKDF-Expand-Label (RFC 8446 §7.1)
// ============================================================

func hkdfExpandLabel(secret []byte, label string, context []byte, length int) []byte {
	// info = u16(len("tls13 " + label)) || "tls13 " + label || u16(len(context)) || context || u16(length)
	fullLabel := []byte("tls13 " + label)
	info := make([]byte, 0, 4+len(fullLabel)+len(context)+2)
	info = binary.BigEndian.AppendUint16(info, uint16(len(fullLabel)))
	info = append(info, fullLabel...)
	info = binary.BigEndian.AppendUint16(info, uint16(len(context)))
	info = append(info, context...)
	info = binary.BigEndian.AppendUint16(info, uint16(length))

	// HKDF-Expand-Label = HKDF-Expand(secret, info, length)
	// 简化: length ≤ 32 时只用一轮 HKDF-Expand (T(1))
	if length > 255*sha256.Size {
		panic("length too large")
	}
	r := hkdf.Expand(sha256.New, secret, info)
	out := make([]byte, length)
	if _, err := io.ReadFull(r, out); err != nil {
		panic(err)
	}
	return out
}

func deriveSecret(secret []byte, label string, transcriptHash []byte) []byte {
	return hkdfExpandLabel(secret, label, transcriptHash, 32)
}

// ============================================================
// 3. AES-128-GCM 加解密 (Go stdlib)
// ============================================================

func aeadSeal(key, iv, plaintext, aad []byte) (ciphertext, tag []byte) {
	block, _ := aes.NewCipher(key)
	aesgcm, _ := cipher.NewGCMWithNonceSize(block, 12) // 12-byte nonce for TLS 1.3 (RFC 5116)
	nonce := make([]byte, 12)
	copy(nonce, iv)
	// AES-GCM in Go stdlib returns ciphertext||tag concatenated
	out := aesgcm.Seal(nil, nonce, plaintext, aad)
	return out[:len(out)-16], out[len(out)-16:]
}

func aeadOpen(key, iv, ciphertext, tag, aad []byte) (plaintext []byte, ok bool) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, false
	}
	aesgcm, err := cipher.NewGCMWithNonceSize(block, 12)
	if err != nil {
		return nil, false
	}
	nonce := make([]byte, 12)
	copy(nonce, iv)
	full := append(append([]byte{}, ciphertext...), tag...)
	pt, err := aesgcm.Open(nil, nonce, full, aad)
	if err != nil {
		return nil, false
	}
	return pt, true
}

// ============================================================
// 4. 简化 transcript 哈希 (教学:真实 TLSPlaintext 字节级)
// ============================================================

func transcriptHash(a, b []byte) []byte {
	h := sha256.New()
	h.Write(a)
	h.Write(b)
	return h.Sum(nil)
}

// ============================================================
// 5. 完整 1-RTT 握手演示
// ============================================================

func main() {
	fmt.Println("================================================================")
	fmt.Println(" TLS 1.3 1-RTT Handshake Demo (RFC 8446)")
	fmt.Println("================================================================")

	// ---- Client 侧生成 X25519 临时密钥对 ----
	cPriv, cPub, _ := x25519Keypair()
	fmt.Println("\n[Client] X25519 keypair")
	fmt.Printf("  priv = %x…\n", cPriv[:8])
	fmt.Printf("  pub  = %x…\n", cPub[:8])

	// ---- Server 侧生成 X25519 临时密钥对 ----
	sPriv, sPub, _ := x25519Keypair()
	fmt.Println("\n[Server] X25519 keypair")
	fmt.Printf("  priv = %x…\n", sPriv[:8])
	fmt.Printf("  pub  = %x…\n", sPub[:8])

	// ---- ECDHE 双方计算 shared secret(必须相等)----
	sharedC := x25519Shared(&cPriv, &sPub)
	sharedS := x25519Shared(&sPriv, &cPub)

	fmt.Println("\n[ECDHE] shared secret")
	if sharedC != sharedS {
		panic("ECDHE mismatch!")
	}
	fmt.Printf("  Client → %x…\n", sharedC[:8])
	fmt.Printf("  Server → %x…\n", sharedS[:8])
	fmt.Println("  ✓ 双方一致")

	// ---- HKDF-Extract: handshake_secret ----
	zeroSalt := make([]byte, 32)
	hkdfReader := hkdf.New(sha256.New, sharedC[:], zeroSalt, nil) // 注意: 标准用法 PRK=HKDF-Extract(salt, ikm)
	handshakeSecret := make([]byte, 32)
	if _, err := io.ReadFull(hkdfReader, handshakeSecret); err != nil {
		panic(err)
	}
	// 更直接: 用 hkdf.Extract 拿 32 字节 PRK (RFC 5869 §2.2)
	hkdfReader = hkdf.New(sha256.New, sharedC[:], zeroSalt, nil)
	handshakeSecret2 := make([]byte, 32)
	io.ReadFull(hkdfReader, handshakeSecret2)
	_ = handshakeSecret
	handshakeSecret = handshakeSecret2
	fmt.Println("\n[HKDF-Extract] handshake_secret =", hex.EncodeToString(handshakeSecret)[:16], "…")

	// ---- Derive-Secret ----
	// transcript (CH || SH) — 教学用 canon 字符串 hash 替代 TLSPlaintext 字节流
	chStr := []byte("ClientHello(v3, TLS_AES_128_GCM_SHA256, X25519, key_share=...)")
	shStr := []byte("ServerHello(TLS_AES_128_GCM_SHA256, X25519, key_share=...)")
	transcript := transcriptHash(chStr, shStr)

	serverHSSecret := deriveSecret(handshakeSecret, "s hs traffic", transcript)
	clientHSSecret := deriveSecret(handshakeSecret, "c hs traffic", transcript)
	fmt.Printf("[Derive-Secret] server_handshake_traffic_secret = %x…\n", serverHSSecret[:8])
	fmt.Printf("[Derive-Secret] client_handshake_traffic_secret = %x…\n", clientHSSecret[:8])

	// ---- write_key + write_iv ----
	serverWriteKey := hkdfExpandLabel(serverHSSecret, "key", nil, 16)
	serverWriteIV := hkdfExpandLabel(serverHSSecret, "iv", nil, 12)
	clientWriteKey := hkdfExpandLabel(clientHSSecret, "key", nil, 16)
	clientWriteIV := hkdfExpandLabel(clientHSSecret, "iv", nil, 12)

	fmt.Printf("\n[AEAD] server_write_key (16 B) = %x…\n", serverWriteKey[:8])
	fmt.Printf("[AEAD] server_write_iv  (12 B) = %x…\n", serverWriteIV[:8])

	// ---- Server 加密 Certificate + Finished ----
	certMsg := []byte("<fake X.509 certificate chain ~2KB DER in real>")
	finMsg := []byte("<HMAC over full handshake transcript>")

	ctCert, tagCert := aeadSeal(serverWriteKey, serverWriteIV, certMsg, transcript)
	ctFin, tagFin := aeadSeal(serverWriteKey, serverWriteIV, finMsg, transcript)

	fmt.Println("\n[Server] 加密 Certificate/Finished:")
	fmt.Printf("  ct_Cert   (%d B) = %x…  tag=%x\n", len(ctCert), ctCert[:8], tagCert[:8])
	fmt.Printf("  ct_Fin    (%d B) = %x…  tag=%x\n", len(ctFin), ctFin[:8], tagFin[:8])

	// ---- Client 解密验证 ----
	ptCert, okC := aeadOpen(serverWriteKey, serverWriteIV, ctCert, tagCert, transcript)
	ptFin, okF := aeadOpen(serverWriteKey, serverWriteIV, ctFin, tagFin, transcript)
	fmt.Println("\n[Client] 解密:")
	fmt.Printf("  Certificate     = %q  ok=%v\n", ptCert, okC)
	fmt.Printf("  Finished        = %q  ok=%v\n", ptFin, okF)

	// ---- 派生 master_secret + application_traffic_secret (1-RTT 后才能加 app data) ----
	// RFC 8446 §7.1: master_secret = HKDF-Extract(Derive-Secret(handshake_secret, "derived", ""), 0^Hash.length)
	derivedFromHandshake := deriveSecret(handshakeSecret, "derived", nil)
	// 拿 32 字节 0 作 ikm
	zeroIKM := make([]byte, 32)
	msReader := hkdf.New(sha256.New, zeroIKM, derivedFromHandshake, nil)
	masterSecret := make([]byte, 32)
	io.ReadFull(msReader, masterSecret)
	fmt.Printf("\n[master_secret] = %x…\n", masterSecret[:8])

	// ClientFinished 发送后,transcript 包含全部 handshake 消息 → 派生 app traffic secret
	fullTranscript := append(append(append(append(append(chStr, shStr...), certMsg...), finMsg...), ctCert...), tagCert...)
	fullTranscript = append(fullTranscript, append(ctFin, tagFin...)...)

	serverAppSecret := deriveSecret(masterSecret, "s ap traffic", fullTranscript)
	clientAppSecret := deriveSecret(masterSecret, "c ap traffic", fullTranscript)
	fmt.Printf("[server_app_traffic_secret] = %x…\n", serverAppSecret[:8])
	fmt.Printf("[client_app_traffic_secret] = %x…\n", clientAppSecret[:8])

	// ---- App Data 加解密 (HTTP over TLS) ----
	appKeyC := hkdfExpandLabel(clientAppSecret, "key", nil, 16)
	appIVC := hkdfExpandLabel(clientAppSecret, "iv", nil, 12)

	httpReq := []byte("GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
	ctReq, tagReq := aeadSeal(appKeyC, appIVC, httpReq, nil)
	ptReq, okR := aeadOpen(appKeyC, appIVC, ctReq, tagReq, nil)
	fmt.Printf("\n[App Data] 加密 HTTP 请求: %x…  tag=%x…\n", ctReq[:8], tagReq[:8])
	fmt.Printf("[App Data] 解密还原       : %q  ok=%v\n", ptReq, okR)

	fmt.Println("\n================================================================")
	fmt.Println(" TLS 1.3 1-RTT 握手演示完成 ✓")
	fmt.Println("================================================================")
}
