// PathMatchModes 自检：以官方文档两页给出的确切结果为断言。
package main

import "fmt"

var okCount int
var failures []string

func ck(name string, cond bool, detail string) {
	if cond {
		okCount++
		return
	}
	failures = append(failures, fmt.Sprintf("%s  %s", name, detail))
}

func eqInt(name string, got, want int) {
	ck(name, got == want, fmt.Sprintf("got=%d want=%d", got, want))
}

func eqStr(name, got, want string) {
	ck(name, got == want, fmt.Sprintf("got=%s want=%s", got, want))
}

func hasPath(ps []Path, nodes ...string) bool {
	for _, p := range ps {
		if len(p.Nodes) != len(nodes) {
			continue
		}
		same := true
		for i := range nodes {
			if p.Nodes[i] != nodes[i] {
				same = false
				break
			}
		}
		if same {
			return true
		}
	}
	return false
}

func allNodesUnique(ps []Path) bool {
	for _, p := range ps {
		seen := map[string]bool{}
		for _, n := range p.Nodes {
			if seen[n] {
				return false
			}
			seen[n] = true
		}
	}
	return true
}

func anyNodeRepeated(ps []Path) bool {
	for _, p := range ps {
		seen := map[string]bool{}
		for _, n := range p.Nodes {
			if seen[n] {
				return true
			}
			seen[n] = true
		}
	}
	return false
}

func anyRelRepeated(ps []Path) bool {
	for _, p := range ps {
		seen := map[string]bool{}
		for _, r := range p.Rels {
			if seen[r] {
				return true
			}
			seen[r] = true
		}
	}
	return false
}

