// PromQL 范围向量语义与 rate/increase 外推 —— Go 版自检。
//
// 运行：go run .
// 覆盖 8 组语义：A 选择器合法性 / B 空值匹配 / C 正则锚定 / D lookback 与 staleness
// E 范围向量左开右闭 / F offset 与 @ / G 外推与计数器重置 / H 子查询
// （F/G/H 在 checks_extra.go，同一 main 包）
package main

import (
	"fmt"
	"math"
	"os"
)

var pass, failed int
var failures []string

func check(label string, cond bool, detail string) {
	if cond {
		pass++
		return
	}
	failed++
	failures = append(failures, label+" | "+detail)
	fmt.Printf("FAIL  %s | %s\n", label, detail)
}

func approx(a, b, tol float64) bool { return math.Abs(a-b) < tol }

func parseErr(text string) bool {
	_, err := Parse(text)
	return err != nil
}

func mustAdd(st *MemStore, name string, labels map[string]string, t, v float64) {
	if err := st.Add(name, labels, t, v); err != nil {
		panic(err)
	}
}

func sel(st *MemStore, text string, evalT float64) []Point {
	ex, err := Parse(text)
	if err != nil {
		panic(err)
	}
	return st.Select(ex.Name, ex.Matchers, evalT)
}

func groupA() {
	for _, bad := range []string{`{job=~".*"}`, `{job!="x"}`, `{env=~""}`,
		`{__name__=~".*"}`, `{a=~".*",b=~".*"}`} {
		check("A 非法选择器应报错", parseErr(bad), bad)
	}
	for _, good := range []string{`{job=~".+"}`, `{job="x"}`, `{job!=""}`,
		`{__name__=~".+"}`, `{job=~".*",method="get"}`, `up`} {
		_, err := Parse(good)
		check("A 合法选择器应通过", err == nil, good)
	}
	check("A 无 matcher 的裸花括号非法", parseErr("{}"), "{}")
}

func groupB() {
	st := NewMemStore(0)
	mustAdd(st, "http_requests_total", map[string]string{}, 0, 1)
	mustAdd(st, "http_requests_total", map[string]string{"replica": "rep-a"}, 0, 1)
	mustAdd(st, "http_requests_total", map[string]string{"replica": "rep-b"}, 0, 1)
	mustAdd(st, "http_requests_total", map[string]string{"environment": "development"}, 0, 1)

	got := sel(st, `http_requests_total{environment=""}`, 10)
	check("B 匹配空值 = 无该标签也命中", len(got) == 3, fmt.Sprint(len(got)))
	allNoEnv := true
	for _, p := range got {
		if _, ok := p.Labels["environment"]; ok {
			allNoEnv = false
		}
	}
	check("B 有 environment 标签的被排除", allNoEnv, fmt.Sprint(got))

	got = sel(st, `http_requests_total{replica!="rep-a",replica=~"rep.*"}`, 10)
	check("B 同标签多 matcher 全通过才命中", len(got) == 1, fmt.Sprint(len(got)))
	check("B 同标签多 matcher 命中 rep-b",
		len(got) == 1 && got[0].Labels["replica"] == "rep-b", fmt.Sprint(got))
}

func groupC() {
	st := NewMemStore(0)
	for _, r := range []string{"rep-a", "rep-b", "replication"} {
		mustAdd(st, "up", map[string]string{"replica": r}, 0, 1)
	}
	check("C =~ 完全锚定（rep 不匹配 replication）",
		len(sel(st, `up{replica=~"rep"}`, 10)) == 0, "")
	check("C =~ 加 .* 才匹配前缀",
		len(sel(st, `up{replica=~"rep.*"}`, 10)) == 3, "")
	check("C =~ ^rep$ 与 rep 等价（锚定口径）",
		len(sel(st, `up{replica=~"^rep$"}`, 10)) == 0, "")
}

func groupD() {
	st := NewMemStore(300)
	mustAdd(st, "m", map[string]string{"a": "1"}, 1000, 7)
	check("D 距求值恰 300s（=lookback）不返回", len(sel(st, "m", 1300)) == 0, "")
	check("D 距求值 299s 返回", len(sel(st, "m", 1299)) == 1, "")
	check("D 取 at-or-before 的最新样本（未来样本不可见）",
		len(sel(st, "m", 1000)) == 1 && sel(st, "m", 1000)[0].V == 7, "")
	if err := st.MarkStale("m", map[string]string{"a": "1"}, 1500); err != nil {
		panic(err)
	}
	check("D stale 标记后无值", len(sel(st, "m", 1600)) == 0, "")
	mustAdd(st, "m", map[string]string{"a": "1"}, 1700, 9)
	check("D stale 之后写入新样本即恢复",
		len(sel(st, "m", 1750)) == 1 && sel(st, "m", 1750)[0].V == 9, "")
}

func groupE() {
	st := NewMemStore(0)
	for _, t := range []float64{0, 60, 120, 180, 240, 300} {
		mustAdd(st, "c", map[string]string{"a": "1"}, t, t)
	}
	e := NewEngine(st)
	r, err := e.Instant("c[5m]", 300)
	if err != nil {
		panic(err)
	}
	pts := r.Matrix[0].Pts
	has0, has300 := false, false
	for _, p := range pts {
		if p.T == 0 {
			has0 = true
		}
		if p.T == 300 {
			has300 = true
		}
	}
	check("E 左边界样本被排除（t=0 不在 5m 窗口内）", !has0, "")
	check("E 右边界样本被包含（t=300 在窗口内）", has300, "")
	check("E 窗口共 5 个样本（60/120/180/240/300）", len(pts) == 5, fmt.Sprint(len(pts)))
	d1, _ := ParseDuration("5m")
	d2, _ := ParseDuration("1h30m")
	d3, _ := ParseDuration("250ms")
	check("E 时长解析 5m=300s / 1h30m=5400s / 250ms=0.25s",
		d1 == 300 && d2 == 5400 && approx(d3, 0.25, 1e-12), "")
}

func main() {
	for _, g := range []func(){groupA, groupB, groupC, groupD, groupE,
		groupF, groupG, groupH} {
		g()
	}
	fmt.Println("------------------------------------------------------------")
	if failed > 0 {
		fmt.Printf("断言失败 %d 项 / 通过 %d 项\n", failed, pass)
		for _, f := range failures {
			fmt.Println("  -", f)
		}
		os.Exit(1)
	}
	fmt.Printf("全部 %d 项断言通过\n", pass)
}
