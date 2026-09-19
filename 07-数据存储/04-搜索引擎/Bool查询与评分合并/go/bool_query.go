// Lucene / ES bool 查询(Go),与 python/bool_query.py 同构。
//
// 权威来源(实际读过):
//   1. .../query-dsl-bool-query.html —— must/should 计分且分数**相加**;
//      filter/must_not 走 filter context 不计分且可缓存;纯 filter ⇒ _score=0;
//      must:match_all + filter ⇒ 1.0,与 constant_score 等效;must 下的 should 只加分
//   2. .../query-dsl-minimum-should-match.html —— 默认值(有 should 且无 must/filter ⇒ 1,
//      否则 0);百分比向下取整;结果 clamp 到 [1,n];无 required 子句时仍须匹配 ≥1 optional
//   3. BooleanQuery.java —— bool query 直接映射到 Lucene BooleanQuery
package main

import (
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
)

const (
	MUST     = "must"
	SHOULD   = "should"
	FILTER   = "filter"
	MUSTNOT  = "must_not"
)

var okCount, failCount int

func check(cond bool, msg string) {
	if cond {
		okCount++
		fmt.Println("  [ok]   " + msg)
	} else {
		failCount++
		fmt.Println("  [FAIL] " + msg)
	}
}

// ---------------------------------------------------------------- msm
func applyOne(spec string, n int) int {
	if strings.HasSuffix(spec, "%") {
		v, _ := strconv.ParseFloat(strings.TrimSuffix(spec, "%"), 64)
		if v >= 0 {
			return int(math.Floor(float64(n) * v / 100.0))
		}
		return n - int(math.Floor(float64(n)*(-v)/100.0))
	}
	v, _ := strconv.Atoi(spec)
	if v >= 0 {
		return v
	}
	return n + v
}

// parseMSM 支持 整数 / 负整数 / 百分比 / 负百分比 / `3<90%` / `2<-25% 9<-3`
func parseMSM(spec string, nOpt int) int {
	if nOpt == 0 {
		return 0
	}
	parts := strings.Fields(spec)
	v := nOpt
	if len(parts) == 1 && !strings.Contains(parts[0], "<") {
		v = applyOne(parts[0], nOpt)
	} else {
		chosen := ""
		for _, p := range parts {
			if i := strings.Index(p, "<"); i >= 0 {
				thr, _ := strconv.Atoi(p[:i])
				if nOpt > thr {
					chosen = p[i+1:]
				}
			} else {
				chosen = p
			}
		}
		if chosen != "" {
			v = applyOne(chosen, nOpt)
		}
	}
	if v < 1 {
		return 1
	}
	if v > nOpt {
		return nOpt
	}
	return v
}

func isScoring(o string) bool  { return o == MUST || o == SHOULD }
func isRequired(o string) bool { return o == MUST || o == FILTER }

// ---------------------------------------------------------------- 模型
type Clause struct {
	Occur  string
	Name   string
	Hits   map[int]bool
	Scores map[int]float64
}

func (c *Clause) hit(d int) bool { return c.Hits[d] }

type BoolQuery struct {
	Clauses []Clause
	MSM     interface{} // nil | int | string
}

func (q *BoolQuery) count(occur string) int {
	c := 0
	for _, cl := range q.Clauses {
		if cl.Occur == occur {
			c++
		}
	}
	return c
}

// effectiveMSM 默认值规则 + 「无 required 子句时至少 1」的兜底
func (q *BoolQuery) effectiveMSM() int {
	nS, nM, nF := q.count(SHOULD), q.count(MUST), q.count(FILTER)
	base := 0
	switch v := q.MSM.(type) {
	case nil:
		if nS >= 1 && nM == 0 && nF == 0 {
			base = 1
		}
	case int:
		base = v
	case string:
		base = parseMSM(v, nS)
	}
	if nM == 0 && nF == 0 {
		if nS == 0 {
			return 0
		}
		if base < 1 {
			return 1
		}
	}
	return base
}

func (q *BoolQuery) Matches(d int) bool {
	for i := range q.Clauses {
		c := &q.Clauses[i]
		if isRequired(c.Occur) && !c.hit(d) {
			return false
		}
		if c.Occur == MUSTNOT && c.hit(d) {
			return false
		}
	}
	n := 0
	for i := range q.Clauses {
		if q.Clauses[i].Occur == SHOULD && q.Clauses[i].hit(d) {
			n++
		}
	}
	return n >= q.effectiveMSM()
}

