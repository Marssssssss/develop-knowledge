// Lucene / ES DocValues 列式存储与 global ordinals(Go),与 python/doc_values.py 同构。
//
// 权威来源(实际读过):
//   1. .../doc-values.html —— 倒排解决 term→docs,排序/聚合/脚本需要 doc→terms;
//      doc_values 是索引期构建的**磁盘列存**;支持的类型默认开启(text/match_only_text
//      支持但默认关,annotated_text 不支持);doc-value-only(index:false)能查但慢;
//      wildcard 与 columnar index 字段不能关;columnar 下 multi_value:false 多值默认拒绝
//   2. OrdinalMap.java —— 段内 ordinal ↔ 全局 ordinal 的 packed-ints 映射;
//      NOTE:代价高(要对全部 term 归并排序 + 占 RAM);成员 globalOrdDeltas /
//      firstSegments / 每段的 segmentOrd→globalOrd
package main

import (
	"fmt"
	"math"
	"os"
	"sort"
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

type Field struct {
	Name      string
	Indexed   bool
	DocValues bool
	Ftype     string
}

func canDisableDocValues(f Field) bool {
	return f.Ftype != "wildcard" && f.Ftype != "columnar"
}

func buildInverted(docs map[int]string) map[string][]int {
	inv := map[string][]int{}
	ids := []int{}
	for d := range docs {
		ids = append(ids, d)
	}
	sort.Ints(ids)
	for _, d := range ids {
		inv[docs[d]] = append(inv[docs[d]], d)
	}
	return inv
}

// filterByTerm 走倒排(只碰命中文档)还是走列存(扫整列)
func filterByTerm(inv map[string][]int, dv map[int]string, term string, indexed bool) ([]int, int) {
	if indexed {
		h := inv[term]
		return h, len(h)
	}
	ids := []int{}
	for d := range dv {
		ids = append(ids, d)
	}
	sort.Ints(ids)
	hits := []int{}
	for _, d := range ids {
		if dv[d] == term {
			hits = append(hits, d)
		}
	}
	return hits, len(dv)
}

// ---------------------------------------------------------------- ordinals
type OrdinalMap struct {
	SegTerms    [][]string
	GlobalTerms []string
	Seg2Global  [][]int
	FirstSeg    map[int]int
	Deltas      map[int]int
}

func NewOrdinalMap(segTerms [][]string) *OrdinalMap {
	m := &OrdinalMap{FirstSeg: map[int]int{}, Deltas: map[int]int{}}
	seen := map[string]bool{}
	for _, s := range segTerms {
		cp := append([]string{}, s...)
		sort.Strings(cp)
		m.SegTerms = append(m.SegTerms, cp)
		for _, t := range cp {
			seen[t] = true
		}
	}
	for t := range seen {
		m.GlobalTerms = append(m.GlobalTerms, t)
	}
	sort.Strings(m.GlobalTerms)
	gidx := map[string]int{}
	for i, t := range m.GlobalTerms {
		gidx[t] = i
	}
	for _, s := range m.SegTerms {
		row := []int{}
		for _, t := range s {
			row = append(row, gidx[t])
		}
		m.Seg2Global = append(m.Seg2Global, row)
	}
	for g, t := range m.GlobalTerms {
		for i, s := range m.SegTerms {
			found := false
			for _, x := range s {
				if x == t {
					found = true
					break
				}
			}
			if found {
				segOrd := 0
				for j, x := range s {
					if x == t {
						segOrd = j
						break
					}
				}
				m.FirstSeg[g] = i
				m.Deltas[g] = g - segOrd
				break
			}
		}
	}
	return m
}

func (m *OrdinalMap) segToGlobal(seg, segOrd int) int { return m.Seg2Global[seg][segOrd] }

func (m *OrdinalMap) globalToSeg(g int) int { return g - m.Deltas[g] }

// packedBits packed ints:ceil(log2(n)) 位一个条目
func (m *OrdinalMap) packedBits() int {
	n := len(m.GlobalTerms)
	if n <= 1 {
		return 1
	}
	b := int(math.Ceil(math.Log2(float64(n))))
	if b < 1 {
		return 1
	}
	return b
}

// aggregate 段内计数 → 映射累加 → 全局桶
func (m *OrdinalMap) aggregate(segDocs [][]string) map[int]int {
	buckets := map[int]int{}
	for seg, docs := range segDocs {
		local := map[int]int{}
		for _, t := range docs {
			o := 0
			for j, x := range m.SegTerms[seg] {
				if x == t {
					o = j
					break
				}
			}
			local[o]++
		}
		for o, c := range local {
			buckets[m.segToGlobal(seg, o)] += c
		}
	}
	return buckets
}

func main() {
	fmt.Println("== Demo 1 · 倒排 vs 列存 ==")
	dv := map[int]string{1: "beijing", 2: "shanghai", 3: "beijing", 4: "shenzhen", 5: "beijing"}
	inv := buildInverted(dv)
	fmt.Println("   倒排:", inv["beijing"], " 列存 doc4 =", dv[4])
	check(fmt.Sprint(inv["beijing"]) == "[1 3 5]", "倒排:查词 → 文档列表")
	check(dv[4] == "shenzhen", "列存:拿文档 → 词")

	fmt.Println("\n== Demo 2 · doc-value-only ==")
	h1, s1 := filterByTerm(inv, dv, "beijing", true)
	h2, s2 := filterByTerm(inv, dv, "beijing", false)
	fmt.Printf("   indexed: 命中%v 触碰%d ; doc-value-only: 命中%v 触碰%d\n", h1, s1, h2, s2)
	check(fmt.Sprint(h1) == fmt.Sprint(h2), "结果集一致")
	check(s2 == len(dv) && s1 < s2, "doc-value-only 要扫整列 ⇒ 慢得多")

	fmt.Println("\n== Demo 3 · 谁能关 doc_values ==")
	check(canDisableDocValues(Field{Ftype: "keyword"}), "keyword 可关")
	check(!canDisableDocValues(Field{Ftype: "wildcard"}), "wildcard 不能关")
	check(!canDisableDocValues(Field{Ftype: "columnar"}), "columnar index 字段不能关")

	fmt.Println("\n== Demo 4 · global ordinals ==")
	om := NewOrdinalMap([][]string{
		{"beijing", "shanghai"}, {"beijing", "shenzhen"}, {"shenzhen"}})
	fmt.Println("   全局字典:", om.GlobalTerms)
	fmt.Println("   段0→全局:", om.Seg2Global[0], " 段1→全局:", om.Seg2Global[1])
	check(fmt.Sprint(om.GlobalTerms) == "[beijing shanghai shenzhen]", "归并去重后排序")
	check(om.segToGlobal(1, 1) == 2, "段1 的 shenzhen(ord 1) → 全局 2")
	allback := true
	for g, t := range om.GlobalTerms {
		seg := om.SegTerms[om.FirstSeg[g]]
		segOrd := 0
		for j, x := range seg {
			if x == t {
				segOrd = j
				break
			}
		}
		if om.globalToSeg(g) != segOrd {
			allback = false
		}
	}
	check(allback, "由 delta + firstSegment 能还原段内 ord")
	bits := om.packedBits()
	fmt.Printf("   全局 %d 个 term ⇒ packed %d 位/条目(定长 32 位 ⇒ %.1fx)\n",
		len(om.GlobalTerms), bits, 32.0/float64(bits))
	check(bits < 32, "packed ints 远小于定长 32 位")

	fmt.Println("\n== Demo 5 · 聚合 ==")
	b := om.aggregate([][]string{
		{"beijing", "beijing", "shanghai"}, {"beijing", "shenzhen"}, {"shenzhen"}})
	named := map[string]int{}
	total := 0
	for g, c := range b {
		named[om.GlobalTerms[g]] = c
		total += c
	}
	fmt.Println("   全局桶:", named)
	check(named["beijing"] == 3 && named["shanghai"] == 1 && named["shenzhen"] == 2,
		"跨段同名 term 并到同一全局桶")
	check(total == 6, "桶总和 = 参与聚合的文档数")

	fmt.Printf("\n断言 %d 通过 / %d 失败\n", okCount, failCount)
	if failCount > 0 {
		os.Exit(1)
	}
}
