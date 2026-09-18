// EXPLAIN / EXPLAIN ANALYZE 解读的 Go 版对照实现（与 python/explain_model.py 同题异构）。
//
// 口径来源：PostgreSQL 18 官方手册 14.1 "Using EXPLAIN"
// （https://www.postgresql.org/docs/current/using-explain.html，实读）。本文件复现：
//   - 代价算式：(disk pages read * seq_page_cost) + (rows scanned * cpu_tuple_cost)，
//     默认 seq_page_cost=1.0 / cpu_tuple_cost=0.01，文档算例 345 页 × 1.0 + 10000 × 0.01 = 445；
//   - cost=startup..total 是「arbitrary units」，actual time 是毫秒，二者不可比；
//   - "Multiply by the `loops` value to get the total time actually spent in the node."；
//   - "which was then rejected by a recheck" → Rows Removed by Index Recheck；
//   - 只有一页的表 "you'll nearly always get a sequential scan plan"；
//   - Disabled: true 表示该节点类型被 enable/disable 开关压制过。
//
// 运行：go run explain_model.go（本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"
)

const (
	seqPageCost  = 1.0  // 文档明示默认值
	cpuTupleCost = 0.01 // 文档明示默认值
)

// node 表示计划树上的一个节点（demo 只保留解读所需的字段）。
type node struct {
	label    string
	indent   int
	startup  float64
	total    float64
	estRows  int
	width    int
	aStart   float64
	aEnd     float64
	aRows    int
	loops    int
	extras   []string
	children []*node
}

var (
	nodeRE   = regexp.MustCompile(`^(\s*)(?:->\s*)?([A-Z][^():]*?)\s*\((.*)\)\s*$`)
	costRE   = regexp.MustCompile(`cost=([\d.]+)\.\.([\d.]+)\s+rows=(\d+)\s+width=(\d+)`)
	actualRE = regexp.MustCompile(`actual time=([\d.]+)\.\.([\d.]+)\s+rows=(\d+)\s+loops=(\d+)`)
)

// actualSeen 判断该节点是否被 EXPLAIN ANALYZE 执行过。
func (n *node) actualSeen() bool { return n.loops > 0 }

// totalMs 对应文档 "Multiply by the loops value to get the total time"。
func (n *node) totalMs() float64 {
	if n.loops == 0 {
		return 0
	}
	return n.aEnd * float64(n.loops)
}

// rowsEmitted 是含重复发射在内的实际输出行总数（merge join 重复扫描口径）。
func (n *node) rowsEmitted() int { return n.aRows * n.loops }

func parsePlan(plan string) *node {
	var root *node
	stack := []*node{}
	for _, raw := range strings.Split(plan, "\n") {
		line := strings.TrimRight(raw, " \t\r")
		trimmed := strings.TrimSpace(line)
		if trimmed == "" {
			continue
		}
		m := nodeRE.FindStringSubmatch(line)
		valid := m != nil && (strings.Contains(m[3], "cost=") || strings.Contains(m[3], "actual time="))
		if !valid {
			if len(stack) > 0 {
				stack[len(stack)-1].extras = append(stack[len(stack)-1].extras, trimmed)
			}
			continue
		}
		n := &node{label: strings.TrimSpace(m[2]), indent: len(m[1])}
		if c := costRE.FindStringSubmatch(m[3]); c != nil {
			n.startup, _ = strconv.ParseFloat(c[1], 64)
			n.total, _ = strconv.ParseFloat(c[2], 64)
			n.estRows, _ = strconv.Atoi(c[3])
			n.width, _ = strconv.Atoi(c[4])
		}
		if a := actualRE.FindStringSubmatch(m[3]); a != nil {
			n.aStart, _ = strconv.ParseFloat(a[1], 64)
			n.aEnd, _ = strconv.ParseFloat(a[2], 64)
			n.aRows, _ = strconv.Atoi(a[3])
			n.loops, _ = strconv.Atoi(a[4])
		}
		for len(stack) > 0 && stack[len(stack)-1].indent >= n.indent {
			stack = stack[:len(stack)-1]
		}
		if len(stack) > 0 {
			stack[len(stack)-1].children = append(stack[len(stack)-1].children, n)
		} else {
			root = n
		}
		stack = append(stack, n)
	}
	return root
}

