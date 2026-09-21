// Package sshtrans 实现 SSH 传输层的二进制包协议、算法协商与密钥派生
// （RFC 4253 §6/§7/§8）以及 RFC 8308 的扩展协商。
//
// 与 Python 版的差异显式落地：
//   - Python 的任意精度 int -> Go 用 math/big.Int 做 mpint；
//   - Python 抛 ValueError -> Go 返回 (值, error)；
//   - Python 的 set/dict -> Go 的 map[string]map[string]bool / map[string]bool。
package main

import (
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"math/big"
)

const (
	minPadding              = 4
	maxPadding              = 255
	minPacket               = 16
	maxUncompressedPayload  = 32768
	maxTotalPacket          = 35000
)

func hashIt(data []byte) []byte {
	s := sha256.Sum256(data)
	return s[:]
}

// BlockSizeFor 是 §6 的对齐单位：max(cipher block size, 8)。
func BlockSizeFor(cipherBlockSize int) int {
	if cipherBlockSize > 8 {
		return cipherBlockSize
	}
	return 8
}

// PaddingLength 求满足全部约束的最小 padding。
func PaddingLength(payloadLen, cipherBlockSize int) int {
	blk := BlockSizeFor(cipherBlockSize)
	pad := (blk - ((4+1+payloadLen)%blk)) % blk
	if pad < minPadding {
		pad += blk
	}
	return pad
}

// BuildPacket 组装一个二进制包（padding 用确定性字节，便于测试）。
func BuildPacket(payload []byte, cipherBlockSize int, mac []byte) []byte {
	pad := PaddingLength(len(payload), cipherBlockSize)
	packetLength := pad + 1 + len(payload)
	out := make([]byte, 4)
	binary.BigEndian.PutUint32(out, uint32(packetLength))
	out = append(out, byte(pad))
	out = append(out, payload...)
	for i := 0; i < pad; i++ {
		out = append(out, byte((i*7+3)&0xFF))
	}
	return append(out, mac...)
}

type Packet struct {
	PacketLength  int
	PaddingLength int
	Payload       []byte
	Padding       []byte
	MAC           []byte
}

