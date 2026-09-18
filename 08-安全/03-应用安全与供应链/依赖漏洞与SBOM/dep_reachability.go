// 依赖漏洞判定：SemVer 区间匹配 → 传递闭包 → 可达性剪枝（Go 版）。
// 与 Python 版同一套 fixture、同一组判定结果。SBOM 生成见 Python 版 sbom()。
package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

type Ver struct {
	maj, min, pat int
	pre           []string
}

type Cons struct {
	op string
	v  Ver
}

// ---- SemVer（semver.org 2.0.0）----

func parse(s string) Ver {
	core, pre := s, ""
	if i := strings.IndexByte(core, '+'); i >= 0 { // build metadata 不参与优先级
		core = core[:i]
	}
	if i := strings.IndexByte(core, '-'); i >= 0 {
		core, pre = core[:i], core[i+1:]
	}
	p := strings.Split(core, ".")
	if len(p) != 3 {
		panic("bad semver: " + s)
	}
	n := [3]int{}
	for i := 0; i < 3; i++ {
		if len(p[i]) > 1 && p[i][0] == '0' {
			panic("leading zero: " + s) // 规范 §2 禁止前导零
		}
		v, err := strconv.Atoi(p[i])
		if err != nil {
			panic("non-numeric: " + s)
		}
		n[i] = v
	}
	out := Ver{n[0], n[1], n[2], nil}
	if pre != "" {
		out.pre = strings.Split(pre, ".")
	}
	return out
}

func cmpPre(a, b []string) int {
	// 规范 §11.4：数字按数值、其余按 ASCII 序；数字优先级低于非数字；
	// 前缀相同时标识符更多的优先级更高；空集（正式版）最高。
	if len(a) == 0 && len(b) == 0 {
		return 0
	}
	if len(a) == 0 {
		return 1
	}
	if len(b) == 0 {
		return -1
	}
	for i := 0; i < len(a) && i < len(b); i++ {
		x, y := a[i], b[i]
		xn, yn := isDigits(x), isDigits(y)
		var c int
		switch {
		case xn && yn:
			xi, _ := strconv.Atoi(x)
			yi, _ := strconv.Atoi(y)
			c = cmpInt(xi, yi)
		case xn != yn:
			if xn {
				c = -1
			} else {
				c = 1
			}
		default:
			c = strings.Compare(x, y)
		}
		if c != 0 {
			return c
		}
	}
	return cmpInt(len(a), len(b))
}

