// 硬编码凭据检测（Go 版）：裸熵阈值 → 归一化熵 → 规则叠加。
// 与 Python 版同一份语料，同样的 TP/FP/FN/TN。
package main

import (
	"fmt"
	"math"
	"os"
	"regexp"
)

const (
	minLen        = 20
	rawThreshold  = 4.5
	normThreshold = 0.90
)

type item struct {
	name     string
	value    string
	isSecret bool
}

var corpus = []item{
	{"aws_key", "AKIAIOSFODNN7EXAMPLE", true},
	{"api_token", "3f8a1c7d5e2b4096af17c3de85b0f2146e9a7c31", true},
	{"jwt_secret", "c3VwZXJzZWNyZXR2YWx1ZTEyMzQ1Njc4OTA=", true},
	{"db_password", "password123", true},
	{"vendor_sk", "tok_live_51H8xQ2eZvKYlo2Cabcdefghijklm", true},
	{"git_sha", "da39a3ee5e6b4b0d3255bfef95601890afd80709", false},
	{"request_id", "550e8400-e29b-41d4-a716-446655440000", false},
	{"png_b64", "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ", false},
	{"api_url", "https://api.example.com/v1/users?id=12345", false},
	{"version", "1.2.3", false},
}

// ---- 熵 ----

func shannon(s string) float64 {
	if s == "" {
		return 0
	}
	cnt := map[rune]int{}
	for _, r := range s {
		cnt[r]++
	}
	n := float64(len([]rune(s)))
	h := 0.0
	for _, c := range cnt {
		p := float64(c) / n
		h -= p * math.Log2(p)
	}
	return h
}

func alphabetSize(s string) int {
	return len(cntOf(s))
}

func cntOf(s string) map[rune]int {
	cnt := map[rune]int{}
	for _, r := range s {
		cnt[r]++
	}
	return cnt
}

// normalized = H / log2(|A|)：不同字符集之间才可比。
// 十六进制串最多 4.0 bit，除以 log2(16)=4 后同样能接近 1.0。
func normalized(s string) float64 {
	a := alphabetSize(s)
	if a <= 1 {
		return 0
	}
	return shannon(s) / math.Log2(float64(a))
}

// ---- 规则 ----

var valueRules = []struct {
	name string
	re   *regexp.Regexp
}{
	{"AWS access key id", regexp.MustCompile(`\bAKIA[0-9A-Z]{16}\b`)},
	{"服务商 live key", regexp.MustCompile(`\btok_live_[0-9a-zA-Z]{20,}`)},
	{"PEM private key", regexp.MustCompile(`-----BEGIN [A-Z ]*PRIVATE KEY-----`)},
}

var nameRule = regexp.MustCompile(
	`(?i)(password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)`)

var nameAllowlist = regexp.MustCompile(
	`(?i)(sha|sha1|sha256|commit|digest|checksum|uuid|request[_-]?id|version|hash)`)

// ---- 三级检测器 ----

func d1RawEntropy(name, value string) bool {
	return len(value) >= minLen && shannon(value) >= rawThreshold
}

func d2NormEntropy(name, value string) bool {
	return len(value) >= minLen && normalized(value) >= normThreshold
}

func d3Combined(name, value string) bool {
	if nameAllowlist.MatchString(name) { // 上下文白名单优先
		return false
	}
	for _, r := range valueRules {
		if r.re.MatchString(value) {
			return true
		}
	}
	if d2NormEntropy(name, value) {
		return true
	}
	return nameRule.MatchString(name) && shannon(value) >= 3.0
}

func evaluate(det func(string, string) bool) (int, int, int, int) {
	tp, fp, fn, tn := 0, 0, 0, 0
	for _, it := range corpus {
		got := det(it.name, it.value)
		switch {
		case it.isSecret && got:
			tp++
		case it.isSecret && !got:
			fn++
		case !it.isSecret && got:
			fp++
		default:
			tn++
		}
	}
	return tp, fp, fn, tn
}

// sweep 扫描裸熵阈值，量化「不存在两全的阈值」。
func sweep() (maxTP int, maxTPZeroFP int) {
	for t := 3.0; t <= 5.0; t += 0.01 {
		tp, fp := 0, 0
		for _, it := range corpus {
			if len(it.value) < minLen {
				continue
			}
			if shannon(it.value) >= t {
				if it.isSecret {
					tp++
				} else {
					fp++
				}
			}
		}
		if tp > maxTP {
			maxTP = tp
		}
		if fp == 0 && tp > maxTPZeroFP {
			maxTPZeroFP = tp
		}
	}
	return maxTP, maxTPZeroFP
}

func valueOf(name string) string {
	for _, it := range corpus {
		if it.name == name {
			return it.value
		}
	}
	return ""
}

func main() {
	fail := 0
	check := func(label string, cond bool, detail string) {
		if !cond {
			fmt.Println("FAIL:", label, detail)
			fail++
		}
	}

	// 熵的数学性质
	check("均匀 16 符号 = 4.0", math.Abs(shannon("0123456789abcdef")-4.0) < 1e-9, "")
	check("单字符串为 0", shannon("aaaa") == 0, "")
	check("偏斜 < 均匀", shannon("aaaaaabc") < shannon("abcdefab"), "")

	// 十六进制串的熵上界是 4.0 → 阈值 4.5 必然漏报
	hex40 := valueOf("api_token")
	check("40 位十六进制 < 4.5", shannon(hex40) < 4.5, "")
	check("归一化后 > 0.95", normalized(hex40) > 0.95, "")

	// 三级检测器
	tuple := func(det func(string, string) bool) [4]int {
		tp, fp, fn, tn := evaluate(det)
		return [4]int{tp, fp, fn, tn}
	}
	rows := []struct {
		label string
		r     [4]int
	}{
		{"D1 裸熵 ≥4.5", tuple(d1RawEntropy)},
		{"D2 归一化熵 ≥0.90", tuple(d2NormEntropy)},
		{"D3 规则+归一化熵", tuple(d3Combined)},
	}
	fmt.Printf("%-22s %-4s %-4s %-4s %-4s\n", "detector", "TP", "FP", "FN", "TN")
	for _, row := range rows {
		fmt.Printf("%-22s %-4d %-4d %-4d %-4d\n",
			row.label, row.r[0], row.r[1], row.r[2], row.r[3])
	}
	check("D1 = 2/1/3/4", rows[0].r == [4]int{2, 1, 3, 4}, "")
	check("D2 = 4/2/1/3", rows[1].r == [4]int{4, 2, 1, 3}, "")
	check("D3 = 5/1/0/4", rows[2].r == [4]int{5, 1, 0, 4}, "")
	check("召回单调提升", rows[0].r[0] < rows[1].r[0] && rows[1].r[0] < rows[2].r[0], "")

	// 不可分性：aws_key 的熵比 git_sha 还低 → 任何阈值都分不开
	ha, hb := shannon(valueOf("aws_key")), shannon(valueOf("git_sha"))
	check("H(aws_key) < H(git_sha)", ha < hb, "")
	check("二者不可分", !(ha >= hb), "")

	// 阈值扫描
	maxTP, maxZero := sweep()
	check("零误报时召回最多 1 条", maxZero == 1, "")
	check("最高召回 4 条", maxTP == 4, "")

	if fail > 0 {
		fmt.Printf("FAILED %d\n", fail)
		os.Exit(1)
	}
	fmt.Println("all checks passed")
}