// walk 按先根顺序展开计划树；underLimit 表示该节点位于 Limit 之下（被截断）。
func walk(n *node, underLimit bool) []*node {
	out := []*node{n}
	trunc := underLimit || strings.HasPrefix(n.label, "Limit")
	for _, c := range n.children {
		out = append(out, walk(c, trunc)...)
	}
	return out
}

// seqScanCost 复现文档算式，用于核对计划里的 cost 估算。
func seqScanCost(pages, rows int) float64 {
	return float64(pages)*seqPageCost + float64(rows)*cpuTupleCost
}

// misestimates 返回 (节点, 实际/估算 比值) 超过 10× 的清单；被 Limit 截断的分支单列。
func misestimates(root *node) (bad, truncated []string) {
	var visit func(n *node, trunc bool)
	visit = func(n *node, trunc bool) {
		trunc = trunc || strings.HasPrefix(n.label, "Limit")
		if n.actualSeen() && n.estRows > 0 {
			ratio := float64(n.rowsEmitted()) / float64(n.estRows)
			if ratio > 10 || ratio < 0.1 {
				item := fmt.Sprintf("%s ratio=%.1f", n.label, ratio)
				if trunc {
					truncated = append(truncated, item)
				} else {
					bad = append(bad, item)
				}
			}
		}
		for _, c := range n.children {
			visit(c, trunc)
		}
	}
	visit(root, false)
	return bad, truncated
}

// plannerPicks 用文档给出的两条规则做粗判：单页表几乎必然 Seq Scan。
func plannerPicks(pages int) string {
	if pages <= 1 {
		return "Seq Scan" // "on a table that only occupies one disk page, you'll nearly always get a sequential scan plan"
	}
	return "Index Scan or Bitmap Index Scan（取决于选择性与随机读代价）"
}

var checks = struct{ pass, fail int }{}

func check(label string, cond bool, detail string) {
	if cond {
		checks.pass++
		fmt.Printf("  [PASS] %s\n", label)
		return
	}
	checks.fail++
	fmt.Printf("  [FAIL] %s :: %s\n", label, detail)
}

const planLoops = `
 Nested Loop  (cost=0.29..1424.26 rows=10000 width=8) (actual time=0.030..7.500 rows=10000 loops=1)
   ->  Seq Scan on tenk1  (cost=0.00..445.00 rows=10000 width=4) (actual time=0.015..2.100 rows=10000 loops=1)
   ->  Index Scan using tenk1_unique1 on tenk2  (cost=0.29..0.31 rows=1 width=4) (actual time=0.003..0.003 rows=1 loops=10000)
`

const planLimit = `
 Limit  (cost=0.29..0.33 rows=2 width=244) (actual time=0.030..0.031 rows=2 loops=1)
   ->  Index Scan using tenk1_unique1 on tenk1  (cost=0.29..1669.29 rows=10000 width=244) (actual time=0.030..0.031 rows=2 loops=1)
`

const planRecheck = `
 Bitmap Heap Scan on polygons  (cost=4.00..12.00 rows=1 width=32) (actual time=0.050..0.060 rows=1 loops=1)
   Recheck Cond: (p ~> polygon)
   Rows Removed by Index Recheck: 12
   ->  Bitmap Index Scan on poly_idx  (cost=0.00..4.00 rows=13 width=0) (actual time=0.030..0.030 rows=13 loops=1)
`

