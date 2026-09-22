package main

// Unify（v1/ast/unify.go）与 outputVarsForExpr（v1/ast/compile.go）的转写。
//
// 两个极易写反的点：
//
//	outputVarsForTerms 的结果要与 safe 取并集再交给 Unify，最后再减掉 safe；
//	不是「拿 Unify 结果代替」。
//	:= 会把 LHS 的变量整体从安全基里删掉（issue #3546），所以
//	input.a[i] == 1 产出 {i} 而 input.a[i] := 1 产出空集。

// ---------------------------------------------------------------- Unifier

type Unifier struct {
	safe    VarSet
	unified VarSet
	unknown map[string]VarSet
}

func (u *Unifier) isSafe(v string) bool { return u.safe[v] || u.unified[v] }

func (u *Unifier) markSafe(v string) {
	u.unified[v] = true

	// x 的依赖随之安全
	deps := u.unknown[v]
	delete(u.unknown, v)
	for w := range deps {
		u.markSafe(w)
	}

	// 依赖 x 的变量若再无依赖，也变安全
	for w, ds := range u.unknown {
		if ds[v] {
			delete(ds, v)
			if len(ds) == 0 {
				delete(u.unknown, w)
				u.markSafe(w)
			}
		}
	}
}

func (u *Unifier) markUnknown(a, b string) {
	if u.unknown[a] == nil {
		u.unknown[a] = VarSet{}
	}
	u.unknown[a][b] = true
}

func (u *Unifier) markAllSafe(t Term) {
	for v := range t.OutputVars() {
		u.markSafe(v)
	}
}

func (u *Unifier) unifyAll(a *Var, b Term) {
	if u.isSafe(a.Name) {
		u.markAllSafe(b)
		return
	}
	unsafe := Diff(Diff(b.OutputVars(), u.safe), u.unified)
	if len(unsafe) == 0 {
		u.markSafe(a.Name)
		return
	}
	for v := range unsafe {
		u.markUnknown(a.Name, v)
	}
}

func (u *Unifier) unify(a, b Term) {
	switch x := a.(type) {

	case *Var:
		switch y := b.(type) {
		case *Var:
			if u.isSafe(y.Name) {
				u.markSafe(x.Name)
			} else if u.isSafe(x.Name) {
				u.markSafe(y.Name)
			} else {
				u.markUnknown(x.Name, y.Name)
				u.markUnknown(y.Name, x.Name)
			}
		case *Arr:
			u.unifyAll(x, y)
		case *Obj:
			u.unifyAll(x, y)
		case *Ref:
			if isRefSafe(y, u.safe) {
				u.markSafe(x.Name)
			}
		default: // Const / SetT / CallT
			u.markSafe(x.Name)
		}

	case *Ref:
		if isRefSafe(x, u.safe) {
			switch y := b.(type) {
			case *Var:
				u.markSafe(y.Name)
			case *Arr:
				u.markAllSafe(y)
			case *Obj:
				u.markAllSafe(y)
			}
		}

	case *Arr:
		switch y := b.(type) {
		case *Var:
			u.unifyAll(y, x)
		case *Arr:
			if len(x.Items) == len(y.Items) {
				for i := range x.Items {
					u.unify(x.Items[i], y.Items[i])
				}
			}
		}

	case *Obj:
		switch y := b.(type) {
		case *Var:
			u.unifyAll(y, x)
		case *Obj:
			if len(x.Keys) == len(y.Keys) {
				idx := map[string]Term{}
				for i := range y.Keys {
					idx[termKey(y.Keys[i])] = y.Vals[i]
				}
				for i := range x.Keys {
					if v, ok := idx[termKey(x.Keys[i])]; ok {
						u.unify(x.Vals[i], v)
					}
				}
			}
		}

	default: // Const / SetT 在左侧
		if y, ok := b.(*Var); ok {
			u.markSafe(y.Name)
		}
	}
}

