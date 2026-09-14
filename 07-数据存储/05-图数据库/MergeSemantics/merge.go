// Cypher MERGE 语义最小实现 (Go 版): find-or-create / ON CREATE / ON MATCH / 并发锁.
// 语义同 Python 版, 依据 Neo4j Cypher Manual "MERGE".
package main

import (
	"fmt"
	"sort"
	"sync"
)

type Node struct {
	Label string
	Props map[string]any
}

type Rel struct {
	Type     string
	Src, Dst int // 节点下标
}

type Graph struct {
	mu     sync.Mutex
	Nodes  []Node
	Rels   []Rel
	Events []string
}

func (g *Graph) FindNode(label string, props map[string]any) int {
	for i, n := range g.Nodes {
		if n.Label != label || len(n.Props) != len(props) {
			continue
		}
		match := true // 全属性精确匹配
		for k, v := range props {
			if n.Props[k] != v {
				match = false
				break
			}
		}
		if match {
			return i
		}
	}
	return -1
}

// FindRel 无向时双向匹配(无向 MERGE: 先双向匹配).
func (g *Graph) FindRel(src, dst int, rtype string, undirected bool) int {
	for i, r := range g.Rels {
		if r.Type != rtype {
			continue
		}
		if (r.Src == src && r.Dst == dst) ||
			(undirected && r.Src == dst && r.Dst == src) {
			return i
		}
	}
	return -1
}

func (g *Graph) CreateNode(label string, props map[string]any) int {
	g.Nodes = append(g.Nodes, Node{label, props})
	return len(g.Nodes) - 1
}

func (g *Graph) CreateRel(src, dst int, rtype string) int {
	g.Rels = append(g.Rels, Rel{rtype, src, dst}) // 无向语义下左 -> 右创建
	return len(g.Rels) - 1
}

// MergeNode MERGE (n:Label {props}) + ON CREATE / ON MATCH.
func MergeNode(g *Graph, label string, props map[string]any,
	onCreate, onMatch func(*Node)) (int, bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	idx := g.FindNode(label, props)
	created := idx < 0
	if created {
		idx = g.CreateNode(label, map[string]any{})
		for k, v := range props {
			g.Nodes[idx].Props[k] = v
		}
	}
	if created && onCreate != nil {
		onCreate(&g.Nodes[idx])
	}
	if !created && onMatch != nil {
		onMatch(&g.Nodes[idx])
	}
	return idx, created
}

// MergeNodeFromRows 官方 Location 例子: 查询内已绑定的节点被后续行复用.
func MergeNodeFromRows(g *Graph, label string, values []string) map[string]int {
	bound := map[string]int{}
	for _, key := range values {
		if _, ok := bound[key]; ok {
			continue // 本查询内已绑定 -> 复用, 不再 MERGE
		}
		idx, _ := MergeNode(g, label, map[string]any{"name": key}, nil, nil)
		bound[key] = idx
	}
	return bound
}

// MergeRelConcurrent 并发 MERGE 关系(官方 Concurrent relationship merges):
// 首次 MATCH 失败 -> 对两端节点取排他锁 -> 锁后二次 MATCH -> 仍无才创建.
func MergeRelConcurrent(g *Graph, a, b int, rtype string) (int, string) {
	if idx := g.FindRel(a, b, rtype, true); idx >= 0 {
		return idx, "matched" // 快路径: 无需加锁
	}
	g.mu.Lock() // 排他锁(两端节点)
	defer g.mu.Unlock()
	if idx := g.FindRel(a, b, rtype, true); idx >= 0 {
		g.Events = append(g.Events, "second-match-hit") // 竞态被锁吸收
		return idx, "matched-after-lock"
	}
	idx := g.CreateRel(a, b, rtype)
	g.Events = append(g.Events, "created-under-lock")
	return idx, "created"
}

// NaiveMergeRelConcurrent 反面教材: 无锁(无二次 MATCH)的并发 MERGE.
func NaiveMergeRelConcurrent(g *Graph, a, b int, rtype string) string {
	if g.FindRel(a, b, rtype, true) < 0 {
		g.CreateRel(a, b, rtype)
		return "created"
	}
	return "matched"
}

