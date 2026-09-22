package main

// main.go 把 python/selfcheck_secret.py 的关键结论在 Go 侧跑一遍，
// 便于与 Python 实现逐条对拍（本机无 Go 工具链时以静态检查 + 人工审查替代）。

import (
	"fmt"
	"regexp"

	s "secret"
)

func main() {
	// A4/A4b：同一串在两套口径下的熵
	fmt.Printf("A4  ds base64 entropy of %q: %.4f\n", "a!bc",
		s.CharsetEntropy("a!bc", s.Base64Charset))
	fmt.Printf("A4b gitleaks entropy of %q: %.4f\n", "a!bc", s.ShannonEntropy("a!bc"))

	// B1/B3：hex 插件的纯数字惩罚
	fmt.Printf("B1  hex %q: %.4f\n", "0123456789", s.HexEntropy("0123456789"))
	fmt.Printf("B3  hex %q: %.4f\n", "01234567890123456789", s.HexEntropy("01234567890123456789"))
	fmt.Printf("B6  hex %q: %.4f (罚后可为负)\n", "55", s.HexEntropy("55"))

	// B7：真实 AWS key 的熵低于默认阈值
	fmt.Printf("B7  base64 %q: %.4f < 4.5\n", "AKIAIOSFODNN7EXAMPLE",
		s.CharsetEntropy("AKIAIOSFODNN7EXAMPLE", s.Base64Charset))

	// D3：gitleaks 的阈值是「小于等于则跳过」
	re := regexp.MustCompile(`tok=([A-Za-z]+)`)
	eq := &s.Rule{RuleID: "tok", Regex: re, Entropy: 2.0, SecretGroup: 1}
	lt := &s.Rule{RuleID: "tok", Regex: re, Entropy: 1.99, SecretGroup: 1}
	fmt.Println("D3  entropy==threshold skipped:", len(s.DetectRule(eq, "tok=abcd", "")) == 0)
	fmt.Println("D3b entropy<threshold kept:", len(s.DetectRule(lt, "tok=abcd", "")) == 1)

	// E1/E2：关键词预筛
	kw := &s.Rule{RuleID: "artifactory", Regex: regexp.MustCompile(`(AKCp[A-Za-z0-9]{4})`),
		Keywords: []string{"akcp"}, SecretGroup: 1}
	fmt.Println("E1  keyword hit:", s.PrefilterHit(kw, map[string]bool{"akcp": true}))
	fmt.Println("E1b keyword miss:", s.PrefilterHit(kw, map[string]bool{"other": true}))
	noKW := &s.Rule{RuleID: "generic", Regex: regexp.MustCompile(`(AKCp[A-Za-z0-9]{4})`), SecretGroup: 1}
	fmt.Println("E2  no keywords always scanned:", s.PrefilterHit(noKW, map[string]bool{"other": true}))

	// F1/F3：规则校验
	fmt.Printf("F1  empty id: %q\n", (&s.Rule{RuleID: "", Regex: re}).Validate())
	fmt.Printf("F3  bad secretGroup: %q\n",
		(&s.Rule{RuleID: "r", Regex: re, SecretGroup: 2}).Validate())
	fmt.Printf("F3b ok: %q\n", (&s.Rule{RuleID: "r", Regex: re, SecretGroup: 1}).Validate())

	// G3：regexTarget 决定拿哪段匹配 allowlist 正则
	re2 := regexp.MustCompile(`tok=([a-z]+)_END`)
	alSec := &s.Allowlist{Regexes: []*regexp.Regexp{regexp.MustCompile(`^abc$`)}, RegexTarget: "secret"}
	alMat := &s.Allowlist{Regexes: []*regexp.Regexp{regexp.MustCompile(`^abc$`)}, RegexTarget: "match"}
	fmt.Println("G3  target=secret allowed:",
		len(s.DetectRule(&s.Rule{RuleID: "t", Regex: re2, SecretGroup: 1, Allowlists: []*s.Allowlist{alSec}},
			"tok=abc_END", "")) == 0)
	fmt.Println("G3b target=match kept:",
		len(s.DetectRule(&s.Rule{RuleID: "t", Regex: re2, SecretGroup: 1, Allowlists: []*s.Allowlist{alMat}},
			"tok=abc_END", "")) == 1)
}
