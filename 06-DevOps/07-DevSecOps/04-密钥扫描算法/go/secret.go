// Package secret 是 demo574 的 Go 转写：两套密钥扫描器的熵口径、
// gitleaks 的规则校验与关键词预筛、detect-secrets hex 插件的纯数字惩罚。
package secret

import (
	"math"
	"regexp"
	"strings"
)

// ---------------------------------------------------------------- 熵

// ShannonEntropy 对应 gitleaks 的 shannonEntropy：
// 统计 data 里实际出现的每个 rune，不受字符集限制。
func ShannonEntropy(data string) float64 {
	if data == "" {
		return 0
	}
	counts := map[rune]int{}
	for _, ch := range data {
		counts[ch]++
	}
	inv := 1.0 / float64(len([]rune(data)))
	entropy := 0.0
	for _, c := range counts {
		freq := float64(c) * inv
		entropy -= freq * math.Log2(freq)
	}
	return entropy
}

// CharsetEntropy 对应 detect-secrets 的 HighEntropyStringsPlugin.calculate_shannon_entropy：
// **只遍历 charset**，分母却是 len(data)，因此含 charset 外字符时会偏低。
func CharsetEntropy(data, charset string) float64 {
	if len([]rune(data)) == 0 {
		return 0
	}
	entropy := 0.0
	n := float64(len([]rune(data)))
	for _, x := range charset {
		c := strings.Count(data, string(x))
		if c == 0 {
			continue
		}
		p := float64(c) / n
		entropy -= p * math.Log2(p)
	}
	return entropy
}

// CharsetEntropyStr 是 CharsetEntropy 的 string 版本，附带阈值过滤（严格大于）。
func Exceeds(data, charset string, limit float64) bool {
	return CharsetEntropy(data, charset) > limit
}

// ---------------------------------------------------------------- 字符集

const (
	// Base64Charset 对应 Base64HighEntropyString：源码里写的是 '\\-_'，
	// 即包含反斜杠、连字符与下划线。
	Base64Charset = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/\\-_="
	// HexCharset 对应 HexHighEntropyString：string.hexdigits。
	HexCharset = "0123456789abcdefABCDEF"
)

// HexEntropy 对应 HexHighEntropyString.calculate_shannon_entropy：
// 纯数字串要减去 1.2/log2(len) 的惩罚，len == 1 时不罚。
func HexEntropy(data string) float64 {
	e := CharsetEntropy(data, HexCharset)
	if len([]rune(data)) == 1 {
		return e
	}
	if _, err := parseIntAllDigits(data); err != nil {
		return e
	}
	return e - 1.2/math.Log2(float64(len([]rune(data))))
}

func parseIntAllDigits(s string) (int64, error) {
	if s == "" {
		return 0, errNotNumber
	}
	var v int64
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c < '0' || c > '9' {
			return 0, errNotNumber
		}
		v = v*10 + int64(c-'0')
	}
	return v, nil
}

var errNotNumber = &parseError{}

type parseError struct{}

func (e *parseError) Error() string { return "not a pure-digit string" }

// ---------------------------------------------------------------- gitleaks

// Allowlist 是 gitleaks allowlist 的简化模型。
type Allowlist struct {
	Regexes     []*regexp.Regexp
	Paths       []*regexp.Regexp
	Stopwords   []string
	RegexTarget string // match | secret | line
}

// Rule 对应 gitleaks 的 config.Rule。
type Rule struct {
	RuleID      string
	Regex       *regexp.Regexp
	Path        *regexp.Regexp
	Entropy     float64
	SecretGroup int
	Keywords    []string
	Allowlists  []*Allowlist
}

// Validate 对应 config.Rule.Validate()：返回空串表示通过。
func (r *Rule) Validate() string {
	if strings.TrimSpace(r.RuleID) == "" {
		return "rule |id| is missing or empty"
	}
	if r.Regex == nil && r.Path == nil {
		return r.RuleID + ": both |regex| and |path| are empty, this rule will have no effect"
	}
	if r.Regex != nil && r.SecretGroup > r.Regex.NumSubexp() {
		return "invalid regex secret group"
	}
	for _, al := range r.Allowlists {
		if al.RegexTarget != "" && al.RegexTarget != "match" && al.RegexTarget != "secret" && al.RegexTarget != "line" {
			return "invalid regexTarget: " + al.RegexTarget
		}
	}
	return ""
}

// PrefilterHit 对应 detect.go 的关键词预筛：
// 规则没有 keywords 时**总是**扫描；否则片段必须命中其中任一keyword。
func PrefilterHit(r *Rule, fragmentKeywords map[string]bool) bool {
	if len(r.Keywords) == 0 {
		return true
	}
	for _, k := range r.Keywords {
		if fragmentKeywords[k] {
			return true
		}
	}
	return false
}

// Finding 是一条命中。
type Finding struct {
	Rule    string
	Secret  string
	Entropy float64
}

// DetectRule 在片段上跑一条规则：allowlist 过滤 + 熵阈值（严格大于）。
func DetectRule(r *Rule, fragment, path string) []Finding {
	if r.Regex == nil {
		return nil
	}
	var out []Finding
	for _, m := range r.Regex.FindAllStringSubmatch(fragment, -1) {
		matchText := m[0]
		secret := matchText
		if r.SecretGroup > 0 && r.SecretGroup < len(m) {
			secret = m[r.SecretGroup]
		}
		if allowedBy(r, secret, matchText, fragment, path) {
			continue
		}
		entropy := ShannonEntropy(secret)
		// 源码：if r.Entropy != 0.0 { if entropy <= r.Entropy { skip } }
		if r.Entropy != 0.0 && entropy <= r.Entropy {
			continue
		}
		out = append(out, Finding{Rule: r.RuleID, Secret: secret, Entropy: entropy})
	}
	return out
}

func allowedBy(r *Rule, secret, matchText, fragment, path string) bool {
	for _, al := range r.Allowlists {
		for _, p := range al.Paths {
			if p.MatchString(path) {
				return true
			}
		}
		target := matchText
		switch al.RegexTarget {
		case "secret":
			target = secret
		case "line":
			target = fragment
		}
		for _, re := range al.Regexes {
			if re.MatchString(target) {
				return true
			}
		}
		for _, w := range al.Stopwords {
			if strings.Contains(strings.ToLower(secret), w) {
				return true
			}
		}
	}
	return false
}
