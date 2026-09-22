// Package sbom 的 vers / VEX / SPDX 部分，是 python/main.py 的 Go 转写。
//
// 对应规范：
//   CycloneDX specification 1.6 schema（vulnerability / affects / affectedStatus）
//   SPDX 2.3 chapters/relationships-between-SPDX-elements.md
package sbom

import "strings"

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
// 未支持的写法 panic，绝不静默放行。
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
	RefToVersion    map[string]string
	Dependencies    map[string][]string
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

// SPDX 关系类型常量。
const (
	DependsOn    = "DEPENDS_ON"
	DependencyOf = "DEPENDENCY_OF"
	Describes    = "DESCRIBES"
	DescribedBy  = "DESCRIBED_BY"
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

// CdxToSpdxRelationships 把 CycloneDX 的 dependencies 转成 SPDX 的 DEPENDS_ON 边。
func CdxToSpdxRelationships(b Bom) []Relationship {
	var out []Relationship
	for ref, deps := range b.Dependencies {
		for _, d := range deps {
			out = append(out, Relationship{From: ref, Type: DependsOn, To: d})
		}
	}
	return out
}
