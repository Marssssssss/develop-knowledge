// Cypher 执行计划: 火山模型(Volcano/迭代器)算子树最小实现 (Go 版).
// 依据 Neo4j Cypher Manual "Execution plans". 算子库见 pipeline_ops.go;
// 本文件: 图数据 + 查询计划组装 + demo.
package main

import (
	"fmt"
	"sort"
)

// Graph 官方 Movies 简化图.
type Graph struct {
	Nodes map[string]Node
	Out   map[string][]Edge // 无索引邻接: 只看该节点邻接表
}

type Node struct {
	Labels []string
	Name   string // 演示用单一属性
	Title  string
}

type Edge struct {
	Rid, Dst string
}

func BuildGraph() *Graph {
	return &Graph{
		Nodes: map[string]Node{
			"charlie": {Labels: []string{"Person"}, Name: "Charlie Sheen"},
			"martin":  {Labels: []string{"Person"}, Name: "Martin Sheen"},
			"michael": {Labels: []string{"Person"}, Name: "Michael Douglas"},
			"oliver":  {Labels: []string{"Director"}, Name: "Oliver Stone"},
			"wallst":  {Labels: []string{"Movie"}, Title: "Wall Street"},
		},
		Out: map[string][]Edge{
			"charlie": {{"r1", "wallst"}},
			"martin":  {{"r2", "wallst"}},
			"michael": {{"r3", "wallst"}},
			"oliver":  {{"r4", "wallst"}},
		},
	}
}

func pad(n int) string { s := ""; for i := 0; i < n; i++ { s += "  " }; return s }

func hasLabel(g *Graph, nid, label string) bool {
	for _, l := range g.Nodes[nid].Labels {
		if l == label {
			return true
		}
	}
	return false
}

func sortedIDs(g *Graph) []string { // 稳定输出
	ids := make([]string, 0, len(g.Nodes))
	for id := range g.Nodes {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}


func main() {
	g := BuildGraph()

	fmt.Println("== 1. 惰性流水线: Projection <- Expand <- NodeByLabelScan ==")
	plan1 := &Projection{
		Child: &Expand{
			Child:   &NodeByLabelScan{Var: "p", Label: "Person"},
			FromVar: "p", RelVar: "r", RelType: "ACTED_IN", ToVar: "m",
		},
		Expr: func(r Row) Row {
			return Row{"name": g.Nodes[r["p"]].Name, "title": g.Nodes[r["m"]].Title}
		},
	}
	rows := execAll(openIt(plan1, g))
	for _, r := range rows {
		fmt.Printf("  %-16s -ACTED_IN-> %s\n", r["name"], r["title"])
	}
	fmt.Println("  计划树(官方: 自底向上阅读):")
	fmt.Println(plan1.Describe(1))

	fmt.Println("\n== 2. Apply 风格: LHS 每行驱动 RHS 重启(连续 MATCH) ==")
	plan2 := &Apply{
		Left:       &NodeByLabelScan{Var: "m", Label: "Movie"},
		RightLabel: "Expand+Filter",
		RightFactory: func(b Row) Operator {
			m := b["m"]
			return &Filter{
				Child: &Expand{
					Child:   &NodeByLabelScan{Var: "p", Label: "Person"},
					FromVar: "p", RelVar: "r", RelType: "ACTED_IN", ToVar: "m2",
				},
				Pred: func(row Row) bool { return row["m2"] == m },
				Text: "m2 == " + m,
			}
		},
	}
	rows2 := execAll(openIt(plan2, g))
	for _, r := range rows2 {
		fmt.Printf("  %s: %s\n", g.Nodes[r["m"]].Title, g.Nodes[r["p"]].Name)
	}
	fmt.Println("  -- 1 部电影 × RHS 每电影重启一次 = 3 行(嵌套循环语义)")

	fmt.Println("\n== 3. Join 风格: CartesianProduct(两侧完整输入) ==")
	plan3 := &CartesianProduct{
		Left:  &NodeByLabelScan{Var: "m", Label: "Movie"},
		Right: &NodeByLabelScan{Var: "d", Label: "Director"},
	}
	rows3 := execAll(openIt(plan3, g))
	for _, r := range rows3 {
		fmt.Printf("  %s x %s\n", g.Nodes[r["m"]].Title, g.Nodes[r["d"]].Name)
	}
	fmt.Printf("  -- 1 Movie x 1 Director = %d 行(笛卡尔积语义)\n", len(rows3))

	fmt.Println("\n== 4. Eager 算子: Sort 必须收完全部输入才能产出 ==")
	plan4 := &Sort{
		Child: &Projection{
			Child: &NodeByLabelScan{Var: "p", Label: "Person"},
			Expr:  func(r Row) Row { return Row{"name": g.Nodes[r["p"]].Name} },
		},
		Key: func(r Row) string { return r["name"] },
	}
	rows4 := execAll(openIt(plan4, g))
	names := []string{}
	for _, r := range rows4 {
		names = append(names, r["name"])
	}
	fmt.Printf("  %v\n", names)
	fmt.Println("  -- Sort/聚合类: 上游行全部物化进缓冲, 内存代价 O(行数)")

	fmt.Println("\n== 5. Estimated Rows vs 实际 Rows(官方: 估算可能错) ==")
	fmt.Println("  Estimated Rows: 标签统计+选择性模型, 用于计划选择, 无运行时反馈")
	fmt.Println("  Rows/DB Hits:   仅 PROFILE 实测; DB Hits = 存储层访问抽象单位")
	fmt.Println("  -- 估算与实测偏差大 -> 统计过期, 需更新统计或加索引")
}

// openIt 便捷: Open 后返回自身供 execAll.
func openIt(op Operator, g *Graph) Operator {
	op.Open(g)
	return op
}
