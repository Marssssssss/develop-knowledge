// Terraform 资源依赖图与拓扑排序 (Go 版)
//
// 来源:
// - Terraform Internals: Dependency Graph
//   (docs.hashicorp.com/terraform/internals/v1.15.x/graph)
// - "Terraform builds a dependency graph and uses it to perform operations,
//   such as generate plans and refresh state"
// - "graph walking is done in parallel: a node is walked as soon as
//   all of its dependencies are walked. By default, up to 10 nodes
//   will be processed concurrently. This number can be set using
//   the -parallelism flag on the plan, apply, and destroy commands"

package main

import (
	"container/list"
	"fmt"
	"sort"
	"strings"
)

// -----------------------------------------------------------------------------
// Graph — 邻接表 + 拓扑分层
// -----------------------------------------------------------------------------

type Graph struct {
	nodes map[string]struct{}
	out   map[string][]string // out[A] = [B, C]; 创建顺序:A 先,B/C 后
	inDeg map[string]int
}

func NewGraph() *Graph {
	return &Graph{
		nodes: map[string]struct{}{},
		out:   map[string][]string{},
		inDeg: map[string]int{},
	}
}

func (g *Graph) addNode(name string) {
	if _, ok := g.nodes[name]; ok {
		return
	}
	g.nodes[name] = struct{}{}
	g.out[name] = nil
	g.inDeg[name] = 0
}

func (g *Graph) AddEdge(frm, to string) {
	g.addNode(frm)
	g.addNode(to)
	// 防重复
	for _, t := range g.out[frm] {
		if t == to {
			return
		}
	}
	g.out[frm] = append(g.out[frm], to)
	g.inDeg[to]++
}

func (g *Graph) HasCycle() bool {
	// Kahn 出队节点数 < 节点总数 = 有环
	deg := make(map[string]int, len(g.inDeg))
	for k, v := range g.inDeg {
		deg[k] = v
	}
	q := list.New()
	for name, d := range deg {
		if d == 0 {
			q.PushBack(name)
		}
	}
	visited := 0
	for q.Len() > 0 {
		n := q.Front().Value.(string)
		q.Remove(q.Front())
		visited++
		for _, t := range g.out[n] {
			deg[t]--
			if deg[t] == 0 {
				q.PushBack(t)
			}
		}
	}
	return visited < len(g.nodes)
}

// TopoLayers — Kahn + 分层;每层 = 一批可并行执行节点;同层按名字排序保证确定性
func (g *Graph) TopoLayers() ([][]string, error) {
	deg := make(map[string]int, len(g.inDeg))
	for k, v := range g.inDeg {
		deg[k] = v
	}
	var layers [][]string
	for {
		var current []string
		for name, d := range deg {
			if d == 0 {
				current = append(current, name)
			}
		}
		if len(current) == 0 {
			break
		}
		sort.Strings(current) // 确定性
		layers = append(layers, current)
		for _, n := range current {
			deg[n] = -1 // 标记已访问
			for _, t := range g.out[n] {
				if deg[t] > 0 {
					deg[t]--
				}
			}
		}
	}
	visited := 0
	for _, l := range layers {
		visited += len(l)
	}
	if visited < len(g.nodes) {
		return nil, fmt.Errorf("cycle detected: visited %d of %d", visited, len(g.nodes))
	}
	return layers, nil
}

func (g *Graph) ApplyOrder() []string {
	layers, _ := g.TopoLayers()
	out := []string{}
	for _, l := range layers {
		out = append(out, l...)
	}
	return out
}

func (g *Graph) DestroyOrder() []string {
	o := g.ApplyOrder()
	rev := make([]string, len(o))
	for i, n := range o {
		rev[len(o)-1-i] = n
	}
	return rev
}

// ScheduleParallel — Terraform `apply -parallelism=N` 模拟
func (g *Graph) ScheduleParallel(maxPar int) [][]string {
	layers, _ := g.TopoLayers()
	var sched [][]string
	for _, layer := range layers {
		for off := 0; off < len(layer); off += maxPar {
			end := off + maxPar
			if end > len(layer) {
				end = len(layer)
			}
			sched = append(sched, append([]string{}, layer[off:end]...))
		}
	}
	return sched
}

// -----------------------------------------------------------------------------
// Demos
// -----------------------------------------------------------------------------

func printLayers(name string, layers [][]string) {
	fmt.Printf("  %s:\n", name)
	for i, l := range layers {
		fmt.Printf("    Layer %d (并行): %s\n", i+1, strings.Join(l, ", "))
	}
}

func main() {
	fmt.Println("=== Terraform 资源依赖图与拓扑排序 demo (Go 版) ===\n")

	// ----- Demo 1: 菱形 -----
	{
		g := NewGraph()
		g.AddEdge("aws_vpc.main", "aws_subnet.public")
		g.AddEdge("aws_vpc.main", "aws_security_group.web")
		g.AddEdge("aws_subnet.public", "aws_instance.web")
		g.AddEdge("aws_security_group.web", "aws_instance.web")
		fmt.Println("--- Demo 1: 菱形 (VPC / Subnet+SG 并行 / Instance) ---")
		layers, _ := g.TopoLayers()
		printLayers("层级", layers)
		fmt.Printf("  apply  顺序: %v\n  destroy 顺序: %v\n\n", g.ApplyOrder(), g.DestroyOrder())
	}

	// ----- Demo 2: depends_on 显式依赖 -----
	{
		g := NewGraph()
		g.AddEdge("aws_iam_role.app", "aws_iam_instance_profile.app")
		g.AddEdge("aws_iam_role.app", "aws_iam_role_policy_attachment.s3_access")
		g.AddEdge("aws_iam_role_policy_attachment.s3_access", "aws_instance.app")
		g.AddEdge("aws_iam_instance_profile.app", "aws_instance.app")
		fmt.Println("--- Demo 2: depends_on 显式依赖 (policy 必须先 attached) ---")
		layers, _ := g.TopoLayers()
		printLayers("层级", layers)
		fmt.Println()
	}

	// ----- Demo 3: 环检测 -----
	{
		g := NewGraph()
		g.AddEdge("A", "B")
		g.AddEdge("B", "C")
		g.AddEdge("C", "A")
		fmt.Println("--- Demo 3: 环检测 (A → B → C → A) ---")
		fmt.Printf("  HasCycle() = %v\n", g.HasCycle())
		_, err := g.TopoLayers()
		fmt.Printf("  TopoLayers 错误: %v\n\n", err)
	}

	// ----- Demo 4: parallelism 跨批 -----
	{
		g := NewGraph()
		for i := 0; i < 11; i++ {
			g.AddEdge(fmt.Sprintf("res_%d", i), "aggregator")
		}
		fmt.Println("--- Demo 4: parallelism=10 (11 个无依赖 + 1 聚合) ---")
		sched := g.ScheduleParallel(10)
		for i, batch := range sched {
			fmt.Printf("    batch %2d: %s\n", i+1, strings.Join(batch, ", "))
		}
		fmt.Println()
	}

	// ----- Demo 5: HasCycle 检查通过但 topo 失败(破坏入度初值) -----
	{
		g := NewGraph()
		g.AddEdge("X", "Y")
		g.AddEdge("Y", "X")
		fmt.Println("--- Demo 5: 双节点环 (X ↔ Y) ---")
		fmt.Printf("  HasCycle = %v, 节点数 %d\n", g.HasCycle(), len(g.nodes))
		fmt.Println()
	}
}
