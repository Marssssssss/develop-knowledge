// Package main 实现 Cypher 路径匹配模式（match mode / path mode）的最小模型。
//
// 依据 Neo4j Cypher Manual（Cypher 25 / current）：
//   - Paths with unique relationships：Cypher 默认不允许同一条关系在同一条 MATCH
//     结果里被重复遍历（无论方向），节点则可以；默认行为可用
//     MATCH DIFFERENT RELATIONSHIPS 显式写出且不改变语义。
//   - Acyclic paths：ACYCLIC 关键字进一步禁止节点在同一条路径内重复；Cypher 也
//     接受 GQL 路径模式 TRAIL 与 WALK，二者分别与 DIFFERENT RELATIONSHIPS /
//     REPEATABLE ELEMENTS 已施加的约束相同。
//
// 三种等级：WALK（无约束） ⊃ TRAIL（关系唯一） ⊃ ACYCLIC（关系+节点唯一）。
package main

const (
	Walk    = "WALK"
	Trail   = "TRAIL"
	Acyclic = "ACYCLIC"
)

// Rel 是一条有向关系；无向遍历时两端都可以进入它。
type Rel struct {
	ID    string
	Type  string
	Start string
	End   string
}

// Graph 同时保存有向邻接与无向邻接两张表。
type Graph struct {
	Nodes map[string]bool
	Rels  []*Rel
	out   map[string][]*edge
	und   map[string][]*edge
}

type edge struct {
	rel  *Rel
	next string
}

// NewGraph 返回空图。
func NewGraph() *Graph {
	return &Graph{Nodes: map[string]bool{}, out: map[string][]*edge{},
		und: map[string][]*edge{}}
}

// AddNode 增加一个节点。
func (g *Graph) AddNode(name string) {
	g.Nodes[name] = true
	if _, ok := g.out[name]; !ok {
		g.out[name] = nil
		g.und[name] = nil
	}
}

// AddRel 增加一条有向关系，并同时登记到无向邻接表。
func (g *Graph) AddRel(id, rtype, a, b string) *Rel {
	r := &Rel{ID: id, Type: rtype, Start: a, End: b}
	g.Rels = append(g.Rels, r)
	g.out[a] = append(g.out[a], &edge{r, b})
	g.und[a] = append(g.und[a], &edge{r, b})
	g.und[b] = append(g.und[b], &edge{r, a})
	return r
}

// Edges 返回从 name 出发可走的边；directed=false 时两个方向都可走。
func (g *Graph) Edges(name, rtype string, directed bool) []*edge {
	var table map[string][]*edge
	if directed {
		table = g.out
	} else {
		table = g.und
	}
	res := make([]*edge, 0, 4)
	for _, e := range table[name] {
		if rtype == "" || e.rel.Type == rtype {
			res = append(res, e)
		}
	}
	return res
}

// Allows 判定在该模式下能否把 (rel, node) 追加进当前路径。
func Allows(mode string, relSeen map[string]bool, nodeSeen map[string]bool,
	rel *Rel, node string) bool {
	switch mode {
	case Walk:
		return true
	case Acyclic:
		// 节点唯一 ⇒ 关系必然唯一（重复关系必然带回重复节点）
		return !relSeen[rel.ID] && !nodeSeen[node]
	case Trail:
		return !relSeen[rel.ID]
	}
	panic("unknown mode: " + mode)
}

// Path 是枚举出来的一条路径：节点名序列 + 关系 ID 序列。
type Path struct {
	Nodes []string
	Rels  []string
}

type query struct {
	start    string
	mode     string
	hops     int  // >0 表示定长 {n}
	end      string
	rtype    string
	directed bool
	maxHops  int
}

// Match 枚举满足模式的路径。
//
// hops>0：定长量化 {n}，恰好 n 跳后停止。end 非空时要求终点匹配。
// hops==0 且 end 非空：变长量化 +，到达 end 即产出并停止扩展。
// 口径：在 ACYCLIC 下「到达终点即停」与官方语义严格等价（节点唯一使中途经过
// 终点后不可能再回到终点）；在 TRAIL 下它等价于官方示例里写成内联谓词的
// (l WHERE l.name <> 'Z')。
func (g *Graph) Match(q query) []Path {
	if q.maxHops == 0 {
		q.maxHops = 64
	}
	res := []Path{}
	relSeen := map[string]bool{}
	nodeSeen := map[string]bool{q.start: true}
	var dfs func(cur string, nodes, rels []string)
	dfs = func(cur string, nodes, rels []string) {
		if q.hops > 0 {
			if len(rels) == q.hops {
				if q.end == "" || cur == q.end {
					res = append(res, Path{append([]string{}, nodes...),
						append([]string{}, rels...)})
				}
				return
			}
		} else if q.end != "" && len(rels) >= 1 && cur == q.end {
			res = append(res, Path{append([]string{}, nodes...),
				append([]string{}, rels...)})
			return
		}
		if len(rels) >= q.maxHops {
			return
		}
		for _, e := range g.Edges(cur, q.rtype, q.directed) {
			if !Allows(q.mode, relSeen, nodeSeen, e.rel, e.next) {
				continue
			}
			relSeen[e.rel.ID] = true
			nodeSeen[e.next] = true
			dfs(e.next, append(nodes, e.next), append(rels, e.rel.ID))
			delete(nodeSeen, e.next)
			delete(relSeen, e.rel.ID)
		}
	}
	dfs(q.start, []string{q.start}, nil)
	return res
}

// Count 是 Match 的计数便捷封装。
func (g *Graph) Count(q query) int { return len(g.Match(q)) }

// Konigsberg 复刻官方「七桥」示例图。
func Konigsberg() *Graph {
	g := NewGraph()
	for _, n := range []string{"Kneiphof", "North Bank", "South Bank", "Lomse"} {
		g.AddNode(n)
	}
	g.AddRel("1", "BRIDGE", "Kneiphof", "North Bank")
	g.AddRel("6", "BRIDGE", "Kneiphof", "South Bank")
	g.AddRel("7", "BRIDGE", "Kneiphof", "Lomse")
	g.AddRel("5", "BRIDGE", "North Bank", "Kneiphof")
	g.AddRel("2", "BRIDGE", "North Bank", "Lomse")
	g.AddRel("4", "BRIDGE", "South Bank", "Kneiphof")
	g.AddRel("3", "BRIDGE", "South Bank", "Lomse")
	return g
}

// RouterNetwork 复刻官方路由器网络（12 节点 / 19 条 LINK）。
func RouterNetwork() *Graph {
	g := NewGraph()
	for _, n := range []string{"A", "B", "C", "D", "E", "F", "G", "H",
		"I", "J", "K", "Z"} {
		g.AddNode(n)
	}
	pairs := [][2]string{{"A", "B"}, {"A", "C"}, {"A", "D"}, {"C", "B"},
		{"C", "D"}, {"B", "E"}, {"C", "E"}, {"D", "F"}, {"E", "G"},
		{"F", "G"}, {"H", "I"}, {"H", "J"}, {"I", "J"}, {"I", "Z"},
		{"G", "J"}, {"G", "K"}, {"J", "K"}, {"J", "Z"}, {"K", "Z"}}
	for _, p := range pairs {
		g.AddRel(p[0]+"->"+p[1], "LINK", p[0], p[1])
	}
	return g
}
