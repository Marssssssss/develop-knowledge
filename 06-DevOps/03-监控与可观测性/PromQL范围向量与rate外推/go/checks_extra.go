package main

import (
	"fmt"
	"math"
)

func groupF() {
	st := NewMemStore(0)
	for _, t := range []float64{0, 60, 300, 600, 700} {
		mustAdd(st, "x", map[string]string{}, t, t)
	}
	e := NewEngine(st)
	r1, _ := e.Instant("x offset 5m", 700)
	r2, _ := e.Instant("x", 400)
	check("F offset 5m 等价于在 eval-300 求值",
		len(r1.Points) == 1 && len(r2.Points) == 1 && r1.Points[0].V == r2.Points[0].V, "")

	r3, _ := e.Instant("x @ 700 offset 5m", 99999)
	r4, _ := e.Instant("x offset 5m @ 700", 0)
	check("F @ 与 offset 顺序无关",
		r3.Points[0].V == r4.Points[0].V && r3.Points[0].V == 300, "")

	r5, _ := e.Instant("x offset -5m", 0)
	check("F 负 offset 可看向求值时刻之后（look ahead）",
		len(r5.Points) == 1 && r5.Points[0].V == 300, fmt.Sprint(r5.Points))

	check("F offset 必须紧跟选择器（rate(x[5m]) offset 5m 非法）",
		parseErr("rate(x[5m]) offset 5m"), "")
	if _, err := Parse("rate(x[5m] offset 5m)"); err != nil {
		panic(err)
	}
	check("F offset 写在范围选择器之后合法", true, "")

	for _, t := range []float64{0, 60, 120, 180, 240, 300, 600} {
		mustAdd(st, "s", map[string]string{}, t, t)
	}
	rs, _ := e.Instant("s @ start()", 600)
	re, _ := e.Instant("s @ end()", 600)
	check("F 即时查询里 start()/end() 都解析为求值时刻",
		rs.Points[0].V == 600 && re.Points[0].V == 600, "")
}

func counterStore() (*MemStore, *MemStore) {
	st := NewMemStore(0)
	for _, c := range []struct {
		name string
		ts   []float64
	}{
		{"A", []float64{60, 120, 180, 240, 300}},
		{"B", []float64{60, 120, 180, 240}},
		{"C", []float64{60, 120, 180}},
		{"one", []float64{300}},
	} {
		for _, t := range c.ts {
			mustAdd(st, "cnt", map[string]string{"case": c.name}, t, 0.1*t)
		}
	}
	st2 := NewMemStore(0)
	for _, p := range [][2]float64{{60, 5}, {120, 10}, {180, 2}, {240, 7}} {
		mustAdd(st2, "rst", map[string]string{"case": "reset"}, p[0], p[1])
	}
	for _, p := range [][2]float64{{90, 5}, {150, 10}, {210, 15}, {270, 25}} {
		mustAdd(st2, "zro", map[string]string{"case": "zero"}, p[0], p[1])
	}
	return st, st2
}

