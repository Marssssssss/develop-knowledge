// Swift 参数包(变长泛型)模型的 Go 侧转写。
// 对照 apple/swift-evolution@main 的 SE-0393 / SE-0398 / SE-0399 / SE-0408。
package main

import (
	"fmt"
	"sort"
)

// ---- 表达式节点 ----
type Kind int

const (
	kindEach Kind = iota
	kindRepeat
	kindNode
)

type Expr struct {
	Kind  Kind
	Name  string
	Parts []*Expr
}

func eachOf(name string) *Expr { return &Expr{Kind: kindEach, Name: name} }

func repeatOf(parts ...*Expr) *Expr { return &Expr{Kind: kindRepeat, Parts: parts} }

func nodeOf(label string, parts ...*Expr) *Expr {
	return &Expr{Kind: kindNode, Name: label, Parts: parts}
}

// captures 包扩展表达式捕获了哪些包:模式里的 each p 算,内层 repeat 不穿透。
func captures(pattern *Expr) map[string]bool {
	out := map[string]bool{}
	var stack []*Expr
	if pattern.Kind == kindRepeat {
		stack = append(stack, pattern.Parts...)
	} else {
		stack = append(stack, pattern)
	}
	for len(stack) > 0 {
		n := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		switch n.Kind {
		case kindEach:
			out[n.Name] = true
		case kindRepeat:
			continue // 不进入内层包扩展
		default:
			stack = append(stack, n.Parts...)
		}
	}
	return out
}

func sortedKeys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// ---- 形状求解:同形状要求合并成等价类 ----
type ShapeSolver struct {
	parent   map[string]string
	concrete map[string]int
}

func newShapeSolver(packs []string) *ShapeSolver {
	s := &ShapeSolver{parent: map[string]string{}, concrete: map[string]int{}}
	for _, p := range packs {
		s.parent[p] = p
	}
	return s
}

func (s *ShapeSolver) find(x string) string {
	for s.parent[x] != x {
		s.parent[x] = s.parent[s.parent[x]]
		x = s.parent[x]
	}
	return x
}

func (s *ShapeSolver) union(a, b string) error {
	ra, rb := s.find(a), s.find(b)
	if ra == rb {
		return nil
	}
	s.parent[rb] = ra
	ca, okA := s.concrete[ra]
	cb, okB := s.concrete[rb]
	if okA && okB && ca != cb {
		return fmt.Errorf("conflict")
	}
	if !okA && okB {
		s.concrete[ra] = cb
	}
	return nil
}

func (s *ShapeSolver) sameShape(a, b string) bool { return s.find(a) == s.find(b) }

func (s *ShapeSolver) classOf(p string) []string {
	root := s.find(p)
	var out []string
	for q := range s.parent {
		if s.find(q) == root {
			out = append(out, q)
		}
	}
	sort.Strings(out)
	return out
}

// imposeConcrete 给包强加具体形状;与已有长度冲突则报 conflict
func (s *ShapeSolver) imposeConcrete(pack string, length int) error {
	root := s.find(pack)
	if have, ok := s.concrete[root]; ok && have != length {
		return fmt.Errorf("conflict")
	}
	s.concrete[root] = length
	return nil
}

// inferFromPackExpansion 参数类型 / 返回类型 / 尾随 where 处的包扩展:两两合并
func inferFromPackExpansion(s *ShapeSolver, packs []string) {
	ps := append([]string{}, packs...)
	sort.Strings(ps)
	for i := 0; i < len(ps); i++ {
		for j := i + 1; j < len(ps); j++ {
			s.union(ps[i], ps[j])
		}
	}
}

// checkPackExpansion 其它位置的包扩展必须已经同形状
func checkPackExpansion(s *ShapeSolver, packs []string, position string) error {
	ps := append([]string{}, packs...)
	sort.Strings(ps)
	for i := 0; i < len(ps); i++ {
		for j := i + 1; j < len(ps); j++ {
			if !s.sameShape(ps[i], ps[j]) {
				return fmt.Errorf("pack expansion requires known same shape at %s", position)
			}
		}
	}
	return nil
}