func isDigits(s string) bool {
	if s == "" {
		return false
	} // 空标识符视为非数字
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func cmpInt(a, b int) int {
	if a < b {
		return -1
	}
	if a > b {
		return 1
	}
	return 0
}

func cmp(a, b Ver) int {
	if c := cmpInt(a.maj, b.maj); c != 0 {
		return c
	}
	if c := cmpInt(a.min, b.min); c != 0 {
		return c
	}
	if c := cmpInt(a.pat, b.pat); c != 0 {
		return c
	}
	return cmpPre(a.pre, b.pre)
}

func caret(s string, lo *Ver, hi *Ver) {
	p := parse(s)
	*lo = Ver{p.maj, p.min, p.pat, nil}
	// 规范 §4：0.y.z 处于初始开发期、API 不稳定 → ^ 在 0.x 上只锁到 minor
	switch {
	case p.maj > 0:
		*hi = Ver{p.maj + 1, 0, 0, nil}
	case p.min > 0:
		*hi = Ver{0, p.min + 1, 0, nil}
	default:
		*hi = Ver{0, 0, p.pat + 1, nil}
	}
}

func tilde(s string, lo *Ver, hi *Ver) {
	p := parse(s)
	*lo = Ver{p.maj, p.min, p.pat, nil}
	if strings.Count(s, ".") >= 1 {
		*hi = Ver{p.maj, p.min + 1, 0, nil}
	} else {
		*hi = Ver{p.maj + 1, 0, 0, nil}
	}
}

func toCons(spec string) []Cons {
	if strings.HasPrefix(spec, "^") {
		var lo, hi Ver
		caret(spec[1:], &lo, &hi)
		return []Cons{{">=", lo}, {"<", hi}}
	}
	if strings.HasPrefix(spec, "~") {
		var lo, hi Ver
		tilde(spec[1:], &lo, &hi)
		return []Cons{{">=", lo}, {"<", hi}}
	}
	out := []Cons{}
	for _, tok := range strings.Fields(spec) {
		for _, op := range []string{">=", "<=", ">", "<", "=="} {
			if strings.HasPrefix(tok, op) {
				out = append(out, Cons{op, parse(tok[len(op):])})
				break
			}
		}
	}
	return out
}

func satisfies(ver Ver, cs []Cons) bool {
	for _, c := range cs {
		k := cmp(ver, c.v)
		ok := false
		switch c.op {
		case ">=":
			ok = k >= 0
		case ">":
			ok = k > 0
		case "<=":
			ok = k <= 0
		case "<":
			ok = k < 0
		case "==":
			ok = k == 0
		}
		if !ok {
			return false
		}
	}
	return true
}

func pick(name string, cs []Cons) string {
	best := ""
	for _, s := range registry[name] {
		if !satisfies(parse(s), cs) {
			continue
		}
		if best == "" || cmp(parse(s), parse(best)) > 0 {
			best = s
		}
	}
	if best == "" {
		panic("no version for " + name)
	}
	return best
}

func resolve() map[string]string {
	cons := map[string][]Cons{}
	for n, s := range root {
		cons[n] = toCons(s)
	}
	frontier := []string{}
	for n := range root {
		frontier = append(frontier, n)
	}
	done := map[string]bool{}
	for len(frontier) > 0 {
		name := frontier[0]
		frontier = frontier[1:]
		if done[name] {
			continue
		}
		done[name] = true
		ver := pick(name, cons[name])
		for dn, spec := range deps[name+"@"+ver] {
			cons[dn] = append(cons[dn], toCons(spec)...)
			frontier = append(frontier, dn)
		}
	}
	out := map[string]string{}
	for n, c := range cons {
		out[n] = pick(n, c)
	}
	return out
}

func main() {
	fail := 0
	// SemVer 规范 §11.4 的优先级链
	chain := []string{"1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
		"1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"}
	for i := 0; i+1 < len(chain); i++ {
		if cmp(parse(chain[i]), parse(chain[i+1])) >= 0 {
			fmt.Printf("FAIL 优先级链 %s < %s\n", chain[i], chain[i+1])
			fail++
		}
	}
	if cmp(parse("1.0.0+build1"), parse("1.0.0+build2")) != 0 {
		fmt.Println("FAIL build metadata 应当被忽略")
		fail++
	}
	if satisfies(parse("0.3.0"), toCons("^0.2.3")) {
		fmt.Println("FAIL ^0.2.3 不应允许 0.3.0")
		fail++
	}

	r := resolve()
	want := map[string]string{"httpkit": "2.4.0", "codec": "1.4.3",
		"compress": "0.2.7", "logfmt": "1.0.2", "orm": "3.1.0"}
	for k, v := range want {
		if r[k] != v {
			fmt.Printf("FAIL resolve %s got=%s want=%s\n", k, r[k], v)
			fail++
		}
	}
	reach := reachable(entry)
	// 期望：版本命中 3 条，其中可达 2 条
	byVer, reachCnt := 0, 0
	fmt.Printf("%-14s %-9s %-7s %-11s %s\n", "CVE", "pkg", "ver", "by_version", "reachable")
	for _, c := range cves {
		ver := r[c.pkg]
		hit := satisfies(parse(ver), toCons(c.spec))
		rc := hit && reach[c.sym]
		if hit {
			byVer++
		}
		if rc {
			reachCnt++
		}
		fmt.Printf("%-14s %-9s %-7s %-11v %v\n", c.id, c.pkg, ver, hit, rc)
	}
	if byVer != 3 || reachCnt != 2 {
		fmt.Printf("FAIL 判定计数 by_version=%d reachable=%d（应为 3/2）\n", byVer, reachCnt)
		fail++
	}
	if reach["compress.Inflate"] {
		fmt.Println("FAIL compress.Inflate 不应可达")
		fail++
	}
	if fail > 0 {
		fmt.Printf("FAILED %d\n", fail)
		os.Exit(1)
	}
	fmt.Println("all checks passed")
}
