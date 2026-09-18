// WireGuard 协议层：常量、Noise 状态机（ck/h/k 与四个混合操作）、X25519 封装、
// TAI64N 时间戳、MAC1 与 cookie、重放窗口。
//
// 与 wg_handshake.go（main 里的校验流程）同属 main 包；常量与消息布局对照内核
// drivers/net/wireguard/{messages.h,noise.c,cookie.c}。
package main

const (
	pubLen    = 32
	symLen    = 32
	tagLen    = 16
	tsLen     = 12
	hashLen   = 32
	cookieLen = 16
	macsLen   = 2 * cookieLen

	lenInitiation = 4 + 4 + pubLen + (pubLen + tagLen) + (tsLen + tagLen) + macsLen
	lenResponse   = 4 + 4 + 4 + pubLen + tagLen + macsLen
	lenDataHeader = 4 + 4 + 8

	rekeyAfterTime  = 120
	rejectAfterTime = 180
	keepaliveTime   = 10
	rekeyTimeout    = 5

	counterBitsTotal = 8192
	counterWindow    = counterBitsTotal - 64 // 冗余 64 位避免移位越界
)

var (
	handshakeName  = []byte("Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s")
	identifierName = []byte("WireGuard v1 zx2c4 Jason@zx2c4.com")
	mac1Label      = []byte("mac1----")
	cookieLabel    = []byte("cookie--")
)

var failures int

func check(label string, ok bool, detail string) {
	status := "ok"
	if !ok {
		status = "FAIL"
		failures++
	}
	fmt.Printf("%-44s %s %s\n", label, status, detail)
}

// symmetric 保存 Noise 的 ck / h / k，对应内核 noise.c 的 noise_handshake 相关字段。
type symmetric struct {
	ck, h, k []byte
	haveKey  bool
}

func newSymmetric(preMessageStatic []byte) *symmetric {
	// 与纯 Noise 的差异：ck0 = HASH(handshakeName)、h0 = HASH(ck0 ‖ identifierName)
	ck := blake2s(handshakeName, nil, hashLen)
	joined := append(append([]byte{}, ck...), identifierName...)
	s := &symmetric{ck: ck, h: blake2s(joined, nil, hashLen)}
	s.mixHash(preMessageStatic)
	return s
}

func (s *symmetric) mixHash(data []byte) {
	s.h = blake2s(append(append([]byte{}, s.h...), data...), nil, hashLen)
}

func (s *symmetric) mixKey(ikm []byte) {
	out := kdf(s.ck, ikm, 2)
	s.ck, s.k, s.haveKey = out[0], out[1], true
}

// mixKeyOnlyCk 对应内核 mix_dh(ck, NULL, ...)：只需要新 chaining key 时不产出密钥。
func (s *symmetric) mixKeyOnlyCk(ikm []byte) {
	s.ck = kdf(s.ck, ikm, 1)[0]
}

// mixEphemeral 是 PSK 握手特有的一步：e token 先 MixHash，再用同一公钥 MixKey（只更新 ck）。
func (s *symmetric) mixEphemeral(pub []byte) {
	s.mixHash(pub)
	s.mixKeyOnlyCk(pub)
}

// mixKeyAndHash 对应 Noise 的 MixKeyAndHash：三个输出依次是新 ck、temp_h（喂给 hash）、新 k。
func (s *symmetric) mixKeyAndHash(psk []byte) {
	out := kdf(s.ck, psk, 3)
	s.ck, s.k, s.haveKey = out[0], out[2], true
	s.mixHash(out[1])
}

func (s *symmetric) encrypt(plain []byte) ([]byte, bool) {
	if !s.haveKey {
		s.mixHash(plain)
		return plain, true
	}
	ct := aeadSeal(s.k, make([]byte, 12), s.h, plain)
	s.mixHash(ct)
	return ct, true
}

// decrypt 与 encrypt 镜像：先用当前 h 作 AD 认证解密，再把密文混入哈希。
// 认证失败时**不**推进哈希（对应 Noise「解密失败则终止握手」与内核的 message_decrypt）。
func (s *symmetric) decrypt(ct []byte) ([]byte, bool) {
	if !s.haveKey {
		s.mixHash(ct)
		return ct, true
	}
	plain, ok := aeadOpen(s.k, make([]byte, 12), s.h, ct)
	if !ok {
		return nil, false
	}
	s.mixHash(ct)
	return plain, true
}

func (s *symmetric) split() ([]byte, []byte) {
	out := kdf(s.ck, []byte{}, 2)
	return out[0], out[1]
}

func x25519Keypair(seed []byte) (*ecdh.PrivateKey, []byte) {
	priv, err := ecdh.X25519().NewPrivateKey(seed)
	if err != nil {
		panic(err)
	}
	return priv, priv.PublicKey().Bytes()
}

func x25519Shared(priv *ecdh.PrivateKey, peerPub []byte) []byte {
	pk, err := ecdh.X25519().NewPublicKey(peerPub)
	if err != nil {
		panic(err)
	}
	shared, err := priv.ECDH(pk)
	if err != nil {
		panic(err)
	}
	return shared
}

// tai64n 生成 12 字节时间戳：8 字节秒（偏移 0x400000000000000A）+ 4 字节纳秒（大端）。
func tai64n(unixSeconds uint64, nsec uint32) []byte {
	out := make([]byte, tsLen)
	binary.BigEndian.PutUint64(out, 0x400000000000000A+unixSeconds)
	binary.BigEndian.PutUint32(out[8:], nsec)
	return out
}

func mac1Key(responderStatic []byte) []byte {
	return blake2s(append(append([]byte{}, mac1Label...), responderStatic...), nil, symLen)
}

func computeMac1(msg, responderStatic []byte) []byte {
	body := msg[:len(msg)-macsLen]
	return blake2s(body, mac1Key(responderStatic), cookieLen)
}

func makeCookie(secret []byte, srcIP []byte, srcPort uint16) []byte {
	buf := append(append([]byte{}, srcIP...), byte(srcPort>>8), byte(srcPort))
	return blake2s(buf, secret, cookieLen)
}

// replayCounter 复刻内核 receive.c counter_validate 的 8192 位滑动窗口。
type replayCounter struct {
	bits []byte
	last uint64
	init bool
}

func newReplayCounter() *replayCounter {
	return &replayCounter{bits: make([]byte, counterBitsTotal/8)}
}

func (r *replayCounter) validate(counter uint64) bool {
	switch {
	case !r.init:
		r.last, r.init = counter, true
	case counter > r.last:
		if counter-r.last > counterWindow {
			r.bits = make([]byte, counterBitsTotal/8)
		}
		r.last = counter
	case r.last-counter >= counterWindow+1:
		return false
	}
	idx := counter % counterBitsTotal
	byteIdx, mask := idx/8, byte(1)<<(idx%8)
	if r.bits[byteIdx]&mask != 0 {
		return false
	}
	r.bits[byteIdx] |= mask
	return true
}

