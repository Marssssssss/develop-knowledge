// Package sbom 是 demo572 Python 实现的 Go 转写：PURL 规范化、
// vers 区间判定、CycloneDX VEX 状态判定与 SPDX 关系归一化。
//
// 对应规范：
//   package-url/purl-spec Clause 5 + docs/specification/how-to-build.md
//   CycloneDX specification 1.6 schema（vulnerability / affects / affectedStatus）
//   SPDX 2.3 chapters/relationships-between-SPDX-elements.md
package sbom

import (
	"sort"
	"strings"
)

// ---------------------------------------------------------------- PURL

// Purl 是 PURL 的七个组件（scheme 恒为 pkg，不存）。
type Purl struct {
	Type       string
	Namespace  []string
	Name       string
	Version    string
	Qualifiers map[string]string
	Subpath    []string
}

func allowed(b byte) bool {
	switch {
	case b >= 'A' && b <= 'Z', b >= 'a' && b <= 'z', b >= '0' && b <= '9':
		return true
	case b == '.', b == '_', b == '~', b == '-', b == ':':
		// 规范 5.4：冒号无论是否作为分隔符都不编码
		return true
	}
	return false
}

// PctEncode 对应规范 5.4 的百分号编码：豁免集为字母数字 + . _ ~ - 与冒号。
func PctEncode(s string) string {
	const hex = "0123456789ABCDEF"
	var sb strings.Builder
	for i := 0; i < len(s); i++ {
		b := s[i]
		if allowed(b) {
			sb.WriteByte(b)
			continue
		}
		sb.WriteByte('%')
		sb.WriteByte(hex[b>>4])
		sb.WriteByte(hex[b&0x0F])
	}
	return sb.String()
}

// BuildPurl 按 how-to-build 构造规范形式。
func BuildPurl(p Purl) string {
	var sb strings.Builder
	sb.WriteString("pkg:")
	sb.WriteString(strings.ToLower(p.Type))
	sb.WriteString("/")
	if len(p.Namespace) > 0 {
		for i, seg := range p.Namespace {
			if i > 0 {
				sb.WriteString("/")
			}
			sb.WriteString(PctEncode(seg))
		}
		sb.WriteString("/")
	}
	sb.WriteString(PctEncode(p.Name))
	if p.Version != "" {
		sb.WriteString("@")
		sb.WriteString(PctEncode(p.Version))
	}
	if len(p.Qualifiers) > 0 {
		var pairs []string
		for k, v := range p.Qualifiers {
			if v == "" {
				continue // 空值等同于该 key 不存在
			}
			pairs = append(pairs, strings.ToLower(k)+"="+PctEncode(v))
		}
		sort.Strings(pairs) // 按 key=value 字符串字典序
		if len(pairs) > 0 {
			sb.WriteString("?")
			sb.WriteString(strings.Join(pairs, "&"))
		}
	}
	if len(p.Subpath) > 0 {
		var segs []string
		for _, s := range p.Subpath {
			if s == "" || s == "." || s == ".." {
				continue
			}
			segs = append(segs, PctEncode(s))
		}
		if len(segs) > 0 {
			sb.WriteString("#")
			sb.WriteString(strings.Join(segs, "/"))
		}
	}
	return sb.String()
}

// ParsePurl 是 BuildPurl 的逆向；只做本 demo 需要的严格性。
func ParsePurl(s string) Purl {
	text := strings.TrimSpace(s)
	if strings.HasPrefix(strings.ToLower(text), "pkg:") {
		text = text[4:]
	}
	text = strings.TrimLeft(text, "/") // 5.6.1：scheme 后的 '/' 应被移除

	p := Purl{Qualifiers: map[string]string{}}
	if i := strings.Index(text, "#"); i >= 0 {
		sp := text[i+1:]
		text = text[:i]
		for _, seg := range strings.Split(sp, "/") {
			if seg == "" || seg == "." || seg == ".." {
				continue
			}
			p.Subpath = append(p.Subpath, seg)
		}
	}
	if i := strings.Index(text, "?"); i >= 0 {
		qs := text[i+1:]
		text = text[:i]
		for _, pair := range strings.Split(qs, "&") {
			if pair == "" {
				continue
			}
			k, v := pair, ""
			if j := strings.Index(pair, "="); j >= 0 {
				k, v = pair[:j], pair[j+1:]
			}
			if v == "" {
				continue
			}
			p.Qualifiers[strings.ToLower(k)] = v
		}
	}
	if i := strings.LastIndex(text, "@"); i >= 0 {
		p.Version = text[i+1:]
		text = text[:i]
	}
	var parts []string
	for _, seg := range strings.Split(text, "/") {
		if seg != "" {
			parts = append(parts, seg)
		}
	}
	if len(parts) == 0 {
		return p
	}
	p.Type = strings.ToLower(parts[0])
	rest := parts[1:]
	switch len(rest) {
	case 0:
	case 1:
		p.Name = rest[0]
	default:
		p.Namespace = rest[:len(rest)-1]
		p.Name = rest[len(rest)-1]
	}
	return p
}

// CanonicalPurl 是「先解析再重建」的规范化。
func CanonicalPurl(s string) string { return BuildPurl(ParsePurl(s)) }

