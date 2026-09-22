package main

// OPA v1/ast 的集合工具与项（Term）最小模型。
//
// 两套变量口径（全篇最容易写反的地方）：
//
//	Vars()       —— 含引用头。用于 bodyVars / unsafe 判定
//	               （SafetyCheckVisitorParams = {SkipRefCallHead, SkipClosures}，
//	                头只在是函数调用时才跳过）
//	OutputVars() —— 跳过所有层级的引用头。用于 outputVarsForTerms
//	               与调用的输入/输出判定（VarVisitorParams{SkipRefHead: true}）
//
// 另两个 visitor 开关同样各自落地成一个方法：
// SkipSets -> SetT.OutputVars() 恒为空；SkipObjectKeys -> Obj.OutputVars() 只看值。

import "strings"

// ---------------------------------------------------------------- 集合工具

type VarSet map[string]bool

func NewVarSet(names ...string) VarSet {
	s := VarSet{}
	for _, n := range names {
		s[n] = true
	}
	return s
}

func Union(a, b VarSet) VarSet {
	out := VarSet{}
	for k := range a {
		out[k] = true
	}
	for k := range b {
		out[k] = true
	}
	return out
}

func Diff(a, b VarSet) VarSet {
	out := VarSet{}
	for k := range a {
		if !b[k] {
			out[k] = true
		}
	}
	return out
}

func Subset(a, b VarSet) bool { return len(Diff(a, b)) == 0 }

func Sorted(s VarSet) []string {
	out := make([]string, 0, len(s))
	for k := range s {
		out = append(out, k)
	}
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j] < out[j-1]; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	return out
}

// ---------------------------------------------------------------- 项

type Term interface {
	Vars() VarSet
	OutputVars() VarSet
	IsGround() bool
	String() string
}

type Const struct{ Value string }

func (c *Const) Vars() VarSet      { return VarSet{} }
func (c *Const) OutputVars() VarSet { return VarSet{} }
func (c *Const) IsGround() bool    { return true }
func (c *Const) String() string    { return c.Value }

type Var struct{ Name string }

func (v *Var) Vars() VarSet       { return NewVarSet(v.Name) }
func (v *Var) OutputVars() VarSet { return NewVarSet(v.Name) }
func (v *Var) IsGround() bool     { return false }
func (v *Var) String() string     { return v.Name }

// Ref 形如 input.a[i]；Head 可以是 Var（变量名）也可以是复合项（如 {1,2}[1]）。
type Ref struct {
	Head  Term
	Parts []Term
}

func (r *Ref) Vars() VarSet {
	out := Union(VarSet{}, r.Head.Vars())
	for _, p := range r.Parts {
		out = Union(out, p.Vars())
	}
	return out
}

func (r *Ref) OutputVars() VarSet {
	out := VarSet{}
	for _, p := range r.Parts {
		out = Union(out, p.OutputVars())
	}
	return out
}

// IsGround 对应 Ref.IsGround: len(ref) < 2 || every(ref[1:], ground)
func (r *Ref) IsGround() bool {
	if len(r.Parts) == 0 {
		return true
	}
	for _, p := range r.Parts {
		if !p.IsGround() {
			return false
		}
	}
	return true
}

func (r *Ref) String() string {
	s := r.Head.String()
	for _, p := range r.Parts {
		if c, ok := p.(*Const); ok {
			s = s + "." + c.Value
		} else {
			s = s + "[" + p.String() + "]"
		}
	}
	return s
}

type Arr struct{ Items []Term }

func (a *Arr) Vars() VarSet {
	out := VarSet{}
	for _, i := range a.Items {
		out = Union(out, i.Vars())
	}
	return out
}
func (a *Arr) OutputVars() VarSet { return a.Vars() }
func (a *Arr) IsGround() bool {
	for _, i := range a.Items {
		if !i.IsGround() {
			return false
		}
	}
	return true
}
func (a *Arr) String() string {
	ps := make([]string, len(a.Items))
	for i, t := range a.Items {
		ps[i] = t.String()
	}
	return "[" + strings.Join(ps, ", ") + "]"
}

// Obj 的键也是项（Rego 允许 {"foo": x} 或 {y: 1}）。
type Obj struct {
	Keys []Term
	Vals []Term
}

func (o *Obj) Vars() VarSet {
	out := VarSet{}
	for i := range o.Keys {
		out = Union(out, o.Keys[i].Vars())
		out = Union(out, o.Vals[i].Vars())
	}
	return out
}

// OutputVars 对应 SkipObjectKeys：只看值
func (o *Obj) OutputVars() VarSet {
	out := VarSet{}
	for _, v := range o.Vals {
		out = Union(out, v.OutputVars())
	}
	return out
}

func (o *Obj) IsGround() bool {
	for i := range o.Keys {
		if !o.Keys[i].IsGround() || !o.Vals[i].IsGround() {
			return false
		}
	}
	return true
}
func (o *Obj) String() string {
	ps := make([]string, len(o.Keys))
	for i := range o.Keys {
		ps[i] = o.Keys[i].String() + ": " + o.Vals[i].String()
	}
	return "{" + strings.Join(ps, ", ") + "}"
}

// SetT 集合：unsafe 判定计入其变量，产出判定一律跳过（SkipSets）。
type SetT struct{ Items []Term }

func (s *SetT) Vars() VarSet {
	out := VarSet{}
	for _, i := range s.Items {
		out = Union(out, i.Vars())
	}
	return out
}
func (s *SetT) OutputVars() VarSet { return VarSet{} }
func (s *SetT) IsGround() bool {
	for _, i := range s.Items {
		if !i.IsGround() {
			return false
		}
	}
	return true
}
func (s *SetT) String() string {
	ps := make([]string, len(s.Items))
	for i, t := range s.Items {
		ps[i] = t.String()
	}
	return "{" + strings.Join(ps, ", ") + "}"
}

// CallT 是作为引用头出现的调用项（split(z, "")[y] 里的 split(z, "")）。
type CallT struct {
	Name     string
	Operands []Term
}

func (c *CallT) Vars() VarSet {
	out := VarSet{}
	for _, t := range c.Operands {
		out = Union(out, t.Vars())
	}
	return out
}
func (c *CallT) OutputVars() VarSet { return c.Vars() }
func (c *CallT) IsGround() bool     { return false }
func (c *CallT) String() string {
	ps := make([]string, len(c.Operands))
	for i, t := range c.Operands {
		ps[i] = t.String()
	}
	return c.Name + "(" + strings.Join(ps, ", ") + ")"
}

// isRefSafe：只看头是否安全，下标变量不影响（复合头走 default 分支）。
func isRefSafe(r *Ref, safe VarSet) bool { return Subset(r.Head.Vars(), safe) }
