// 火山模型算子库: Row / Operator 接口 + 图 + 叶/一元/二元算子 (Go 版).
// 语义依据 Neo4j Cypher Manual "Execution plans"(详见同目录 pipeline.go).
package main

import (
	"fmt"
	"sort"
)

type Row map[string]string

// Operator 火山模型: Open(ctx) 初始化, Next() 返回下一行, ok=false 表示流尽.
type Operator interface {
	Open(g *Graph)
	Next() (Row, bool)
	Describe(depth int) string
}

// ---------- 叶算子 ----------

// NodeByLabelScan 按标签扫描节点(叶算子).
type NodeByLabelScan struct {
	Var, Label string
	g          *Graph
	ids        []string
	i          int
}

func (s *NodeByLabelScan) Open(g *Graph) {
	s.g, s.i = g, 0
	s.ids = nil
	for _, id := range sortedIDs(g) {
		if hasLabel(g, id, s.Label) {
			s.ids = append(s.ids, id)
		}
	}
}

func (s *NodeByLabelScan) Next() (Row, bool) {
	if s.i >= len(s.ids) {
		return nil, false
	}
	r := Row{s.Var: s.ids[s.i]}
	s.i++
	return r, true
}

func (s *NodeByLabelScan) Describe(d int) string {
	return pad(d) + fmt.Sprintf("NodeByLabelScan  %s:%s", s.Var, s.Label)
}

// ---------- 一元算子 ----------

// Expand(All): 对已绑定节点沿出边扩展一跳(无索引邻接).
type Expand struct {
	Child            Operator
	FromVar, RelVar  string
	RelType, ToVar   string
	g                *Graph
	pending          []Row
}

func (e *Expand) Open(g *Graph) { e.g, e.pending = g, nil; e.Child.Open(g) }

func (e *Expand) Next() (Row, bool) {
	for len(e.pending) == 0 {
		r, ok := e.Child.Next()
		if !ok {
			return nil, false
		}
		for _, edge := range e.g.Out[r[e.FromVar]] {
			nr := Row{}
			for k, v := range r {
				nr[k] = v
			}
			nr[e.RelVar], nr[e.ToVar] = edge.Rid, edge.Dst
			e.pending = append(e.pending, nr)
		}
	}
	r := e.pending[0]
	e.pending = e.pending[1:]
	return r, true
}

func (e *Expand) Describe(d int) string {
	return fmt.Sprintf("%sExpand  (%s)-[%s:%s]->(%s)\n%s",
		pad(d), e.FromVar, e.RelVar, e.RelType, e.ToVar, e.Child.Describe(d+1))
}

// Filter 按谓词过滤行.
type Filter struct {
	Child Operator
	Pred  func(Row) bool
	Text  string
}

func (f *Filter) Open(g *Graph) { f.Child.Open(g) }

func (f *Filter) Next() (Row, bool) {
	for {
		r, ok := f.Child.Next()
		if !ok {
			return nil, false
		}
		if f.Pred(r) {
			return r, true
		}
	}
}

func (f *Filter) Describe(d int) string {
	return pad(d) + "Filter  " + f.Text + "\n" + f.Child.Describe(d+1)
}

// Projection 计算表达式产生投影行.
type Projection struct {
	Child Operator
	Expr  func(Row) Row
}

func (p *Projection) Open(g *Graph) { p.Child.Open(g) }

func (p *Projection) Next() (Row, bool) {
	r, ok := p.Child.Next()
	if !ok {
		return nil, false
	}
	return p.Expr(r), true
}

func (p *Projection) Describe(d int) string {
	return pad(d) + "Projection\n" + p.Child.Describe(d+1)
}

// Sort Eager 算子: 必须收完全部输入(全量物化)才能产出第一行.
type Sort struct {
	Child Operator
	Key   func(Row) string
	buf   []Row
	i     int
}

func (s *Sort) Open(g *Graph) {
	s.Child.Open(g)
	s.buf, s.i = nil, 0
	for {
		r, ok := s.Child.Next()
		if !ok {
			break
		}
		s.buf = append(s.buf, r) // 全量缓冲: 内存代价 O(行数)
	}
	sort.Slice(s.buf, func(i, j int) bool { return s.Key(s.buf[i]) < s.Key(s.buf[j]) })
}

func (s *Sort) Next() (Row, bool) {
	if s.i >= len(s.buf) {
		return nil, false
	}
	r := s.buf[s.i]
	s.i++
	return r, true
}

func (s *Sort) Describe(d int) string {
	return pad(d) + "Sort  eager(排序需全量缓冲)\n" + s.Child.Describe(d+1)
}

// ---------- 二元算子 ----------

// CartesianProduct Join 风格: LHS 逐行 × RHS 全部行(两侧完整输入).
type CartesianProduct struct {
	Left, Right Operator
	g           *Graph
	rightRows   []Row
	leftIt      int
	leftRows    []Row
	rightIt     int
	curLeft     Row
}

func (c *CartesianProduct) Open(g *Graph) {
	c.g, c.leftIt, c.rightIt, c.curLeft = g, 0, 0, nil
	c.Left.Open(g), c.Right.Open(g)
	c.rightRows = execAll(c.Right)
	c.leftRows = execAll(c.Left)
}

func execAll(op Operator) []Row {
	var rows []Row
	for {
		r, ok := op.Next()
		if !ok {
			return rows
		}
		rows = append(rows, r)
	}
}

func (c *CartesianProduct) Next() (Row, bool) {
	for {
		if c.curLeft != nil && c.rightIt < len(c.rightRows) {
			rr := c.rightRows[c.rightIt]
			c.rightIt++
			out := Row{}
			for k, v := range c.curLeft {
				out[k] = v
			}
			for k, v := range rr {
				out[k] = v
			}
			return out, true
		}
		if c.leftIt >= len(c.leftRows) {
			return nil, false
		}
		c.curLeft = c.leftRows[c.leftIt]
		c.leftIt++
		c.rightIt = 0
	}
}

// 官方显示约定: 二元算子 RHS 先显示且缩进更深.
func (c *CartesianProduct) Describe(d int) string {
	return pad(d) + "CartesianProduct\n" + c.Right.Describe(d+1) + "\n" + c.Left.Describe(d)
}

// Apply 风格: LHS 每行驱动 RHS 重新执行一次(RightFactory 基于左行绑定构造).
type Apply struct {
	Left         Operator
	RightFactory func(bindings Row) Operator
	RightLabel   string
	g            *Graph
	pending      []Row
}

func (a *Apply) Open(g *Graph) { a.g = g; a.pending = nil; a.Left.Open(g) }

func (a *Apply) Next() (Row, bool) {
	for len(a.pending) == 0 {
		lr, ok := a.Left.Next()
		if !ok {
			return nil, false
		}
		rhs := a.RightFactory(lr) // RHS 以 LHS 绑定重启: 嵌套循环
		rhs.Open(a.g)
		for {
			rr, ok := rhs.Next()
			if !ok {
				break
			}
			out := Row{}
			for k, v := range lr {
				out[k] = v
			}
			for k, v := range rr {
				out[k] = v
			}
			a.pending = append(a.pending, out)
		}
	}
	r := a.pending[0]
	a.pending = a.pending[1:]
	return r, true
}

func (a *Apply) Describe(d int) string {
	return pad(d) + "Apply  (" + a.RightLabel + ": 每左行重启)\n" + a.Left.Describe(d+1)
}
