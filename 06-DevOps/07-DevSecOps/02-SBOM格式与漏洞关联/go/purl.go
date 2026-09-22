// Package sbom 的 PURL 部分，对应 package-url/purl-spec 第 5 章
// 与 docs/specification/how-to-build.md，是 python/purl.py 的 Go 转写。
package sbom

import (
	"sort"
	"strings"
)

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

// BuildPurl 按 how-to-build 逐条构造规范形式。
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
	// 版本只在最后一个 '@' 处切分：name 里可能出现编码后的 '@'
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