func groupG() {
	st, st2 := counterStore()
	e := NewEngine(st)
	e2 := NewEngine(st2)

	rateOf := func(eng *Engine, name, c string) float64 {
		r, err := eng.Instant(fmt.Sprintf(`rate(%s{case="%s"}[5m])`, name, c), 300)
		if err != nil || len(r.Points) == 0 {
			return math.NaN()
		}
		return r.Points[0].V
	}

	check("G 均匀抓取且末样本落在右边界 → rate 精确",
		approx(rateOf(e, "cnt", "A"), 0.1, 1e-9), fmt.Sprint(rateOf(e, "cnt", "A")))
	check("G 末样本距右边界 60s（< 阈值 66s）→ 仍精确",
		approx(rateOf(e, "cnt", "B"), 0.1, 1e-9), fmt.Sprint(rateOf(e, "cnt", "B")))
	check("G 末样本距右边界 120s（≥ 阈值）→ 只外推 avg/2，低估 30%",
		approx(rateOf(e, "cnt", "C"), 0.07, 1e-9), fmt.Sprint(rateOf(e, "cnt", "C")))

	// 阈值平局：avg=60 → 阈值 66；末样本恰在 234 使 durationToEnd == 66
	tie := []Sample{{54, 5.4}, {114, 11.4}, {174, 17.4}, {234, 23.4}}
	v, _ := ExtrapolatedRate(tie, 0, 300, true, true, "threshold_first")
	loose := 18 * (300.0 / 180.0) / 300.0
	check("G 判据是 >= 阈值（恰等于阈值即降级为 avg/2）",
		approx(v, 18*(264.0/180.0)/300.0, 1e-9) && !approx(v, loose, 1e-9), fmt.Sprint(v))

	ri, err := e2.Instant(`increase(rst{case="reset"}[5m])`, 300)
	if err != nil || len(ri.Points) == 0 {
		panic("reset case failed")
	}
	check("G 计数器重置被补偿：raw = (7-5)+10 = 12",
		approx(ri.Points[0].V, 12*300.0/180.0, 1e-9), fmt.Sprint(ri.Points[0].V))
	check("G 不补偿时的 last-first 只有 2/12（差 6 倍）",
		approx(ri.Points[0].V/(2*300.0/180.0), 6.0, 1e-9), "")

	rr, err := e2.Instant(`resets(rst{case="reset"}[5m])`, 300)
	check("G resets() 正确计数 1 次",
		err == nil && len(rr.Points) == 1 && rr.Points[0].V == 1, "")

	pz := []Sample{{90, 5}, {150, 10}, {210, 15}, {270, 25}}
	oa, _ := ExtrapolatedRate(pz, 0, 300, true, true, "threshold_first")
	ob, _ := ExtrapolatedRate(pz, 0, 300, true, true, "zero_first")
	check("G 计数器零点截断把 dStart 从 150 拉到 17.14，影响外推量",
		approx(oa*300, 20*240.0/180.0, 1e-9), "")
	check("G 两种分支顺序在此例给出不同结果（dStart 30 vs 45）",
		!approx(oa, ob, 1e-9), fmt.Sprintf("%v vs %v", oa, ob))

	ro, err := e.Instant(`rate(cnt{case="one"}[5m])`, 300)
	check("G 窗口内只有 1 个样本 → 无值（len<2 丢弃）",
		err == nil && len(ro.Points) == 0, "")

	ra, _ := e.Instant(`rate(cnt{case="A"}[5m])`, 300)
	ia, _ := e.Instant(`increase(cnt{case="A"}[5m])`, 300)
	check("G increase = rate × 范围秒数（官方称语法糖）",
		approx(ia.Points[0].V, ra.Points[0].V*300, 1e-9), "")

	mx, _ := e.Instant(`cnt{case="A"}[5m]`, 300)
	check("G 裸范围向量求值返回矩阵", mx.IsMatrix && len(mx.Matrix[0].Pts) == 5, "")
}

func groupH() {
	st := NewMemStore(0)
	for t := 0.0; t <= 600; t += 10 {
		mustAdd(st, "q", map[string]string{"job": "api"}, t, 0.1*t)
	}
	e := NewEngine(st)
	r1, err := e.Instant("q[10m:60s]", 600)
	if err != nil {
		panic(err)
	}
	check("H 子查询显式 resolution=60s → 11 步", len(r1.Matrix[0].Pts) == 11,
		fmt.Sprint(len(r1.Matrix[0].Pts)))
	r2, _ := e.Instant("q[10m:30s]", 600)
	check("H 子查询显式 30s → 21 步", len(r2.Matrix[0].Pts) == 21,
		fmt.Sprint(len(r2.Matrix[0].Pts)))

	r3, err := e.Instant(`rate(q{job="api"}[1m])[10m:60s]`, 600)
	if err != nil {
		panic(err)
	}
	vals := r3.Matrix[0].Pts
	okAll := true
	for _, p := range vals {
		if !approx(p.V, 0.1, 1e-9) {
			okAll = false
		}
	}
	check("H 子查询内层求 rate：10 个有效步都精确 0.1",
		len(vals) == 10 && okAll, fmt.Sprint(len(vals)))
	check("H resolution 必须为正", func() bool {
		_, err := e.Instant("q[10m:0s]", 600)
		return err != nil
	}(), "")
}
