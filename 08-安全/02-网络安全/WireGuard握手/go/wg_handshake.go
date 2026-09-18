// WireGuard 握手校验流程（main）：按 Noise_IKpsk2 的消息顺序逐步推进两端状态，
// 断言每一阶段 ck/h 一致、字段长度符合内核结构体、AEAD 篡改被拒、重放窗口正确。
//
// 运行:  go run .
package main

import (
	"crypto/rand"
	"fmt"
	"os"
)

func main() {
	check("LEN_INITIATION == 148", lenInitiation == 148, fmt.Sprint(lenInitiation))
	check("LEN_RESPONSE == 92", lenResponse == 92, fmt.Sprint(lenResponse))
	check("LEN_DATA_HEADER == 16", lenDataHeader == 16, fmt.Sprint(lenDataHeader))
	check("handshake_name 长度 37", len(handshakeName) == 37, fmt.Sprint(len(handshakeName)))
	check("identifier_name 长度 34", len(identifierName) == 34, fmt.Sprint(len(identifierName)))

	// BLAKE2s 官方向量（RFC 7693 附录 A 的 "abc"）
	want := "508c5e8c327c14e2e1a72ba34eeb452f37458b209ed63a294d999b4c86675982"
	got := fmt.Sprintf("%x", blake2s([]byte("abc"), nil, 32))
	check("BLAKE2s-256(\"abc\") 官方向量", got == want, got[:16])

	// 两侧各自的静态/临时密钥
	aPriv, aPub := x25519Keypair([]byte("alice-seed-0123456789abcdef012345"))
	bPriv, bPub := x25519Keypair([]byte("bob-seed-0123456789abcdef01234567"))
	ePriv, ePub := x25519Keypair([]byte("eph-seed-0123456789abcdef01234567"))
	e2Priv, e2Pub := x25519Keypair([]byte("eph2seed-0123456789abcdef0123456"))
	psk := make([]byte, symLen)
	for i := range psk {
		psk[i] = 0x33
	}

	// 发起方视角的 pre-message 是响应方静态公钥；响应方视角是自己那份
	syA := newSymmetric(bPub)
	syB := newSymmetric(bPub)
	check("ck0 一致", string(syA.ck) == string(syB.ck), "")
	check("h0 一致", string(syA.h) == string(syB.h), "")

	// msg1: e → es → s → ss
	syA.mixEphemeral(ePub)
	syB.mixEphemeral(ePub)
	esA, esB := x25519Shared(ePriv, bPub), x25519Shared(bPriv, ePub)
	check("DH(es) 两侧一致", string(esA) == string(esB), "")
	syA.mixKey(esA)
	syB.mixKey(esB)
	encStatic, _ := syA.encrypt(aPub)
	check("encrypted_static 长度 48", len(encStatic) == 48, fmt.Sprint(len(encStatic)))
	decStatic, okStatic := syB.decrypt(encStatic)
	check("响应方解出 encrypted_static == 发起方静态公钥",
		okStatic && string(decStatic) == string(aPub), "")
	ssA, ssB := x25519Shared(aPriv, bPub), x25519Shared(bPriv, aPub)
	check("DH(ss) 两侧一致", string(ssA) == string(ssB), "")
	syA.mixKey(ssA)
	syB.mixKey(ssB)
	encTs, _ := syA.encrypt(tai64n(1700000000, 0))
	check("encrypted_timestamp 长度 28", len(encTs) == 28, fmt.Sprint(len(encTs)))
	decTs, okTs := syB.decrypt(encTs)
	check("响应方解出时间戳（12 字节 TAI64N）",
		okTs && len(decTs) == tsLen && binary.BigEndian.Uint64(decTs) == 0x400000000000000A+1700000000, "")
	check("msg1 后 h 一致", string(syA.h) == string(syB.h), "")
	check("msg1 后 ck 一致", string(syA.ck) == string(syB.ck), "")

	// msg2: e → ee → se → psk → {}
	syA.mixEphemeral(e2Pub)
	syB.mixEphemeral(e2Pub)
	syA.mixKey(x25519Shared(ePriv, e2Pub))
	syB.mixKey(x25519Shared(e2Priv, ePub))
	seA, seB := x25519Shared(aPriv, e2Pub), x25519Shared(e2Priv, aPub)
	check("DH(se) 两侧一致", string(seA) == string(seB), "")
	syA.mixKey(seA)
	syB.mixKey(seB)
	syA.mixKeyAndHash(psk)
	syB.mixKeyAndHash(psk)
	check("psk 混入后 h 一致", string(syA.h) == string(syB.h), "")
	encNothing, _ := syB.encrypt(nil)
	check("encrypted_nothing 长度 16", len(encNothing) == tagLen, fmt.Sprint(len(encNothing)))
	decNothing, okNothing := syA.decrypt(encNothing)
	check("响应消息空负载认证通过", okNothing && len(decNothing) == 0, "")

	// split(): HKDF(ck, 空输入, 2) → 双向传输密钥
	kAB, kBA := syA.split()
	kAB2, kBA2 := syB.split()
	check("Split 两侧一致", string(kAB) == string(kAB2) && string(kBA) == string(kBA2), "")
	check("双向密钥不同", string(kAB) != string(kBA), "")

	// 传输数据消息：type|key_idx|counter|AEAD(空 AD)
	payload := []byte("hello wireguard")
	padded := append(append([]byte{}, payload...), make([]byte, (16-len(payload)%16)%16)...)
	dm := aeadSeal(kAB, append(make([]byte, 4), 7, 0, 0, 0, 0, 0, 0, 0), nil, padded)
	check("数据消息 = 16 + 16 + 16", lenDataHeader+len(dm) == 48, fmt.Sprint(lenDataHeader+len(dm)))
	opened, ok := aeadOpen(kAB, append(make([]byte, 4), 7, 0, 0, 0, 0, 0, 0, 0), nil, dm)
	check("数据消息解封成功", ok && len(opened) == 16, fmt.Sprint(len(opened)))
	bad := append([]byte{}, dm...)
	bad[len(bad)-1] ^= 0x01
	_, ok = aeadOpen(kAB, append(make([]byte, 4), 7, 0, 0, 0, 0, 0, 0, 0), nil, bad)
	check("篡改 tag 必须被拒", !ok, "")
	_, ok = aeadOpen(kBA, append(make([]byte, 4), 7, 0, 0, 0, 0, 0, 0, 0), nil, dm)
	check("反向密钥解不开", !ok, "")

	// MAC1 / cookie
	initMsg := make([]byte, lenInitiation)
	initMsg[0] = 1
	copy(initMsg[8:], ePub)
	_, otherPub := x25519Keypair([]byte("other-seed-0123456789abcdef0123456"))
	macBefore := computeMac1(initMsg, bPub)
	check("MAC1 绑响应方静态公钥", string(macBefore) != string(computeMac1(initMsg, otherPub)), "")
	check("MAC1 长度 16", len(macBefore) == cookieLen, fmt.Sprint(len(macBefore)))
	initMsg[8] ^= 0x01
	check("改一字节 → MAC1 变化", string(macBefore) != string(computeMac1(initMsg, bPub)), "")
	initMsg[8] ^= 0x01

	secret := make([]byte, symLen)
	for i := range secret {
		secret[i] = 0x77
	}
	srcIP := []byte{10, 0, 0, 1}
	c1 := makeCookie(secret, srcIP, 51820)
	c2 := makeCookie(secret, srcIP, 51821)
	check("cookie 绑定源端口", string(c1) != string(c2), "")

	// cookie 用 XChaCha20Poly1305（24 字节随机 nonce）+ MAC1 作为 AD
	nonce := make([]byte, 24)
	if _, err := rand.Read(nonce); err != nil {
		panic(err)
	}
	mac1 := computeMac1(initMsg, bPub)
	ckKey := blake2s(append(append([]byte{}, cookieLabel...), bPub...), nil, symLen)
	sealedCookie := xchacha20poly1305Seal(ckKey, nonce, mac1, c1)
	dec, ok := xchacha20poly1305Open(ckKey, nonce, mac1, sealedCookie)
	check("cookie 往返成功", ok && string(dec) == string(c1), "")
	_, ok = xchacha20poly1305Open(ckKey, nonce, mac1, sealedCookie[:len(sealedCookie)-1])
	check("cookie 截断必须被拒", !ok, "")

	// 重放窗口
	rc := newReplayCounter()
	check("首包接受", rc.validate(0), "")
	check("重复包拒绝", !rc.validate(0), "")
	check("跳窗接受", rc.validate(counterWindow+5), "")
	check("跳窗后旧号拒绝", !rc.validate(1), "")
	rc2 := newReplayCounter()
	check("边界：落后恰好一个窗口仍接受",
		rc2.validate(counterWindow) && rc2.validate(0), "")
	rc3 := newReplayCounter()
	check("边界：落后超一个窗口拒绝",
		rc3.validate(counterWindow+1) && !rc3.validate(0), "")

	// 定时器常量（内核 messages.h enum limits）
	check("REKEY_AFTER_TIME(120) < REJECT_AFTER_TIME(180)",
		rekeyAfterTime < rejectAfterTime, fmt.Sprintf("%d<%d", rekeyAfterTime, rejectAfterTime))
	check("MAX_TIMER_HANDSHAKES == 18", 90/rekeyTimeout == 18, fmt.Sprint(90/rekeyTimeout))
	check("KEEPALIVE_TIMEOUT == 10", keepaliveTime == 10, fmt.Sprint(keepaliveTime))

	fmt.Printf("\n%s: %d failure(s)\n", map[bool]string{true: "FAILED", false: "all checks passed"}[failures > 0], failures)
	if failures > 0 {
		os.Exit(1)
	}
}
