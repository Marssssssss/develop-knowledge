// Cheney 半空间复制式 GC 的算法实现(Go 版)。
//
// 权威来源(实际联网阅读):
//   v8.dev/blog/trash-talk               —— V8 Orinoco 的 young generation Scavenger
//   v8.dev/blog/orinoco-parallel-scavenger —— Cheney 半空间复制的完整描述
//
// 按"字"计账(1 字 = 一个指针槽),只保留算法的结构性质。
package main

import "strconv"

const (
	word        = 8
	headerWords = 2 // forward 字 + size 字
)

// slot 是一个指针槽 —— 移动式 GC 必须能**改写**它,这就是"精确根"的含义。
type slot struct {
	owner string
	name  string
	val   string // "" 表示空引用
}

type object struct {
	oid          string
	payloadWords int
	fields       []*slot
}

func (o *object) words() int { return headerWords + o.payloadWords + len(o.fields) }

func (o *object) newSlot(name string) *slot {
	s := &slot{name: name}
	o.fields = append(o.fields, s)
	return s
}

// chain 是一个可被 Cheney 复制的对象图。
type chain struct {
	objs  map[string]*object
	roots []*slot

	allocWords  int
	scanWords   int
	copied      int
	forwardHits int
}

func newChain() *chain { return &chain{objs: map[string]*object{}} }

func (c *chain) add(oid string, payloadWords int) *object {
	o := &object{oid: oid, payloadWords: payloadWords}
	c.objs[oid] = o
	return o
}

func (c *chain) root(name, oid string) *slot {
	s := &slot{owner: "root", name: name, val: oid}
	c.roots = append(c.roots, s)
	return s
}

func (c *chain) edge(a, name string) *slot { return c.objs[a].newSlot(name) }

type spaceEntry struct {
	off int
	obj *object
}

type step struct {
	kind string // copy / forward / scan
	from string
	to   string
	oid  string
	next int // 该步之后的 alloc
	scan int // 该步之后的 scan
}

// cheney 执行一次 Cheney 半空间复制。
//
// trace 记录每一步的 alloc/scan 指针 —— Cheney 不需要递归栈,是因为它把
// "待扫描队列"直接编码成 to-space 上的 [scan, alloc) 区间。
func cheney(src *chain) ([]spaceEntry, map[string]string, []step) {
	var toSpace []spaceEntry
	forward := map[string]string{}
	nextOid := 1000
	alloc, scan := 0, 0
	var trace []step

	evacuate := func(s *slot) {
		old := s.val
		if old == "" {
			return
		}
		if nid, ok := forward[old]; ok {
			src.forwardHits++
			s.val = nid // 转发指针命中:只改引用,不再复制
			trace = append(trace, step{kind: "forward", from: old, to: nid, next: alloc, scan: scan})
			return
		}
		o := src.objs[old]
		nid := "n" + strconv.Itoa(nextOid)
		nextOid++
		n := &object{oid: nid, payloadWords: o.payloadWords}
		toSpace = append(toSpace, spaceEntry{off: alloc, obj: n})
		forward[old] = nid
		s.val = nid
		for _, f := range o.fields { // 槽对象整体搬过去,槽值稍后由 scan 改写
			n.newSlot(f.name).val = f.val
		}
		src.allocWords += o.words()
		src.copied++
		alloc += o.words()
		trace = append(trace, step{kind: "copy", from: old, to: nid, next: alloc, scan: scan})
	}

	for _, s := range src.roots {
		evacuate(s)
	}
	for scan < alloc {
		var cur *object
		for _, e := range toSpace {
			if e.off == scan {
				cur = e.obj
				break
			}
		}
		if cur == nil {
			panic("scan 未对齐到对象起点")
		}
		src.scanWords += cur.words()
		for _, f := range cur.fields {
			evacuate(f)
		}
		scan += cur.words()
		trace = append(trace, step{kind: "scan", oid: cur.oid, next: alloc, scan: scan})
	}
	return toSpace, forward, trace
}

// buildChainGraph:A->B, A->C, B->D, C->D(共享), D->E;另有不可达的 X->Y 与孤立 Z。
func buildChainGraph() *chain {
	g := newChain()
	for _, oid := range []string{"A", "B", "C", "D", "E", "X", "Y", "Z"} {
		g.add(oid, 1)
	}
	g.root("r1", "A")
	g.edge("A", "b").val = "B"
	g.edge("A", "c").val = "C"
	g.edge("B", "d").val = "D"
	g.edge("C", "d").val = "D"
	g.edge("D", "e").val = "E"
	g.edge("X", "y").val = "Y" // 不可达
	return g
}

// oldOf 由转发表反查旧 oid。
func oldOf(forward map[string]string, newID string) string {
	for k, v := range forward {
		if v == newID {
			return k
		}
	}
	return ""
}