// ParsePacket 解析并校验一个包。
func ParsePacket(raw []byte, cipherBlockSize, macLen int) (*Packet, error) {
	if len(raw) < 4 {
		return nil, errors.New("连 packet_length 都不够")
	}
	packetLength := int(binary.BigEndian.Uint32(raw[:4]))
	if packetLength+4+macLen != len(raw) {
		return nil, errors.New("packet_length 与实际长度不符")
	}
	if 4+packetLength < maxInt(minPacket, cipherBlockSize) {
		return nil, errors.New("小于最小包长")
	}
	if (4+packetLength)%BlockSizeFor(cipherBlockSize) != 0 {
		return nil, errors.New("未按块对齐")
	}
	pad := int(raw[4])
	if pad < minPadding {
		return nil, errors.New("padding 少于 4 字节")
	}
	payloadLen := packetLength - pad - 1
	if payloadLen < 0 {
		return nil, errors.New("payload 长度为负")
	}
	return &Packet{PacketLength: packetLength, PaddingLength: pad,
		Payload: raw[5 : 5+payloadLen], Padding: raw[5+payloadLen : 4+packetLength],
		MAC: raw[4+packetLength:]}, nil
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// Alg 是一个算法名加上它需要的主机密钥能力。
type Alg struct {
	Name  string
	Needs string // any / sign / encrypt
}

// NeedOk 判断某个主机密钥算法是否具备所需能力。
func NeedOk(hostKeyAlg, need string, caps map[string]map[string]bool) bool {
	return caps[hostKeyAlg][need]
}

// SelectKex 是 §7.1 的三条件选择。
func SelectKex(clientKex, serverKex []Alg, clientHostKeys, serverHostKeys []string,
	caps map[string]map[string]bool) *Alg {
	inServer := func(n string) bool {
		for _, s := range serverKex {
			if s.Name == n {
				return true
			}
		}
		return false
	}
	for _, k := range clientKex {
		if !inServer(k.Name) {
			continue
		}
		if k.Needs == "any" {
			return &k
		}
		found := false
		for _, hk := range clientHostKeys {
			inSrvHost := false
			for _, sh := range serverHostKeys {
				if sh == hk {
					inSrvHost = true
					break
				}
			}
			if inSrvHost && NeedOk(hk, k.Needs, caps) {
				found = true
				break
			}
		}
		if found {
			return &k
		}
	}
	return nil
}

// SelectFirstMatch 是 §7.1 中 cipher / MAC / compression 的选法。
func SelectFirstMatch(clientList, serverList []string) string {
	set := map[string]bool{}
	for _, s := range serverList {
		set[s] = true
	}
	for _, c := range clientList {
		if set[c] {
			return c
		}
	}
	return ""
}

var letters = map[string]byte{
	"iv_c2s": 'A', "iv_s2c": 'B',
	"enc_c2s": 'C', "enc_s2c": 'D',
	"mac_c2s": 'E', "mac_s2c": 'F',
}

// DeriveKey 是 §7.2 的 K1 = HASH(K || H || X || session_id) 与链式扩展。
func DeriveKey(kMpint, h []byte, letter byte, sessionID []byte, needBytes int) []byte {
	k1 := hashIt(append(append(append(append([]byte{}, kMpint...), h...), letter), sessionID...))
	out := append([]byte{}, k1...)
	prev := [][]byte{k1}
	for len(out) < needBytes {
		buf := append([]byte{}, kMpint...)
		buf = append(buf, h...)
		for _, p := range prev {
			buf = append(buf, p...)
		}
		nxt := hashIt(buf)
		prev = append(prev, nxt)
		out = append(out, nxt...)
	}
	return out[:needBytes]
}

func deriveAll(kMpint, h, sessionID []byte) map[string][]byte {
	out := map[string][]byte{}
	for name, ch := range letters {
		out[name] = DeriveKey(kMpint, h, ch, sessionID, 32)
	}
	return out
}

// ExchangeHash 是 §8 的 H = hash(V_C||V_S||I_C||I_S||K_S||e||f||K)。
func ExchangeHash(vC, vS, iC, iS, kS []byte, e, f, k *big.Int) []byte {
	buf := sshString(vC)
	buf = append(buf, sshString(vS)...)
	buf = append(buf, sshString(iC)...)
	buf = append(buf, sshString(iS)...)
	buf = append(buf, sshString(kS)...)
	buf = append(buf, Mpint(e)...)
	buf = append(buf, Mpint(f)...)
	buf = append(buf, Mpint(k)...)
	return hashIt(buf)
}

const (
	extInfoC = "ext-info-c"
	extInfoS = "ext-info-s"
)

// OffersExtInfo 判断某方是否给出了本角色专用的 indicator。
func OffersExtInfo(names []string, role string) bool {
	want := extInfoC
	if role == "server" {
		want = extInfoS
	}
	for _, n := range names {
		if n == want {
			return true
		}
	}
	return false
}

// ExtNegotiationError 是 RFC 8308 §2.2 的错误判定。
func ExtNegotiationError(selectedKex string, clientKex, serverKex []string) string {
	if selectedKex == extInfoC || selectedKex == extInfoS {
		return "indicator 被协商为 KEX 方法，必须断开"
	}
	for _, n := range clientKex {
		if n == extInfoS {
			return "客户端发送了服务端专用的 indicator"
		}
	}
	for _, n := range serverKex {
		if n == extInfoC {
			return "服务端发送了客户端专用的 indicator"
		}
	}
	return ""
}

// ExtInfoAllowed 是 RFC 8308 §2.2：只有对端给了 indicator 才「可以」发。
func ExtInfoAllowed(peerOffers bool) bool { return peerOffers }