func main() {
	// ---------------------------------------------------------- 七桥图
	g := Konigsberg()

	// 官方：定长 5 有向 → 2 行，桥序列 [1,5,6,4,7] 与 [6,4,1,5,7]
	r5 := g.Match(query{start: "Kneiphof", mode: Trail, hops: 5,
		rtype: "BRIDGE", directed: true})
	eqInt("七桥/定长5/有向/TRAIL 条数", len(r5), 2)
	got := ""
	for _, p := range r5 {
		if len(got) > 0 {
			got += "|"
		}
		for _, r := range p.Rels {
			got += r
		}
	}
	eqStr("七桥/定长5/桥序列", got, "15647|64157")

	eqInt("七桥/定长6/无向/TRAIL 条数",
		g.Count(query{start: "Kneiphof", mode: Trail, hops: 6}), 48)
	eqInt("七桥/定长7/无向/TRAIL 条数",
		g.Count(query{start: "Kneiphof", mode: Trail, hops: 7}), 0)

	w7 := g.Count(query{start: "Kneiphof", mode: Walk, hops: 7})
	ck("七桥/定长7/无向/WALK 条数 > 0", w7 > 0, fmt.Sprintf("walk=%d", w7))

	w6 := g.Count(query{start: "Kneiphof", mode: Walk, hops: 6})
	t6 := g.Count(query{start: "Kneiphof", mode: Trail, hops: 6})
	a6 := g.Count(query{start: "Kneiphof", mode: Acyclic, hops: 6})
	ck("七桥/WALK > TRAIL >= ACYCLIC", w6 > t6 && t6 >= a6,
		fmt.Sprintf("w=%d t=%d a=%d", w6, t6, a6))

	ck("七桥/ACYCLIC 节点必唯一",
		allNodesUnique(g.Match(query{start: "Kneiphof", mode: Acyclic, hops: 6})), "")
	ck("七桥/TRAIL 存在节点重复",
		anyNodeRepeated(g.Match(query{start: "Kneiphof", mode: Trail, hops: 6})), "")
	ck("七桥/TRAIL 关系不重复",
		!anyRelRepeated(g.Match(query{start: "Kneiphof", mode: Trail, hops: 6})), "")
	ck("七桥/WALK 存在关系重复",
		anyRelRepeated(g.Match(query{start: "Kneiphof", mode: Walk, hops: 6})), "")

	eqInt("七桥/ACYCLIC 4 跳为 0",
		g.Count(query{start: "Kneiphof", mode: Acyclic, hops: 4}), 0)

	// ---------------------------------------------------------- 路由器网
	r := RouterNetwork()
	paths := r.Match(query{start: "A", mode: Acyclic, end: "Z", rtype: "LINK"})
	eqInt("路由/ACYCLIC A→Z 路径条数", len(paths), 80)
	eqInt("路由/总数可被 8 整除", len(paths)%8, 0)

	mids := map[string]int{}
	for _, p := range paths {
		for _, n := range p.Nodes[1 : len(p.Nodes)-1] {
			mids[n]++
		}
	}
	official := map[string]float64{"G": 100.0, "J": 87.5, "C": 80.0, "E": 70.0,
		"K": 62.5, "D": 60.0, "B": 60.0, "I": 50.0, "F": 40.0, "H": 25.0}
	for _, n := range []string{"G", "J", "C", "E", "K", "D", "B", "I", "F", "H"} {
		pct := float64(mids[n]) * 100.0 / float64(len(paths))
		ck("路由/占比 "+n, pct > official[n]-1e-9 && pct < official[n]+1e-9,
			fmt.Sprintf("got=%v want=%v", pct, official[n]))
	}
	eqInt("路由/中间路由器种类数", len(mids), 10)
	ck("路由/存在 A-B-E-G-J-I-Z",
		hasPath(paths, "A", "B", "E", "G", "J", "I", "Z"), "")

	tr := r.Match(query{start: "A", mode: Trail, end: "Z", rtype: "LINK",
		maxHops: 8})
	ck("路由/TRAIL 存在官方含环路径 A-B-E-G-J-I-H-J-Z",
		hasPath(tr, "A", "B", "E", "G", "J", "I", "H", "J", "Z"), "")
	ck("路由/ACYCLIC 不存在节点重复", allNodesUnique(paths), "")
	ck("路由/TRAIL 存在节点重复", anyNodeRepeated(tr), "")

	w8 := r.Match(query{start: "A", mode: Walk, end: "Z", rtype: "LINK",
		maxHops: 8})
	ck("路由/WALK(≤8) 严格多于 TRAIL(≤8)", len(w8) > len(tr),
		fmt.Sprintf("walk=%d trail=%d", len(w8), len(tr)))
	ck("路由/WALK 存在关系重复", anyRelRepeated(w8), "")

	dir := r.Match(query{start: "A", mode: Acyclic, end: "Z", rtype: "LINK",
		directed: true})
	eqInt("路由/有向遍历 A→Z 条数", len(dir), 15)
	ck("路由/有向遍历漏掉官方路径", !hasPath(dir, "A", "B", "E", "G", "J", "I", "Z"), "")

	// ---------------------------------------------------------- 模式语义
	func() {
		defer func() {
			ck("模式/未知模式 panic", recover() != nil, "no panic")
		}()
		Allows("NOPE", map[string]bool{}, map[string]bool{},
			&Rel{ID: "x"}, "y")
	}()

	t := NewGraph()
	t.AddNode("X")
	t.AddNode("Y")
	t.AddRel("r1", "E", "X", "Y")
	t.AddRel("r2", "E", "Y", "X")
	eqInt("最小图/ACYCLIC 2 跳为 0",
		t.Count(query{start: "X", mode: Acyclic, hops: 2}), 0)
	eqInt("最小图/TRAIL 2 跳为 2",
		t.Count(query{start: "X", mode: Trail, hops: 2}), 2)
	eqInt("最小图/WALK 2 跳为 4",
		t.Count(query{start: "X", mode: Walk, hops: 2}), 4)
	eqInt("最小图/有向 TRAIL 2 跳为 1",
		t.Count(query{start: "X", mode: Trail, hops: 2, directed: true}), 1)

	fmt.Printf("断言通过: %d\n", okCount)
	if len(failures) > 0 {
		fmt.Printf("失败 %d 条:\n", len(failures))
		for _, f := range failures {
			fmt.Println("  - " + f)
		}
		return
	}
	fmt.Println("ALL OK")
}
