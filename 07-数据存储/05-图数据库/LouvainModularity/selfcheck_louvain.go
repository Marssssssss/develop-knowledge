// LouvainModularity 自检：模块度、ΔQ 公式、聚合不变性、两阶段迭代。
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

func eqF(name string, got, want float64) {
	ck(name, abs(got-want) < 1e-9,
		fmt.Sprintf("got=%.12g want=%.12g", got, want))
}

func nComm(part map[int]int) int { return len(groupBy(part)) }

func blocks(part map[int]int) string {
	return fmt.Sprint(groupBy(part))
}

func main() {
	// ---------------------------------------------- 基础量
	g := NewGraph()
	for _, e := range [][2]int{{0, 1}, {0, 2}, {1, 2}, {3, 4}, {3, 5}, {4, 5},
		{2, 3}} {
		g.AddEdge(e[0], e[1], 1.0)
	}
	eqF("m = 边数", g.M(), 7.0)
	eqF("k(0) = 2", g.K(0), 2.0)
	eqF("k(2) = 3", g.K(2), 3.0)
	sumK := 0.0
	for _, n := range g.Nodes {
		sumK += g.K(n)
	}
	eqF("Σk = 2m", sumK, 2*g.M())

	// ---------------------------------------------- 模块度
	single := map[int]int{}
	for _, n := range g.Nodes {
		single[n] = n
	}
	eqF("Q(singleton)", Modularity(g, single), -0.17346938775510204)
	two := map[int]int{0: 0, 1: 0, 2: 0, 3: 1, 4: 1, 5: 1}
	eqF("Q(两个三角形)", Modularity(g, two), 0.35714285714285715)
	ck("两三角形划分优于单点划分", Modularity(g, two) > Modularity(g, single), "")
	allOne := map[int]int{}
	for _, n := range g.Nodes {
		allOne[n] = 0
	}
	eqF("全图一个社区 Q = 0", Modularity(g, allOne), 0.0)
	for _, p := range []map[int]int{single, two, allOne} {
		q := Modularity(g, p)
		ck("Q 落在 [-1,1]", q >= -1.0 && q <= 1.0, fmt.Sprintf("q=%v", q))
	}

	// ---------------------------------------------- ΔQ 公式 vs 重算
	part := map[int]int{0: 0, 1: 0, 2: 0, 3: 1, 4: 1, 5: 1}
	eqF("ΔQ_add({3,4,5})", DeltaQAdd(g, part, 2, []int{3, 4, 5}),
		-0.07142857142857142)
	eqF("ΔQ_add({0,1})", DeltaQAdd(g, part, 2, []int{0, 1}),
		0.16326530612244897)
	moved := map[int]int{}
	for k, v := range part {
		moved[k] = v
	}
	moved[2] = 1
	eqF("净增益 = ΔQ_add(C) − ΔQ_add(D\\{i})",
		DeltaQAdd(g, part, 2, []int{3, 4, 5})-DeltaQAdd(g, part, 2, []int{0, 1}),
		Modularity(g, moved)-Modularity(g, part))
	iso := map[int]int{0: 0, 1: 0, 2: 2, 3: 1, 4: 1, 5: 1}
	post := map[int]int{}
	for k, v := range iso {
		post[k] = v
	}
	post[2] = 0
	eqF("孤立节点移入：公式 = 重算差", DeltaQAdd(g, iso, 2, []int{0, 1}),
		Modularity(g, post)-Modularity(g, iso))
	eqF("ΔQ_add(空社区) = 0", DeltaQAdd(g, part, 2, []int{}), 0.0)

	// ---------------------------------------------- 自环约定
	sg := NewGraph()
	sg.AddEdge(1, 2, 3.0)
	sg.AddEdge(2, 3, 4.0)
	sg.AddEdge(1, 1, 5.0)
	eqF("A(a,a) = 2·s", sg.A(1, 1), 10.0)
	eqF("m = 边权之和（自环按一次计）", sg.M(), 12.0)
	eqF("自环只记一次时 m 变小（反例）", sg.MOnce(), 9.5)
	ck("自环只记一次时 m 与总边权不等（反例）", abs(sg.MOnce()-sg.M()) > 1e-9, "")

	// ---------------------------------------------- 聚合不变性
	pAgg := map[int]int{1: 0, 2: 0, 3: 1}
	qBefore := Modularity(sg, pAgg)
	sup := Aggregate(sg, pAgg)
	eqF("聚合后 m 不变", sup.M(), sg.M())
	singletonSup := map[int]int{}
	for _, c := range sup.Nodes {
		singletonSup[c] = c
	}
	eqF("聚合前后 Q 不变", Modularity(sup, singletonSup), qBefore)
	eqF("聚合产生自环（内部边 + 原有自环）", sup.A(0, 0), 16.0)
	eqF("聚合后跨社区边权", sup.A(0, 1), 4.0)
	ck("自环只记一次会破坏 m 不变（反例）", abs(sup.MOnce()-sup.M()) > 1e-9, "")

	// ---------------------------------------------- 第一阶段
	g2 := NewGraph()
	for _, e := range [][2]int{{0, 1}, {0, 2}, {1, 2}, {3, 4}, {3, 5}, {4, 5},
		{2, 3}} {
		g2.AddEdge(e[0], e[1], 1.0)
	}
	start2 := map[int]int{}
	for _, n := range g2.Nodes {
		start2[n] = n
	}
	p1, q1, _ := OneLevel(g2, start2, 20, 1e-7)
	eqInt("第一阶段社区数", nComm(p1), 2)
	eqF("第一阶段 Q", q1, 0.35714285714285715)
	p1b, q1b, _ := OneLevel(g2, p1, 20, 1e-7)
	eqF("再跑一次 Q 不变（局部极大）", q1b, q1)
	ck("再跑一次划分不变", blocks(p1b) == blocks(p1), blocks(p1b)+" vs "+blocks(p1))

	// ---------------------------------------------- 完整 Louvain
	fin, qf, levels, _ := Louvain(g2, 10, 20, 1e-7)
	eqInt("Louvain 最终社区数", nComm(fin), 2)
	eqF("Louvain 最终 Q", qf, 0.35714285714285715)
	mono := true
	for k := 0; k+1 < len(levels); k++ {
		if levels[k+1] < levels[k]-1e-12 {
			mono = false
		}
	}
	ck("每个 level 的 Q 单调不减", mono, fmt.Sprint(levels))

	_, _, itT := OneLevel(g2, start2, 20, 10.0)
	ck("tolerance 极大时立即视为稳定", itT <= 2, fmt.Sprintf("iters=%d", itT))

	// ---------------------------------------------- ring of cliques
	rg := RingOfCliques(30, 5)
	eqInt("ring 30×5 节点数", len(rg.Nodes), 150)
	eqF("ring 30×5 边数", rg.M(), 30*10+30)
	rfin, rq, rlevels, _ := Louvain(rg, 10, 20, 1e-7)
	eqInt("最终社区数 = 15（团两两合并）", nComm(rfin), 15)
	ck("最终 Q ≈ 0.888", abs(rq-0.8878) < 0.002, fmt.Sprintf("q=%.6f", rq))
	monoR := true
	for k := 0; k+1 < len(rlevels); k++ {
		if rlevels[k+1] < rlevels[k]-1e-12 {
			monoR = false
		}
	}
	ck("level 序列单调不减", monoR, fmt.Sprint(rlevels))

	startR := map[int]int{}
	for _, n := range rg.Nodes {
		startR[n] = n
	}
	rp1, rq1, _ := OneLevel(rg, startR, 20, 1e-7)
	eqInt("第一阶段社区数 = 30", nComm(rp1), 30)
	eqF("第一阶段 Q", rq1, rlevels[0])
	sizes := map[int]int{}
	for _, c := range rp1 {
		sizes[c]++
	}
	allFive := true
	for _, s := range sizes {
		if s != 5 {
			allFive = false
		}
	}
	ck("每个社区 5 个节点", allFive, fmt.Sprint(sizes))

	// ---------------------------------------------- 分辨率极限
	natural30 := map[int]int{}
	merged15 := map[int]int{}
	for _, n := range rg.Nodes {
		natural30[n] = n / 5
		merged15[n] = (n / 5) / 2
	}
	q30, q15 := Modularity(rg, natural30), Modularity(rg, merged15)
	ck("30 团图：合并成 15 个的 Q 更高（分辨率极限征兆）", q15 > q30,
		fmt.Sprintf("q30=%.6f q15=%.6f", q30, q15))
	eqF("自然划分的 Q 等于第一阶段 Q", q30, rlevels[0])

	tiny := NewGraph()
	for c := 0; c < 2; c++ {
		b := c * 5
		for i := 0; i < 5; i++ {
			for j := i + 1; j < 5; j++ {
				tiny.AddEdge(b+i, b+j, 1.0)
			}
		}
	}
	tiny.AddEdge(0, 5, 1.0)
	eqF("两团图边数", tiny.M(), 21.0)
	tfin, _, _, _ := Louvain(tiny, 10, 20, 1e-7)
	eqInt("两个 5 阶团保持 2 个社区", nComm(tfin), 2)
	nat2, one2 := map[int]int{}, map[int]int{}
	for _, n := range tiny.Nodes {
		nat2[n] = n / 5
		one2[n] = 0
	}
	ck("两团图：自然划分 Q 高于合成一个",
		Modularity(tiny, nat2) > Modularity(tiny, one2), "")

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
