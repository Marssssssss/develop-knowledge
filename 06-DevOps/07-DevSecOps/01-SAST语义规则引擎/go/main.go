package main

// main.go 只做演示：把 python/selfcheck_rule.py 里的关键结论在 Go 侧跑一遍，
// 便于与 Python 实现逐条对拍（本机无 Go 工具链时以静态检查 + 人工审查替代）。

import (
	"fmt"

	s "semgrep"
)

func lit(v int) s.Node { return s.Node{Kind: "E", Value: v} }

func id(name string, info *s.IdInfo) s.Node { return s.Node{Kind: "Id", Name: name, Info: info} }

func mv(name string) s.Pat { return s.Pat{Kind: "Metavar", Name: name, IsMetav: true} }

func mvEll(name string) s.Pat { return s.Pat{Kind: "MetavarEllipsis", Name: name} }

func litPat(v int) s.Pat { return s.Pat{Kind: "E", Value: v} }

func dots() s.Pat { return s.Pat{Kind: "Dots"} }

func elem(p s.Pat, c s.Node, e s.Env) []s.Env {
	cfg := s.Config{}
	if p.Kind == "Metavar" {
		ne := s.CheckAndAddBinding(e, p.Name, c, cfg)
		if ne == nil {
			return nil
		}
		return []s.Env{ne}
	}
	if p.Kind == "E" && c.Kind == "E" && p.Value == c.Value {
		return []s.Env{e}
	}
	return nil
}

func main() {
	cfg := s.Config{}

	// A1: [..., 3, ...] 对 [1,2,3,4] 走优化分支
	got := s.MatchListDotsMetavarEllipsis(
		[]s.Pat{dots(), litPat(3), dots()},
		[]s.Node{lit(1), lit(2), lit(3), lit(4)}, s.Env{}, false, elem, cfg)
	fmt.Println("A1 matches:", len(got))

	// A3: [..., $X, ...] 每个元素各绑定一次
	got = s.MatchListDotsMetavarEllipsis(
		[]s.Pat{dots(), mv("X"), dots()},
		[]s.Node{lit(1), lit(2), lit(3), lit(4)}, s.Env{}, false, elem, cfg)
	fmt.Print("A3 X values:")
	for _, e := range got {
		fmt.Print(" ", e["X"].Value)
	}
	fmt.Println()

	// B3/B4: less_is_ok 决定 $...ARGS 的绑定个数
	for _, lio := range []bool{false, true} {
		g := s.MatchListDotsMetavarEllipsis([]s.Pat{mvEll("ARGS")},
			[]s.Node{lit(1), lit(2)}, s.Env{}, lio, elem, cfg)
		fmt.Printf("B less_is_ok=%v bindings=%d\n", lio, len(g))
	}

	// C6: Some resolved vs 无 id_info 的不对称
	resolved := id("x", &s.IdInfo{HasResolved: true, Resolved: "Local", SID: 7})
	noInfo := id("x", nil)
	fmt.Println("C6 resolved==noInfo:", s.EqualBoundCode(resolved, noInfo, cfg))
	fmt.Println("C6b noInfo==resolved:", s.EqualBoundCode(noInfo, resolved, cfg))

	// E: import 归一化
	mods := s.FullModuleNames(true, s.ModuleName{Idents: []string{"foo"}}, []string{"bar"})
	fmt.Println("E1:", s.RenderModule(mods[0]))
	jsMods := s.FullModuleNames(false, s.ModuleName{IsFileName: true, Path: "path"}, []string{"x"})
	fmt.Println("E6:", s.RenderModule(jsMods[0]))
	patMods := s.FullModuleNames(true, s.ModuleName{IsFileName: true, Path: "path"}, []string{"x"})
	fmt.Println("E5 is nil:", patMods == nil)
}
