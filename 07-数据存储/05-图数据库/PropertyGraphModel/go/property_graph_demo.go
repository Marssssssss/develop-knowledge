// 属性图模型(Neo4j property graph)最小可读实现 —— Go 版。
//
// 权威来源：
//   - Neo4j Getting Started "Graph database concepts"
//     https://neo4j.com/docs/getting-started/current/graphdb-concepts/
//   - Neo4j Developer Guides "What is a Graph Database"
//     https://neo4j.com/developer/graph-database/
//
// 节点 = labels:set + props:map + first_rel_out / first_rel_in map[type]rid
// 关系 = id + type + src/dst + props + next_out/prev_out (on src) +
//       next_in/prev_in (on dst) —— Neo4j 风格的双向链表。
package main

import "fmt"

// Node 节点：标签、属性、出/入关系链表头(按 type 分桶)。
type Node struct {
	ID          int
	Labels      map[string]struct{}
	Props       map[string]any
	FirstRelOut map[string]int // type -> first outgoing rid
	FirstRelIn  map[string]int // type -> first incoming rid
}

// Relationship 关系 + 双向链表(出向/入向两套 prev/next)。
type Relationship struct {
	ID       int
	Type     string
	Src, Dst int
	Props    map[string]any
	NextOut  *int // outgoing chain on Src
	PrevOut  *int
	NextIn   *int // incoming chain on Dst
	PrevIn   *int
}

// PropertyGraph 图：nodes + rels + 自增 id。
type PropertyGraph struct {
	Nodes    map[int]*Node
	Rels     map[int]*Relationship
	NextNID  int
	NextRID  int
}

func New() *PropertyGraph {
	return &PropertyGraph{
		Nodes:    map[int]*Node{},
		Rels:     map[int]*Relationship{},
		NextNID:  1,
		NextRID:  1,
	}
}

func ptr(i int) *int { return &i }

// CreateNode 新建节点，labels 列表，props 键值对。
func (g *PropertyGraph) CreateNode(labels []string, props map[string]any) int {
	id := g.NextNID
	g.NextNID++
	lb := map[string]struct{}{}
	for _, l := range labels {
		lb[l] = struct{}{}
	}
	g.Nodes[id] = &Node{
		ID: id, Labels: lb, Props: props,
		FirstRelOut: map[string]int{}, FirstRelIn: map[string]int{},
	}
	return id
}

// CreateRel 创建关系 + 挂双向链表头。
func (g *PropertyGraph) CreateRel(src, dst int, typeName string, props map[string]any) int {
	rid := g.NextRID
	g.NextRID++
	s := g.Nodes[src]
	d := g.Nodes[dst]
	oldHeadOut := s.FirstRelOut[typeName]    // 0 = not found
	var oldHeadOutPtr *int
	if oldHeadOut != 0 {
		oldHeadOutPtr = ptr(oldHeadOut)
	}
	oldHeadIn := d.FirstRelIn[typeName]
	var oldHeadInPtr *int
	if oldHeadIn != 0 {
		oldHeadInPtr = ptr(oldHeadIn)
	}
	// 注意 prev 留空(新头)。
	g.Rels[rid] = &Relationship{
		ID: rid, Type: typeName, Src: src, Dst: dst, Props: props,
		NextOut: oldHeadOutPtr, PrevOut: nil,
		NextIn: oldHeadInPtr, PrevIn: nil,
	}
	if oldHeadOut != 0 {
		g.Rels[oldHeadOut].PrevOut = ptr(rid)
	}
	if oldHeadIn != 0 {
		g.Rels[oldHeadIn].PrevIn = ptr(rid)
	}
	s.FirstRelOut[typeName] = rid
	d.FirstRelIn[typeName] = rid
	return rid
}

// NeighborsOut 沿 Src 上 type 双向链表遍历出邻居。
func (g *PropertyGraph) NeighborsOut(nid int, typeName string) []int {
	n := g.Nodes[nid]
	results := []int{}
	cur := n.FirstRelOut[typeName]
	for cur != 0 {
		r := g.Rels[cur]
		results = append(results, r.Dst)
		if r.NextOut == nil {
			break
		}
		cur = *r.NextOut
	}
	return results
}

// NeighborsIn 沿 Dst 上 type 双向链表遍历入邻居。
func (g *PropertyGraph) NeighborsIn(nid int, typeName string) []int {
	n := g.Nodes[nid]
	results := []int{}
	cur := n.FirstRelIn[typeName]
	for cur != 0 {
		r := g.Rels[cur]
		results = append(results, r.Src)
		if r.NextIn == nil {
			break
		}
		cur = *r.NextIn
	}
	return results
}

// FindByLabel 标签扫描。
func (g *PropertyGraph) FindByLabel(label string) []int {
	out := []int{}
	for _, n := range g.Nodes {
		if _, ok := n.Labels[label]; ok {
			out = append(out, n.ID)
		}
	}
	return out
}

func (g *PropertyGraph) DemoMovieGraph() {
	tom := g.CreateNode([]string{"Person"}, map[string]any{"name": "Tom Hanks", "born": 1956})
	forrest := g.CreateNode([]string{"Movie"}, map[string]any{"title": "Forrest Gump", "released": 1994})
	g.CreateRel(tom, forrest, "ACTED_IN", map[string]any{"roles": []string{"Forrest"}})

	sally := g.CreateNode([]string{"Person"}, map[string]any{"name": "Sally Field", "born": 1946})
	g.CreateRel(sally, forrest, "ACTED_IN", map[string]any{"roles": []string{"Mrs. Gump"}})

	apollo := g.CreateNode([]string{"Movie"}, map[string]any{"title": "Apollo 13", "released": 1995})
	g.CreateRel(tom, apollo, "ACTED_IN", map[string]any{"roles": []string{"Jim Lovell"}})

	// 查询 1：Tom Hanks 演过的所有电影
	fmt.Printf("Tom Hanks 的电影: %v\n", titleOf(g, g.NeighborsOut(tom, "ACTED_IN")))
	// 查询 2：所有 Person 节点
	for _, id := range g.FindByLabel("Person") {
		fmt.Printf("Person nid=%d name=%v\n", id, g.Nodes[id].Props["name"])
	}
	// 查询 3：Forrest Gump 的演员
	fmt.Printf("Forrest Gump 演员: %v\n", nameOf(g, g.NeighborsIn(forrest, "ACTED_IN")))
}

func titleOf(g *PropertyGraph, ids []int) []string {
	out := []string{}
	for _, id := range ids {
		out = append(out, g.Nodes[id].Props["title"].(string))
	}
	return out
}

func nameOf(g *PropertyGraph, ids []int) []string {
	out := []string{}
	for _, id := range ids {
		out = append(out, g.Nodes[id].Props["name"].(string))
	}
	return out
}

func main() {
	g := New()
	g.DemoMovieGraph()
	fmt.Println("--- rel 双向链表自检 ---")
	for rid, r := range g.Rels {
		var nxO, pvO, nxI, pvI any = "<nil>", "<nil>", "<nil>", "<nil>"
		if r.NextOut != nil {
			nxO = *r.NextOut
		}
		if r.PrevOut != nil {
			pvO = *r.PrevOut
		}
		if r.NextIn != nil {
			nxI = *r.NextIn
		}
		if r.PrevIn != nil {
			pvI = *r.PrevIn
		}
		fmt.Printf("rid=%d type=%s %d->%d next_out=%v prev_out=%v next_in=%v prev_in=%v\n",
			rid, r.Type, r.Src, r.Dst, nxO, pvO, nxI, pvI)
	}
}