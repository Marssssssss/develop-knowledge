// Package main 实现账户安全（口令策略 / 黑名单 / 限流 / 凭据填充防护）最小模型。
//
// 依据 NIST SP 800-63B（https://pages.nist.gov/800-63-4/sp800-63b.html 353757 B 实读）：
//   - 3.1.1.2 单因素口令 SHALL ≥15；多因素下 MAY 更短但 SHALL ≥8；SHOULD 允许 ≥64；
//     每个 Unicode 码点算一个字符；接受 Unicode 则哈希前做 NFC；
//     SHALL NOT 施加组成规则 / 定期改密 / 未认证可读的 hint / KBA；
//     SHALL 请求完整口令并校验完整口令（不截断）；黑名单对**整串**比对（不是子串）；
//     SHALL 加盐哈希存储。
//   - 3.1.2.1 look-up secret SHALL ≥6 位十进制数字。
//   - 3.1.3 带外 secret SHALL 在 10 分钟内完成，且有效期内只接受一次。
//   - 3.2.2 连续失败认证尝试上限 100（agencies MAY 更低）。
//   - 激活密钥连续失败重试 SHALL 不超过 10。
//
// 语言差异：Go 标准库没有 NFC，本文件用一张**最小**规范组合表覆盖
// Latin-1 Supplement 里的常见组合，只用于验证「分解式与合成式归一到同值」这一性质。
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"unicode/utf8"
)

// 口令长度门槛
const (
	MinLenSingle        = 15
	MinLenMFA           = 8
	RecommendedMax      = 64
	ThrottleUpperBound  = 100
	LookupSecretDigits  = 6
	OOBValiditySeconds  = 600
	ActivationRetryLim  = 10
	ActivationMinLen    = 4
)

// nfcTable 最小规范组合表：(基字符, 组合记号) → 预组合字符
var nfcTable = map[rune]map[rune]rune{
	'A': {0x0300: 0x00C0, 0x0301: 0x00C1, 0x0302: 0x00C2, 0x0303: 0x00C3, 0x0308: 0x00C4, 0x030A: 0x00C5},
	'a': {0x0300: 0x00E0, 0x0301: 0x00E1, 0x0302: 0x00E2, 0x0303: 0x00E3, 0x0308: 0x00E4, 0x030A: 0x00E5},
	'E': {0x0300: 0x00C8, 0x0301: 0x00C9, 0x0302: 0x00CA, 0x0308: 0x00CB},
	'e': {0x0300: 0x00E8, 0x0301: 0x00E9, 0x0302: 0x00EA, 0x0308: 0x00EB},
	'I': {0x0300: 0x00CC, 0x0301: 0x00CD, 0x0302: 0x00CE, 0x0308: 0x00CF},
	'i': {0x0300: 0x00EC, 0x0301: 0x00ED, 0x0302: 0x00EE, 0x0308: 0x00EF},
	'O': {0x0300: 0x00D2, 0x0301: 0x00D3, 0x0302: 0x00D4, 0x0303: 0x00D5, 0x0308: 0x00D6},
	'o': {0x0300: 0x00F2, 0x0301: 0x00F3, 0x0302: 0x00F4, 0x0303: 0x00F5, 0x0308: 0x00F6},
	'U': {0x0300: 0x00D9, 0x0301: 0x00DA, 0x0302: 0x00DB, 0x0308: 0x00DC},
	'u': {0x0300: 0x00F9, 0x0301: 0x00FA, 0x0302: 0x00FB, 0x0308: 0x00FC},
	'N': {0x0303: 0x00D1}, 'n': {0x0303: 0x00F1},
	'C': {0x0327: 0x00C7}, 'c': {0x0327: 0x00E7},
	'Y': {0x0301: 0x00DD}, 'y': {0x0301: 0x00FD},
}

// NormalizePassword 做最小 NFC：把「基字符 + 组合记号」折叠成预组合字符。
func NormalizePassword(pw string) string {
	runes := []rune(pw)
	out := make([]rune, 0, len(runes))
	for i := 0; i < len(runes); i++ {
		r := runes[i]
		if i+1 < len(runes) {
			if marks, ok := nfcTable[r]; ok {
				if composed, ok2 := marks[runes[i+1]]; ok2 {
					out = append(out, composed)
					i++
					continue
				}
			}
		}
		out = append(out, r)
	}
	return string(out)
}

// PasswordLength 每个 Unicode 码点算一个字符。
func PasswordLength(pw string) int {
	return utf8.RuneCountInString(NormalizePassword(pw))
}