// PurlKey 给出跨格式关联的规范化主键。
func PurlKey(s string) string {
	p := ParsePurl(s)
	keys := make([]string, 0, len(p.Qualifiers))
	for k := range p.Qualifiers {
		keys = append(keys, k+"="+p.Qualifiers[k])
	}
	sort.Strings(keys)
	return strings.Join([]string{
		p.Type,
		strings.Join(p.Namespace, "/"),
		p.Name,
		p.Version,
		strings.Join(keys, "&"),
		strings.Join(p.Subpath, "/"),
	}, "|")
}

// ---------------------------------------------------------------- vers

func vtuple(v string) []int {
	var out []int
	for _, seg := range strings.Split(v, ".") {
		n := 0
		for _, ch := range seg {
			if ch >= '0' && ch <= '9' {
				n = n*10 + int(ch-'0')
			}
		}
		out = append(out, n)
	}
	return out
}

// CmpVersion 按数值段比较版本号：字符串比较会给出相反结论（1.10 < 1.9）。
func CmpVersion(a, b string) int {
	ta, tb := vtuple(a), vtuple(b)
	n := len(ta)
	if len(tb) > n {
		n = len(tb)
	}
	for len(ta) < n {
		ta = append(ta, 0)
	}
	for len(tb) < n {
		tb = append(tb, 0)
	}
	for i := 0; i < n; i++ {
		if ta[i] < tb[i] {
			return -1
		}
		if ta[i] > tb[i] {
			return 1
		}
	}
	return 0
}

// VersMatches 判定 version 是否落在 vers 区间内（本 demo 支持的子集）。
func VersMatches(rangeStr, version string) bool {
	if !strings.HasPrefix(rangeStr, "vers:") {
		panic("not a vers range: " + rangeStr)
	}
	body := rangeStr[len("vers:"):]
	idx := strings.Index(body, "/")
	if idx < 0 {
		panic("missing vers type: " + rangeStr)
	}
	for _, c := range strings.Split(body[idx+1:], "|") {
		c = strings.TrimSpace(c)
		if c == "" {
			continue
		}
		matched := false
		for _, op := range []string{">=", "<=", "!=", ">", "<", "=="} {
			if !strings.HasPrefix(c, op) {
				continue
			}
			r := CmpVersion(version, c[len(op):])
			switch op {
			case ">=":
				matched = r >= 0
			case ">":
				matched = r > 0
			case "<=":
				matched = r <= 0
			case "<":
				matched = r < 0
			case "!=":
				matched = r != 0
			case "==":
				matched = r == 0
			}
			if !matched {
				return false
			}
			matched = true
			break
		}
		if !matched {
			// 裸版本：精确相等
			if CmpVersion(version, c) != 0 {
				return false
			}
		}
	}
	return true
}

// ---------------------------------------------------------------- VEX

// Vulnerability 对应 CycloneDX 的 vulnerability 条目。
type Vulnerability struct {
	ID            string
	Affects       []Affect
	State         string
	Justification string
}

// Affect 对应 vulnerability.affects[] 的一项。
type Affect struct {
	Ref      string
	Versions []map[string]string
}

// Bom 是 CycloneDX BOM 的最小子集。
type Bom struct {
	RefToVersion  map[string]string
	Dependencies  map[string][]string
	Vulnerabilities []Vulnerability
}

// VexStatus 判定某组件版本在 VEX 信息下的状态：affected / unaffected / unknown。
// 首个能匹配的条目胜出；条目内 status 缺省取 affected。
func VexStatus(b Bom, ref, version string) string {
	for _, v := range b.Vulnerabilities {
		for _, a := range v.Affects {
			if a.Ref != ref {
				continue
			}
			for _, item := range a.Versions {
				hit := false
				if ver, ok := item["version"]; ok {
					hit = CmpVersion(version, ver) == 0
				} else if rng, ok := item["range"]; ok {
					hit = VersMatches(rng, version)
				}
				if hit {
					if st, ok := item["status"]; ok {
						return st
					}
					return "affected"
				}
			}
		}
	}
	return "unknown"
}

// DepClosure 是 CycloneDX dependencies 的传递闭包（有环时自然终止）。
func DepClosure(b Bom, root string) []string {
	seen := map[string]bool{}
	var order []string
	queue := []string{root}
	for len(queue) > 0 {
		cur := queue[0]
		queue = queue[1:]
		for _, nxt := range b.Dependencies[cur] {
			if seen[nxt] {
				continue
			}
			seen[nxt] = true
			order = append(order, nxt)
			queue = append(queue, nxt)
		}
	}
	return order
}

// ---------------------------------------------------------------- SPDX

const (
	DependsOn   = "DEPENDS_ON"
	DependencyOf = "DEPENDENCY_OF"
	Describes   = "DESCRIBES"
)

// Relationship 是 SPDX 的 (from, type, to) 三元组。
type Relationship struct {
	From string
	Type string
	To   string
}

// DependsOnEdges 把 DEPENDS_ON 与 DEPENDENCY_OF 归一化成 (from, to)。
func DependsOnEdges(rs []Relationship) [][2]string {
	var out [][2]string
	for _, r := range rs {
		switch r.Type {
		case DependsOn:
			out = append(out, [2]string{r.From, r.To})
		case DependencyOf:
			out = append(out, [2]string{r.To, r.From})
		}
	}
	return out
}
