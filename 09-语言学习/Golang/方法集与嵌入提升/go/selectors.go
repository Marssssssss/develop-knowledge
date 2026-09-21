// Package main 用 Go 实现语言规范里的「方法集 + 选择器解析」规则。
//
// 规则来源：go.dev/ref/spec 的 §Method sets、§Struct types（promoted methods）
// 与 §Selectors（shallowest depth）。Go 编译器在类型检查阶段做的正是这套计算，
// 这里把它做成可在运行时观察的数据结构。
package main

import (
	"fmt"
	"sort"
)

// GoType 一个命名类型：字段、嵌入字段、方法。
type GoType struct {
	Name        string
	Fields      map[string]string // 字段名 -> 类型名
	Embedded    []Embed           // 嵌入字段
	Methods     map[string]bool   // 方法名 -> 接收者是否为指针
	DefinedPtrOf string           // 非空表示「type Q *T」这类定义型指针
	IsInterface bool
	TypeSet     []string // 接口的类型集
}

// Embed 一个嵌入字段：字段名是未限定类型名，IsPtr 表示嵌入的是 *T。
type Embed struct {
	Field string
	Type  string
	IsPtr bool
}

// Universe 类型宇宙。
type Universe struct {
	Types map[string]*GoType
}

// Base 剥掉定义型指针：Q = *T2 → T2。
func (u *Universe) Base(name string) *GoType {
	t := u.Types[name]
	for t.DefinedPtrOf != "" {
		t = u.Types[t.DefinedPtrOf]
	}
	return t
}

// Sel 一次选择器的解析结果。
type Sel struct {
	Depth int
	Path  []string
	Kind  string // "field" / "method"
	Decl  string // 声明它的类型
}

// MethodSet 计算类型的方法集；pointer=true 表示求 *T 的方法集。
func (u *Universe) MethodSet(name string, pointer bool) map[string]Sel {
	t := u.Types[name]
	out := map[string]Sel{}
	if t.IsInterface {
		return u.interfaceMethodSet(t)
	}
	put := func(m string, decl string, isPtr bool, depth int) {
		if m == "_" {
			return
		}
		if _, dup := out[m]; dup {
			return // 方法集里名字必须唯一；重复时规范上不可选
		}
		out[m] = Sel{depth, nil, "method", decl}
	}
	for m, isPtr := range t.Methods {
		if pointer || !isPtr { // T 只收值接收者；*T 两者都收
			put(m, t.Name, isPtr, 0)
		}
	}
	for _, e := range t.Embedded {
		// 嵌入 *T：S 与 *S 都获得接收者为 T 或 *T 的方法
		// 嵌入 T ：S 获得接收者为 T 的方法，*S 额外获得接收者为 *T 的方法
		src := u.MethodSet(e.Type, pointer || e.IsPtr)
		for m, v := range src {
			put(m, v.Decl, false, v.Depth+1)
		}
	}
	return out
}

func (u *Universe) interfaceMethodSet(t *GoType) map[string]Sel {
	if len(t.TypeSet) == 0 {
		return map[string]Sel{}
	}
	common := map[string]int{}
	for i, n := range t.TypeSet {
		for m := range u.MethodSet(n, false) {
			common[m]++
		}
		_ = i
	}
	out := map[string]Sel{}
	for m, c := range common {
		if c == len(t.TypeSet) { // 交集：每个类型都有
			out[m] = Sel{0, nil, "method", t.Name}
		}
	}
	return out
}

// collect 按深度收集所有名为 f 的字段/方法。
func (u *Universe) collect(t *GoType, f string, depth int, path []string,
	out *[]Sel, visiting map[string]bool) {
	if visiting[t.Name] {
		return
	}
	visiting[t.Name] = true
	defer delete(visiting, t.Name)
	if _, ok := t.Methods[f]; ok {
		*out = append(*out, Sel{depth, append([]string{}, path...), "method", t.Name})
	}
	if _, ok := t.Fields[f]; ok {
		*out = append(*out, Sel{depth, append([]string{}, path...), "field", t.Name})
	}
	for _, e := range t.Embedded {
		if e.Field == f {
			*out = append(*out, Sel{depth, append([]string{}, path...), "field", t.Name})
		}
		u.collect(u.Types[e.Type], f, depth+1, append(path, e.Field), out, visiting)
	}
}

// Lookup 实现规范 §Selectors：取深度最浅且唯一的那一个。
func (u *Universe) Lookup(xType, f string) (Sel, error) {
	if f == "_" {
		return Sel{}, fmt.Errorf("selector must not be the blank identifier")
	}
	t := u.Base(xType)
	if t.IsInterface {
		if s, ok := u.MethodSet(t.Name, false)[f]; ok {
			return s, nil
		}
		return Sel{}, fmt.Errorf("x.%s undefined (type %s has no method %s)", f, t.Name, f)
	}
	var out []Sel
	u.collect(t, f, 0, nil, &out, map[string]bool{})
	if len(out) == 0 {
		return Sel{}, fmt.Errorf("x.%s undefined (type %s has no field or method %s)",
			f, t.Name, f)
	}
	best := out[0].Depth
	for _, s := range out {
		if s.Depth < best {
			best = s.Depth
		}
	}
	var cands []Sel
	for _, s := range out {
		if s.Depth == best {
			cands = append(cands, s)
		}
	}
	if len(cands) != 1 {
		return Sel{}, fmt.Errorf("ambiguous selector %s at depth %d (%d candidates)",
			f, best, len(cands))
	}
	return cands[0], nil
}

// Selector 处理「定义型指针」的例外：只有字段才有简写。
func (u *Universe) Selector(xType, f string) (Sel, error) {
	s, err := u.Lookup(xType, f)
	if err != nil {
		return s, err
	}
	if u.Types[xType].DefinedPtrOf != "" && s.Kind != "field" {
		return Sel{}, fmt.Errorf("%s.%s undefined ((*x).%s is a method, not a field)",
			xType, f, f)
	}
	return s, nil
}

// Names 把方法集的名字排序输出，便于断言。
func Names(ms map[string]Sel) []string {
	out := make([]string, 0, len(ms))
	for k := range ms {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// Show 把解析结果写成人类可读的一行。
func Show(s Sel) string {
	if len(s.Path) == 0 {
		return fmt.Sprintf("深度 %d，由 %s 自己声明（%s）", s.Depth, s.Decl, s.Kind)
	}
	return fmt.Sprintf("深度 %d，经 %v 提升（%s，声明于 %s）", s.Depth, s.Path, s.Kind, s.Decl)
}