func main() {
	fmt.Println(strings.Repeat("=", 74))
	fmt.Println("1) 代价算式：文档算例 345 页 / 10000 行 → 445")
	check("seqScanCost(345, 10000) == 445", seqScanCost(345, 10000) == 445.0, fmt.Sprint(seqScanCost(345, 10000)))
	check("代价是「页读取」相对量（seq_page_cost=1 为基准）", seqScanCost(1, 0) == 1.0, fmt.Sprint(seqScanCost(1, 0)))

	fmt.Println("\n2) 解析计划树与 loops 口径")
	root := parsePlan(planLoops)
	if root == nil {
		fmt.Println("  [FAIL] 计划解析为空")
		os.Exit(1)
	}
	nodes := walk(root, false)
	check("解析出 3 个节点（Nested Loop + 两个子节点）", len(nodes) == 3, fmt.Sprint(len(nodes)))
	inner := nodes[2]
	check("内层 Index Scan 的 loops=10000", inner.loops == 10000, fmt.Sprint(inner.loops))
	check("单次 0.003ms × 10000 = 30ms 才是节点总耗时", inner.totalMs() == 30.0, fmt.Sprint(inner.totalMs()))
	check("顶层 loops=1，actual time 上界即整条语句的耗时", root.loops == 1 && root.totalMs() == 7.5, fmt.Sprint(root.totalMs()))
	check("外层 Seq Scan 的复算代价与计划里一致",
		root.children[0].total == seqScanCost(345, 10000), fmt.Sprint(root.children[0].total))

	fmt.Println("\n3) LIMIT 造成的差异不算估算错误")
	root = parsePlan(planLimit)
	bad, trunc := misestimates(root)
	check("Limit 下子节点归入 truncated 而非 misestimate", len(bad) == 0 && len(trunc) == 1,
		fmt.Sprintf("bad=%v trunc=%v", bad, trunc))
	check("被截断节点的估算仍是「跑完」口径（rows=10000 vs actual 2）",
		root.children[0].estRows == 10000 && root.children[0].aRows == 2,
		fmt.Sprintf("%d/%d", root.children[0].estRows, root.children[0].aRows))

	fmt.Println("\n4) 估算偏差的识别（阈值 10×）")
	root = parsePlan(`
 Hash Join  (cost=1.00..900.00 rows=10 width=8) (actual time=0.100..90.000 rows=50000 loops=1)
   Hash Cond: (a.id = b.id)
`)
	bad, _ = misestimates(root)
	check("估算 10 / 实际 50000 → 落入偏差清单", len(bad) == 1, fmt.Sprint(bad))
	check("Hash Cond 行被当作附加信息而非节点", len(root.extras) == 1 && root.extras[0] == "Hash Cond: (a.id = b.id)",
		fmt.Sprint(root.extras))

	fmt.Println("\n5) Rows Removed by Index Recheck（lossy 索引）")
	root = parsePlan(planRecheck)
	nodes = walk(root, false)
	check("Recheck 行数与 Recheck Cond 都进了 extras",
		strings.Contains(strings.Join(nodes[0].extras, "|"), "Rows Removed by Index Recheck: 12"),
		fmt.Sprint(nodes[0].extras))
	check("位图索引扫描只返回候选行（13 → 实测 1 行留用）",
		nodes[1].estRows == 13 && nodes[0].aRows == 1, fmt.Sprintf("%d/%d", nodes[1].estRows, nodes[0].aRows))

	fmt.Println("\n6) 单页表几乎必然 Seq Scan（文档结论）")
	check("1 页表 → Seq Scan", plannerPicks(1) == "Seq Scan", plannerPicks(1))
	check("345 页表 → 允许索引/位图路径", strings.HasPrefix(plannerPicks(345), "Index Scan"), plannerPicks(345))

	fmt.Println("\n" + strings.Repeat("=", 74))
	fmt.Printf("断言结果：pass=%d fail=%d\n", checks.pass, checks.fail)
	if checks.fail > 0 {
		os.Exit(1)
	}
}
