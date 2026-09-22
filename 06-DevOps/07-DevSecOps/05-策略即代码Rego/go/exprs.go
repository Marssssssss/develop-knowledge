package main

// OPA v1/ast 的表达式（Expr）最小模型。
//
// Terms() 供 outputVarsForTerms 遍历；Vars() 用**含引用头**的口径，
// 用于 bodyVars / unsafe 判定。

import "strings"

// ---------------------------------------------------------------- 表达式

type Expr interface {
	Vars() VarSet
	Terms() []Term
	String() string
}

type Eq struct {
	Lhs, Rhs  Term
	FromAssign bool
}

func (e *Eq) Vars() VarSet { return Union(e.Lhs.Vars(), e.Rhs.Vars()) }
func (e *Eq) Terms() []Term { return []Term{e.Lhs, e.Rhs} }
func (e *Eq) String() string {
	op := "=="
	if e.FromAssign {
		op = ":="
	}
	return e.Lhs.String() + " " + op + " " + e.Rhs.String()
}

// Call 的 Operands 不含操作符项（源码 terms[0] 是操作符）。
type Call struct {
	Name     string
	Operands []Term
	Arity    int
}

func (c *Call) Vars() VarSet {
	out := VarSet{}
	for _, t := range c.Operands {
		out = Union(out, t.Vars())
	}
	return out
}
func (c *Call) Terms() []Term { return c.Operands }
func (c *Call) String() string {
	ps := make([]string, len(c.Operands))
	for i, t := range c.Operands {
		ps[i] = t.String()
	}
	return c.Name + "(" + strings.Join(ps, ", ") + ")"
}

// Not 取反表达式：IsNegated 直接返回空产出。
type Not struct{ Inner []Expr }

func (n *Not) Vars() VarSet {
	out := VarSet{}
	for _, e := range n.Inner {
		out = Union(out, e.Vars())
	}
	return out
}
func (n *Not) Terms() []Term {
	var out []Term
	for _, e := range n.Inner {
		out = append(out, e.Terms()...)
	}
	return out
}
func (n *Not) String() string {
	ps := make([]string, len(n.Inner))
	for i, e := range n.Inner {
		ps[i] = e.String()
	}
	return "not {" + strings.Join(ps, "; ") + "}"
}

type With struct {
	Inner  Expr
	Target Term
}

func (w *With) Vars() VarSet { return Union(w.Inner.Vars(), w.Target.Vars()) }
func (w *With) Terms() []Term {
	inner := w.Inner.Terms()
	out := make([]Term, 0, len(inner)+1)
	out = append(out, inner...)
	return append(out, w.Target)
}
func (w *With) String() string {
	return w.Inner.String() + " with input as " + w.Target.String()
}

// Logical and / or：不给外围 body 贡献绑定。
type Logical struct {
	Op    string
	Inner []Expr
}

func (l *Logical) Vars() VarSet {
	out := VarSet{}
	for _, e := range l.Inner {
		out = Union(out, e.Vars())
	}
	return out
}
func (l *Logical) Terms() []Term {
	var out []Term
	for _, e := range l.Inner {
		out = append(out, e.Terms()...)
	}
	return out
}
func (l *Logical) String() string {
	ps := make([]string, len(l.Inner))
	for i, e := range l.Inner {
		ps[i] = e.String()
	}
	return l.Op + "{" + strings.Join(ps, "; ") + "}"
}

// Every 的 Key/Value 是声明变量，不是 output var
// （源码 rewriteEveryStatement 走 declaredVar 路径）。
type Every struct {
	Domain Term
	Keys   []string
	Body   []Expr
}

func (ev *Every) Vars() VarSet {
	out := Union(ev.Domain.Vars(), NewVarSet(ev.Keys...))
	for _, e := range ev.Body {
		out = Union(out, e.Vars())
	}
	return out
}
func (ev *Every) Terms() []Term {
	out := []Term{ev.Domain}
	for _, e := range ev.Body {
		out = append(out, e.Terms()...)
	}
	return out
}
func (ev *Every) String() string {
	return "every " + strings.Join(ev.Keys, ",") + " in " + ev.Domain.String()
}

// Bare 裸项表达式（源码 case *Term）
type Bare struct{ T Term }

func (b *Bare) Vars() VarSet  { return b.T.Vars() }
func (b *Bare) Terms() []Term { return []Term{b.T} }
func (b *Bare) String() string { return b.T.String() }