// Verifier 是口令验证器。
type Verifier struct {
	MFA           bool
	MinLen        int
	MaxLen        int
	Blocklist     map[string]bool
	ContextWords  map[string]bool
	ThrottleLimit int
	Cost          int
	failed        map[string]int
	stored        map[string][3]string
}

// NewVerifier 按 SP 800-63B 的 SHALL 构造。
func NewVerifier(mfa bool, maxLen int, throttleLimit int) *Verifier {
	minLen := MinLenSingle
	if mfa {
		minLen = MinLenMFA
	}
	return &Verifier{MFA: mfa, MinLen: minLen, MaxLen: maxLen,
		ThrottleLimit: throttleLimit, Cost: 1000,
		Blocklist: map[string]bool{}, ContextWords: map[string]bool{},
		failed: map[string]int{}, stored: map[string][3]string{}}
}

// ValidatePassword 返回 (ok, reasons)。
func (v *Verifier) ValidatePassword(pw string) (bool, []string) {
	var reasons []string
	n := PasswordLength(pw)
	if n < v.MinLen {
		reasons = append(reasons, "too_short")
	}
	if n > v.MaxLen {
		reasons = append(reasons, "exceeds_max")
	}
	norm := NormalizePassword(pw)
	if v.Blocklist[norm] {
		reasons = append(reasons, "blocklisted")
	}
	if v.ContextWords[norm] {
		reasons = append(reasons, "context_specific")
	}
	return len(reasons) == 0, reasons
}

func digest(salt string, cost int, pw string) string {
	h := sha256.Sum256([]byte(salt + "|" + itoa(cost) + "|" + NormalizePassword(pw)))
	return hex.EncodeToString(h[:])
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}

// Store 加盐哈希存储。sha256 只是占位（真实须用内存困难函数）。
func (v *Verifier) Store(user, pw string) [3]string {
	saltRaw := sha256.Sum256([]byte(user + "|" + pw))
	salt := hex.EncodeToString(saltRaw[:])[:16]
	rec := [3]string{salt, itoa(v.Cost), digest(salt, v.Cost, pw)}
	v.stored[user] = rec
	return rec
}

// Verify 校验完整提交的口令（不截断）。
func (v *Verifier) Verify(user, pw string) bool {
	rec, ok := v.stored[user]
	if !ok {
		return false
	}
	return digest(rec[0], v.Cost, pw) == rec[2]
}

// Authenticate 带限流的认证，返回 (ok, locked)。
func (v *Verifier) Authenticate(user, pw string) (bool, bool) {
	if v.failed[user] >= v.ThrottleLimit {
		return false, true
	}
	if v.Verify(user, pw) {
		v.failed[user] = 0
		return true, false
	}
	v.failed[user]++
	return false, v.failed[user] >= v.ThrottleLimit
}

// ConsecutiveFailures 返回连续失败次数。
func (v *Verifier) ConsecutiveFailures(user string) int { return v.failed[user] }

// ResetFailures 重置计数。
func (v *Verifier) ResetFailures(user string) { v.failed[user] = 0 }

// CheckLookupSecret look-up secret SHALL 至少 6 位十进制数字。
func CheckLookupSecret(secret string) bool {
	if len(secret) < LookupSecretDigits {
		return false
	}
	for i := 0; i < len(secret); i++ {
		if secret[i] < '0' || secret[i] > '9' {
			return false
		}
	}
	return true
}

// OutOfBandSecret 带外 secret：10 分钟内有效且只接受一次。
type OutOfBandSecret struct {
	Secret   string
	IssuedAt int
	Used     bool
}

// Accept 尝试接受，成功则标记为已用。
func (o *OutOfBandSecret) Accept(candidate string, now int) bool {
	if o.Used || now-o.IssuedAt > OOBValiditySeconds || candidate != o.Secret {
		return false
	}
	o.Used = true
	return true
}

// ActivationSecret 激活密钥：连续失败重试不超过 10。
type ActivationSecret struct {
	Secret  string
	Retries int
}

// TrySecret 返回 ok / reject / locked。
func (a *ActivationSecret) TrySecret(candidate string) string {
	if a.Retries >= ActivationRetryLim {
		return "locked"
	}
	if candidate == a.Secret {
		a.Retries = 0
		return "ok"
	}
	a.Retries++
	if a.Retries >= ActivationRetryLim {
		return "locked"
	}
	return "reject"
}

// ValidLength 激活密钥 SHALL 至少 4 字符。
func (a *ActivationSecret) ValidLength() bool {
	return len([]rune(a.Secret)) >= ActivationMinLen
}