// termKey 是对象键的比较口径：常量键按值，变量键按名字（OPA 用精确匹配）。
func termKey(t Term) string {
	if c, ok := t.(*Const); ok {
		return "c:" + c.Value
	}
	return "t:" + t.String()
}

func Unify(safe VarSet, a, b Term) VarSet {
	u := &Unifier{safe: safe, unified: VarSet{}, unknown: map[string]VarSet{}}
	u.unify(a, b)
	return u.unified
}

// ---------------------------------------------------------------- outputVars

func walkTerms(t Term, out *[]Term) {
	*out = append(*out, t)
	switch x := t.(type) {
	case *Ref:
		walkTerms(x.Head, out)
		for _, p := range x.Parts {
			walkTerms(p, out)
		}
	case *Arr:
		for _, i := range x.Items {
			walkTerms(i, out)
		}
	case *Obj:
		for i := range x.Keys {
			walkTerms(x.Keys[i], out)
			walkTerms(x.Vals[i], out)
		}
	case *SetT:
		for _, i := range x.Items {
			walkTerms(i, out)
		}
	case *CallT:
		for _, i := range x.Operands {
			walkTerms(i, out)
		}
	}
}

// outputVarsForTerms：收集「ref-safe 且非 ground」的引用的下标变量。
func outputVarsForTerms(e Expr, safe VarSet) VarSet {
	var all []Term
	if b, ok := e.(*Bare); ok {
		all = []Term{b.T}
	}
	for _, t := range e.Terms() {
		walkTerms(t, &all)
	}
	out := VarSet{}
	for _, x := range all {
		if r, ok := x.(*Ref); ok {
			if !isRefSafe(r, safe) {
				continue
			}
			if !r.IsGround() {
				out = Union(out, r.OutputVars())
			}
		}
	}
	return out
}

func outputVarsForEq(e *Eq, safe VarSet) VarSet {
	out := Union(outputVarsForTerms(e, safe), safe)
	if e.FromAssign {
		for v := range e.Lhs.Vars() { // issue #3546
			delete(out, v)
		}
	}
	out = Union(out, Unify(out, e.Lhs, e.Rhs))
	return Diff(out, safe)
}

func outputVarsForCall(e *Call, safe VarSet) VarSet {
	if e.Arity < 0 { // 未知 arity（源码 ar < 0）
		return VarSet{}
	}
	out := outputVarsForTerms(e, safe)
	// numInputTerms = arity + 1（含操作符项）
	if e.Arity >= len(e.Operands) {
		return out
	}
	unsafe := VarSet{}
	for _, t := range e.Operands[:e.Arity] {
		unsafe = Union(unsafe, t.OutputVars())
	}
	unsafe = Diff(Diff(unsafe, out), safe)
	if len(unsafe) > 0 {
		return VarSet{}
	}
	for _, t := range e.Operands[e.Arity:] {
		out = Union(out, t.OutputVars())
	}
	return out
}

func outputVarsForExpr(e Expr, safe VarSet) VarSet {
	switch x := e.(type) {
	case *Not: // IsNegated
		return VarSet{}
	case *With:
		if len(Diff(x.Target.Vars(), safe)) > 0 { // with 输入必须已安全
			return VarSet{}
		}
		return outputVarsForExpr(x.Inner, safe)
	case *Bare:
		return outputVarsForTerms(x, safe)
	case *Every:
		return outputVarsForTerms(x, safe)
	case *Logical: // and / or 不贡献绑定
		return VarSet{}
	case *Eq:
		return outputVarsForEq(x, safe)
	case *Call:
		return outputVarsForCall(x, safe)
	}
	return VarSet{}
}

// outputVarsForBody：把 safe 逐条喂进去累积，最后再减掉原始 safe。
func outputVarsForBody(body []Expr, safe VarSet) VarSet {
	o := Union(VarSet{}, safe)
	for _, e := range body {
		o = Union(o, outputVarsForExpr(e, o))
	}
	return Diff(o, safe)
}
