package main

// main.go 把 python/selfcheck_sbom.py 里的关键结论在 Go 侧跑一遍，
// 便于与 Python 实现逐条对拍（本机无 Go 工具链时以静态检查 + 人工审查替代）。

import (
	"fmt"

	s "sbom"
)

func main() {
	// P1/P2/P5：PURL 规范化
	fmt.Println("P1:", s.CanonicalPurl("pkg:NPM/foo"))
	fmt.Println("P2:", s.CanonicalPurl("pkg:npm/foo@1.0.0?b=2&a=1"))
	fmt.Println("P5:", s.CanonicalPurl("pkg:npm/foo#./a/../b/"))
	fmt.Println("P7:", s.CanonicalPurl("pkg://npm/foo"))
	fmt.Println("P9:", s.PctEncode("my pkg"))
	fmt.Println("P10:", s.PctEncode("a:b"))

	// M1：版本必须按数值段比较
	fmt.Println("M1 cmp(1.10,1.9):", s.CmpVersion("1.10", "1.9"))
	fmt.Printf("M1b string cmp: %v\n", "1.10" < "1.9")

	// M2/M3：vers 区间
	fmt.Println("M2:", s.VersMatches("vers:npm/>=1.0.0|<2.0.0", "1.9.9"))
	fmt.Println("M3:", s.VersMatches("vers:npm/>=1.0.0|<2.0.0", "2.0.0"))

	bom := s.Bom{
		RefToVersion: map[string]string{"c1": "1.5.0"},
		Vulnerabilities: []s.Vulnerability{
			{ID: "CVE-1", Affects: []s.Affect{
				{Ref: "c1", Versions: []map[string]string{{"range": "vers:npm/>=1.0.0|<2.0.0"}}},
			}},
		},
	}
	fmt.Println("V1 (缺省 affected):", s.VexStatus(bom, "c1", "1.5.0"))
	fmt.Println("V3 (无匹配 unknown):", s.VexStatus(bom, "c1", "9.9.9"))

	// X2/X3：依赖图与传递闭包
	g := s.Bom{Dependencies: map[string][]string{"a": {"b"}, "b": {"c"}}}
	fmt.Println("X2 closure(a):", s.DepClosure(g, "a"))
	cyc := s.Bom{Dependencies: map[string][]string{"a": {"b"}, "b": {"a"}}}
	fmt.Println("X3 closure(a) on cycle:", s.DepClosure(cyc, "a"))

	// S3：DEPENDENCY_OF 与 DEPENDS_ON 互逆
	edges := s.DependsOnEdges([]s.Relationship{
		{From: "SPDXRef-A", Type: s.DependencyOf, To: "SPDXRef-B"},
	})
	fmt.Println("S3:", edges)

	// X1：跨格式主键
	fmt.Println("X1 same:", s.PurlKey("pkg:npm/foo@1.0.0") == s.PurlKey("pkg:NPM/foo@1.0.0"))
}