func (q *BoolQuery) Score(d int) float64 {
	if !q.Matches(d) {
		return 0
	}
	s := 0.0
	for i := range q.Clauses {
		if isScoring(q.Clauses[i].Occur) {
			s += q.Clauses[i].Scores[d]
		}
	}
	return s
}

func main() {
	docs := []int{1, 2, 3, 4}
	fmt.Println("== Demo 1 · 四类 occurrence 的匹配语义 ==")
	q := &BoolQuery{Clauses: []Clause{
		{MUST, "title:es", map[int]bool{1: true, 2: true, 3: true},
			map[int]float64{1: 1.0, 2: 2.0, 3: 0.5}},
		{FILTER, "status:active", map[int]bool{1: true, 2: true}, nil},
		{MUSTNOT, "tag:spam", map[int]bool{3: true}, nil},
		{SHOULD, "tag:new", map[int]bool{1: true, 4: true}, map[int]float64{1: 0.3, 4: 0.3}},
	}}
	hits := []int{}
	for _, d := range docs {
		if q.Matches(d) {
			hits = append(hits, d)
		}
	}
	fmt.Println("   命中:", hits)
	check(fmt.Sprint(hits) == "[1 2]", "must∩filter 且排除 must_not ⇒ {1,2}")
	check(!q.Matches(3), "doc3 命中 must_not ⇒ 被排除")
	check(!q.Matches(4), "有 must/filter 时 should 不会把 doc4 带进来")

	fmt.Println("\n== Demo 2 · 评分:must/should 求和 ==")
	check(math.Abs(q.Score(1)-1.3) < 1e-9, "doc1 = must 1.0 + should 0.3 = 1.3")
	check(math.Abs(q.Score(2)-2.0) < 1e-9, "doc2 只命中 must(2.0)")

	fmt.Println("\n== Demo 3 · filter context 不计分 ==")
	f := Clause{FILTER, "status:active", map[int]bool{1: true, 2: true}, nil}
	onlyFilter := &BoolQuery{Clauses: []Clause{f}}
	mustAll := &BoolQuery{Clauses: []Clause{
		{MUST, "match_all", map[int]bool{1: true, 2: true}, map[int]float64{1: 1.0, 2: 1.0}}, f}}
	check(onlyFilter.Score(1) == 0 && onlyFilter.Score(2) == 0, "纯 filter ⇒ _score=0")
	check(mustAll.Score(1) == 1.0 && mustAll.Score(2) == 1.0, "match_all+filter ⇒ 1.0")

	fmt.Println("\n== Demo 4 · msm 默认值 ==")
	sOnly := &BoolQuery{Clauses: []Clause{{SHOULD, "x", map[int]bool{1: true}, nil}}}
	sMust := &BoolQuery{Clauses: []Clause{
		{SHOULD, "x", map[int]bool{1: true}, nil},
		{MUST, "m", map[int]bool{1: true, 2: true}, nil}}}
	check(sOnly.effectiveMSM() == 1, "只有 should ⇒ 默认 1")
	check(sMust.effectiveMSM() == 0, "有 must ⇒ 默认 0")
	zero := &BoolQuery{Clauses: []Clause{{SHOULD, "x", map[int]bool{1: true}, nil}}, MSM: "0%"}
	check(zero.effectiveMSM() == 1, "算出 0 也兜底成 1(无 required 子句)")

	fmt.Println("\n== Demo 5 · msm 规格 ==")
	check(parseMSM("3", 5) == 3 && parseMSM("-2", 5) == 3, "整数 / 负整数")
	check(parseMSM("75%", 4) == 3 && parseMSM("-25%", 4) == 3, "4 子句:75% == -25% == 3")
	check(parseMSM("75%", 5) == 3 && parseMSM("-25%", 5) == 4, "5 子句:75%⇒3,-25%⇒4")
	check(parseMSM("3<90%", 3) == 3 && parseMSM("3<90%", 4) == 3 && parseMSM("3<90%", 10) == 9,
		"组合 3<90%")
	got := []int{}
	for _, n := range []int{1, 2, 3, 9, 10, 12} {
		got = append(got, parseMSM("2<-25% 9<-3", n))
	}
	check(fmt.Sprint(got) == "[1 2 3 7 7 9]", "多重组合 2<-25% 9<-3")

	fmt.Printf("\n断言 %d 通过 / %d 失败\n", okCount, failCount)
	if failCount > 0 {
		os.Exit(1)
	}
}