func main() {
	fmt.Println("== 1. 基本语义: 找到则绑定, 找不到才创建 ==")
	g := &Graph{}
	i1, c1 := MergeNode(g, "Person", map[string]any{"name": "Michael Douglas"}, nil, nil)
	fmt.Printf("  首次 MERGE: created=%v\n", c1)
	i2, c2 := MergeNode(g, "Person", map[string]any{"name": "Michael Douglas"}, nil, nil)
	fmt.Printf("  再次 MERGE: created=%v (同一节点: %v)\n", c2, i1 == i2)

	fmt.Println("\n== 2. 属性集不完全一致 -> 视为不存在 -> 创建'相似'节点 ==")
	_, c3 := MergeNode(g, "Person",
		map[string]any{"name": "Michael Douglas", "bornIn": "New Jersey"}, nil, nil)
	fmt.Printf("  MERGE(:Person {name, bornIn}): created=%v\n", c3)
	fmt.Println("  (官方: 需唯一约束才能阻止, 有约束时此类不一致直接报错而非创建)")

	fmt.Println("\n== 3. 查询内复用: Location 例子(3 个纽约人只建 1 个 Location) ==")
	g2 := &Graph{}
	bound := MergeNodeFromRows(g2, "Location",
		[]string{"New York", "Ohio", "New York"})
	keys := []string{}
	for k := range bound {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	fmt.Printf("  3 行输入(含 2 个 New York) -> Location 节点数 = %d = %v\n",
		len(g2.Nodes), keys)

	fmt.Println("\n== 4. ON CREATE / ON MATCH ==")
	g3 := &Graph{}
	_, c4 := MergeNode(g3, "Person", map[string]any{"name": "Keanu Reeves"},
		func(n *Node) { n.Props["created_at"] = 1 }, nil)
	fmt.Printf("  首次: created=%v, props=%v (ON CREATE SET 生效)\n", c4, g3.Nodes[0].Props)
	_, c5 := MergeNode(g3, "Person", map[string]any{"name": "Keanu Reeves"},
		nil, func(n *Node) { n.Props["last_seen"] = 2 })
	fmt.Printf("  再次: created=%v, props=%v (ON MATCH SET 生效)\n", c5, g3.Nodes[0].Props)

	fmt.Println("\n== 5. 并发 MERGE 关系: 排他锁 + 锁后二次 MATCH ==")
	// 5a. 无锁版: 两个并发事务都判"不存在" -> 双重创建
	g4 := &Graph{}
	ia := g4.CreateNode("Person", map[string]any{"name": "A"})
	ib := g4.CreateNode("Person", map[string]any{"name": "B"})
	run2(g4, ia, ib, false)
	dup := 0
	for _, r := range g4.Rels {
		if r.Type == "KNOWS" {
			dup++
		}
	}
	fmt.Printf("  无锁并发: KNOWS 关系数 = %d (重复! MERGE 只保证存在不保证唯一)\n", dup)

	// 5b. 官方锁版: 排他锁 + 二次 MATCH -> 恰好 1 条
	g5 := &Graph{}
	ja := g5.CreateNode("Person", map[string]any{"name": "A"})
	jb := g5.CreateNode("Person", map[string]any{"name": "B"})
	run2(g5, ja, jb, true)
	ok := 0
	for _, r := range g5.Rels {
		if r.Type == "KNOWS" {
			ok++
		}
	}
	fmt.Printf("  排他锁+二次MATCH: KNOWS 关系数 = %d (恰好 1 条), 事件: %v\n",
		ok, g5.Events)
}

// run2 两个"事务"并发 MERGE 同一关系.
func run2(g *Graph, a, b int, withLock bool) {
	var wg sync.WaitGroup
	barrier := make(chan struct{})
	ready := sync.WaitGroup{}
	for i := 0; i < 2; i++ {
		wg.Add(1)
		ready.Add(1)
		go func() {
			defer wg.Done()
			ready.Done()
			<-barrier // 两事务同时进入 MERGE
			if withLock {
				MergeRelConcurrent(g, a, b, "KNOWS")
			} else {
				NaiveMergeRelConcurrent(g, a, b, "KNOWS")
			}
		}()
	}
	ready.Wait()
	close(barrier)
	wg.Wait()
}
