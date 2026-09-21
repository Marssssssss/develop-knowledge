// Package slaacprivacy 实现 IPv6 隐私（临时）地址的生成与生命周期管理：
// RFC 4941 §3.2.1 的 MD5 方案、§3.3 的创建步骤、§3.5 的重新生成频率，
// 以及 RFC 8981 §3.3.2 的 PRF 方案与 §3.8 的默认参数。
//
// 与 Python 版的差异显式落地：
//   - Python 的任意精度 int -> Go 的 uint64（IID 与 history 恒为 64 位）；
//   - Python 返回 (IID, history) 二元组 -> Go 返回 (uint64, uint64)；
//   - Python 的 set -> Go 的 map[uint64]bool。
package main

import (
	"crypto/hmac"
	"crypto/md5"
	"crypto/sha256"
	"encoding/binary"
)

// ULBitMask 是 RFC 4291 里 IID 第 0 字节的 universal/local 位（左起 bit 6）。
const ULBitMask = 0x02

// RFC 8981 §3.8 的默认参数（单位：秒）。
const (
	TempValidLifetime     = 2 * 86400
	TempPreferredLifetime = 1 * 86400
	TempIDGenRetries      = 3
	DupAddrDetectTransmits = 1 // RFC 4862 默认
	RetransTimerMs         = 1000 // RFC 4861 默认
)

// RegenAdvance 是 REGEN_ADVANCE = 2 + (TEMP_IDGEN_RETRIES *
// DupAddrDetectTransmits * RetransTimer / 1000)。
func RegenAdvance(idgenRetries, dadTransmits, retransTimerMs float64) float64 {
	return 2 + (idgenRetries*dadTransmits*retransTimerMs)/1000.0
}

func DefaultRegenAdvance() float64 {
	return RegenAdvance(TempIDGenRetries, DupAddrDetectTransmits, RetransTimerMs)
}

// MaxDesyncFactor 是 0.4 * TEMP_PREFERRED_LIFETIME。
func MaxDesyncFactor() float64 { return 0.4 * TempPreferredLifetime }

// DesyncValid 检查 DESYNC_FACTOR 的两条约束。
func DesyncValid(d float64) bool {
	return 0 <= d && d <= MaxDesyncFactor() && d < TempPreferredLifetime-DefaultRegenAdvance()
}

// Md5IID 是 RFC 4941 §3.2.1：MD5(history || public_iid)，
// 左 64 位清掉 U/L 位作为 IID，右 64 位作为下一次的 history。
func Md5IID(historyValue, publicIID uint64) (uint64, uint64) {
	var buf [16]byte
	binary.BigEndian.PutUint64(buf[:8], historyValue)
	binary.BigEndian.PutUint64(buf[8:], publicIID)
	digest := md5.Sum(buf[:])
	left := binary.BigEndian.Uint64(digest[:8])
	right := binary.BigEndian.Uint64(digest[8:])
	return left &^ (uint64(ULBitMask) << 56), right
}

// GenerateIIDWithRetries 是 §3.3 step 7：DAD 冲突时把 history 换成
// 上一次 MD5 的右 64 位重来，最多 retries 次。
func GenerateIIDWithRetries(historyValue, publicIID uint64, reserved map[uint64]bool,
	retries int) (uint64, uint64, bool) {
	hist := historyValue
	for i := 0; i < retries; i++ {
		iid, nxt := Md5IID(hist, publicIID)
		if !reserved[iid] {
			return iid, nxt, true
		}
		hist = nxt
	}
	return 0, 0, false
}

// EUI64FromMAC 是 RFC 4291 附录：MAC -> EUI-64（插入 fffe，翻转 U/L 位）。
func EUI64FromMAC(mac []byte) uint64 {
	b := make([]byte, 8)
	copy(b[0:3], mac[0:3])
	b[3], b[4] = 0xFF, 0xFE
	copy(b[5:8], mac[3:6])
	b[0] ^= ULBitMask
	return binary.BigEndian.Uint64(b)
}