// ---- 变长泛型类型的实参绑定 ----
type Param struct {
	IsPack bool
	Name   string
}

// bindGenericArgs 非包形参构成固定前缀与后缀,包吃掉中间那一段
func bindGenericArgs(spec []Param, args []string) (map[string]interface{}, error) {
	packs := 0
	for _, p := range spec {
		if p.IsPack {
			packs++
		}
	}
	if packs > 1 {
		return nil, fmt.Errorf("generic type declares more than one parameter pack")
	}
	scalars := 0
	for _, p := range spec {
		if !p.IsPack {
			scalars++
		}
	}
	if len(args) < scalars {
		return nil, fmt.Errorf("expected at least %d generic arguments", scalars)
	}
	out := map[string]interface{}{}
	prefix := 0
	for prefix < len(spec) && !spec[prefix].IsPack {
		prefix++
	}
	suffix := 0
	for suffix < len(spec) && !spec[len(spec)-1-suffix].IsPack {
		suffix++
	}
	idx := 0
	for i := 0; i < prefix; i++ {
		out[spec[i].Name] = args[idx]
		idx++
	}
	packSlice := args[idx : len(args)-suffix]
	for _, p := range spec {
		if p.IsPack {
			out[p.Name] = packSlice
		}
	}
	for i := len(spec) - suffix; i < len(spec); i++ {
		out[spec[i].Name] = args[len(args)-suffix+(i-(len(spec)-suffix))]
	}
	return out, nil
}

// ---- 要求推断(SE-0398) ----
type Arg struct {
	IsPack bool
	Name   string
}

type Req struct {
	IsExpansion bool
	Name        string
}

// inferRequirements imposingKind 为 "scalar" 或 "expansion"
func inferRequirements(imposingKind string, applied []Arg, packDepths map[string]int) ([]Req, error) {
	var reqs []Req
	if imposingKind == "scalar" {
		for _, a := range applied {
			reqs = append(reqs, Req{IsExpansion: a.IsPack, Name: a.Name})
		}
		return reqs, nil
	}
	if len(packDepths) > 1 {
		seen := map[int]bool{}
		for _, d := range packDepths {
			seen[d] = true
		}
		if len(seen) > 1 {
			return nil, fmt.Errorf("multiple pack elements captured at expansions of different depth")
		}
	}
	for _, a := range applied {
		reqs = append(reqs, Req{IsExpansion: a.IsPack, Name: a.Name})
	}
	return reqs, nil
}

// ---- 包遍历(SE-0408) ----
type Event struct {
	IsEval bool
	Text   string
	Index  int
}

// iterateOverPack for-in 里 pattern 每次迭代才求值一次,break 后不再求值
func iterateOverPack(n int, pattern func(int) string, breakAt int, hasBreak bool) []Event {
	var events []Event
	for i := 0; i < n; i++ {
		events = append(events, Event{IsEval: true, Text: pattern(i)})
		events = append(events, Event{IsEval: false, Index: i})
		if hasBreak && i == breakAt {
			break
		}
	}
	return events
}

// expandAll repeat 表达式:一次性把 n 份模式全部求值
func expandAll(n int, pattern func(int) string) []string {
	var out []string
	for i := 0; i < n; i++ {
		out = append(out, pattern(i))
	}
	return out
}

// abstractTupleExample SE-0399 的四组输出:区分 tuple 与 each tuple
func abstractTupleExample(valuePack []int, tupleValue []int) map[string][][2]int {
	out := map[string][][2]int{}
	for _, v := range valuePack {
		out["each value"] = append(out["each value"], [2]int{v, 0})
	}
	for _, v := range tupleValue {
		out["each tuple"] = append(out["each tuple"], [2]int{v, 0})
	}
	for _, v := range valuePack {
		out["value + tuple"] = append(out["value + tuple"], [2]int{v, len(tupleValue)})
	}
	for i, v := range valuePack {
		out["value + each tuple"] = append(out["value + each tuple"], [2]int{v, tupleValue[i]})
	}
	return out
}