// TempAddressLifetimes 是 §3.3 step 4：
// Valid = min(前缀 valid, TEMP_VALID_LIFETIME)
// Preferred = min(前缀 preferred, TEMP_PREFERRED_LIFETIME - DESYNC_FACTOR)
// 注意只有 preferred 减 DESYNC_FACTOR。
func TempAddressLifetimes(prefixValid, prefixPreferred, desyncFactor float64) (float64, float64) {
	valid := prefixValid
	if TempValidLifetime < valid {
		valid = TempValidLifetime
	}
	preferred := prefixPreferred
	if TempPreferredLifetime-desyncFactor < preferred {
		preferred = TempPreferredLifetime - desyncFactor
	}
	return valid, preferred
}

// ShouldCreateTempAddress 是 §3.3 step 5：Preferred 必须严格大于 REGEN_ADVANCE。
func ShouldCreateTempAddress(prefixPreferred, desyncFactor float64) bool {
	_, preferred := TempAddressLifetimes(prefixPreferred, prefixPreferred, desyncFactor)
	return preferred > DefaultRegenAdvance()
}

// ClampExisting 是 §3.3 step 2：更新已有地址时取 RA 与 CAP 中更早的那个。
func ClampExisting(creationTime, now, raPreferred, desyncFactor float64) float64 {
	raExpiry := now + raPreferred
	cap := creationTime + TempPreferredLifetime - desyncFactor
	if raExpiry < cap {
		return raExpiry
	}
	return cap
}

// RegenerationInterval 是 §3.5：至少每 TEMP_PREFERRED - REGEN_ADVANCE - DESYNC 秒。
func RegenerationInterval(desyncFactor float64) float64 {
	return TempPreferredLifetime - DefaultRegenAdvance() - desyncFactor
}

// RidHmacSHA256 是 RFC 8981 §3.3.2 的 RID（六输入按固定顺序拼接）。
func RidHmacSHA256(secretKey, netIface, networkID []byte, prefixHi, prefixLo uint64,
	timeS int64, dadCounter uint32) []byte {
	msg := make([]byte, 0, 16+len(netIface)+len(networkID)+13)
	var b16 [16]byte
	binary.BigEndian.PutUint64(b16[:8], prefixHi)
	binary.BigEndian.PutUint64(b16[8:], prefixLo)
	msg = append(msg, b16[:]...)
	msg = append(msg, netIface...)
	msg = append(msg, '|')
	msg = append(msg, networkID...)
	msg = append(msg, '|')
	var b8 [8]byte
	binary.BigEndian.PutUint64(b8[:], uint64(timeS))
	msg = append(msg, b8[:]...)
	var b4 [4]byte
	binary.BigEndian.PutUint32(b4[:], dadCounter)
	msg = append(msg, b4[:]...)
	m := hmac.New(sha256.New, secretKey)
	m.Write(msg)
	return m.Sum(nil)
}

// IIDFromRIDLow 是 §3.3.2 step 2：从**最低有效位**开始取 64 位。
func IIDFromRIDLow(rid []byte) uint64 {
	return binary.BigEndian.Uint64(rid[len(rid)-8:])
}

// IIDFromRIDHigh 是对照口径：从最高有效位取（RFC 4941 的 MD5 方案取的就是左边）。
func IIDFromRIDHigh(rid []byte) uint64 {
	return binary.BigEndian.Uint64(rid[:8])
}

// GenerateIIDRFC8981 是 §3.3.2 step 3：冲突时 DAD_Counter 加 1 重算。
func GenerateIIDRFC8981(secretKey, netIface, networkID []byte, prefixHi, prefixLo uint64,
	timeS int64, reserved map[uint64]bool, retries int) (uint64, uint32, bool) {
	var counter uint32
	for i := 0; i < retries; i++ {
		rid := RidHmacSHA256(secretKey, netIface, networkID, prefixHi, prefixLo, timeS, counter)
		iid := IIDFromRIDLow(rid)
		if !reserved[iid] {
			return iid, counter, true
		}
		counter++
	}
	return 0, counter, false
}
