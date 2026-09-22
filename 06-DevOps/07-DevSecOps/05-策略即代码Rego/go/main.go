package main

// reorderBodyForSafety / checkBodySafety / checkEvery 的转写，
// 外加一个对拍入口：复现 open-policy-agent/opa
// v1/ast/compile_test.go 的 TestOutputVarsForNode 全部用例。

import "fmt"

// ---------------------------------------------------------------- 构造糖

func c(s string) *Const              { return &Const{Value: s} }
func v(s string) *Var                { return &Var{Name: s} }
func ref(head Term, parts ...Term) *Ref { return &Ref{Head: head, Parts: parts} }
func arr(items ...Term) *Arr         { return &Arr{Items: items} }
func st(items ...Term) *SetT         { return &SetT{Items: items} }
func ct(name string, ops ...Term) *CallT { return &CallT{Name: name, Operands: ops} }
func eq(a, b Term) *Eq               { return &Eq{Lhs: a, Rhs: b} }
func assign(a, b Term) *Eq           { return &Eq{Lhs: a, Rhs: b, FromAssign: true} }
func call(name string, arity int, ops ...Term) *Call {
	return &Call{Name: name, Operands: ops, Arity: arity}
}

// obj 接受交替的键/值项
func obj(kv ...Term) *Obj {
	o := &Obj{}
	for i := 0; i+1 < len(kv); i += 2 {
		o.Keys = append(o.Keys, kv[i])
		o.Vals = append(o.Vals, kv[i+1])
	}
	return o
}

// ---------------------------------------------------------------- 重排

// reorderBodyForSafety 返回 (重排后的 body, 仍 unsafe 的 {下标: 变量集})。
func reorderBodyForSafety(globals VarSet, body []Expr) ([]Expr, map[int]VarSet) {
	bodyVars := VarSet{}
	for _, e := range body {
		bodyVars = Union(bodyVars, e.Vars())
	}

	// 引用头计入 bodyVars，所以 input / data 会天然进入 safe
	safe := VarSet{}
	for x := range bodyVars {
		if globals[x] {
			safe[x] = true
		}
	}

	unsafe := map[int]VarSet{}
	for i, e := range body {
		if u := Diff(e.Vars(), safe); len(u) > 0 {
			unsafe[i] = u
		}
	}

	var reordered []Expr
	done := map[int]bool{}

	for {
		n := len(reordered)
		for i, e := range body {
			if done[i] {
				continue
			}
			ovs := outputVarsForExpr(e, safe)
			if us, ok := unsafe[i]; ok {
				for x := range us {
					if ovs[x] || safe[x] {
						delete(us, x)
					}
				}
				if len(us) == 0 {
					delete(unsafe, i)
				}
			}
			if _, ok := unsafe[i]; !ok {
				done[i] = true
				reordered = append(reordered, e)
				safe = Union(safe, ovs) // 本条产出随即变安全
			}
		}
		if len(reordered) == n { // 不动点：这一趟一条都没排进去
			break
		}
		if len(reordered) == len(body) {
			break
		}
	}
	return reordered, unsafe
}

// safetyErrorSlice：:= 的 LHS 只要同批里还有非 LHS 的 unsafe 变量就被压掉。
func safetyErrors(body []Expr, unsafe map[int]VarSet) []string {
	lhs := VarSet{}
	for i := range unsafe {
		if e, ok := body[i].(*Eq); ok && e.FromAssign {
			lhs = Union(lhs, e.Lhs.Vars())
		}
	}

	idx := make([]int, 0, len(unsafe))
	for i := range unsafe {
		idx = append(idx, i)
	}
	sortInts(idx)

	hasNon := false
	for _, i := range idx {
		for x := range unsafe[i] {
			if !lhs[x] {
				hasNon = true
			}
		}
	}

	var errs []string
	for _, i := range idx {
		for _, x := range Sorted(unsafe[i]) {
			if hasNon && lhs[x] {
				continue
			}
			errs = append(errs, "var "+x+" is unsafe")
		}
	}
	return errs
}

// checkBodySafety：报错时返回**原始** body（源码 c.err(errs...); return b）。
func checkBodySafety(globals VarSet, body []Expr) ([]Expr, []string) {
	ordered, unsafe := reorderBodyForSafety(globals, body)
	if len(unsafe) > 0 {
		return body, safetyErrors(body, unsafe)
	}
	return ordered, nil
}

// checkEvery：every 体内 key/value 按声明变量处理，domain 必须已安全。
func checkEvery(globals VarSet, ev *Every) []string {
	safe := Union(globals, NewVarSet(ev.Keys...))
	var errs []string
	for _, x := range Sorted(Diff(ev.Domain.Vars(), safe)) {
		errs = append(errs, "var "+x+" is unsafe")
	}
	_, unsafe := reorderBodyForSafety(safe, ev.Body)
	idx := make([]int, 0, len(unsafe))
	for i := range unsafe {
		idx = append(idx, i)
	}
	sortInts(idx)
	for _, i := range idx {
		for _, x := range Sorted(unsafe[i]) {
			errs = append(errs, "var "+x+" is unsafe")
		}
	}
	return errs
}

func sortInts(a []int) {
	for i := 1; i < len(a); i++ {
		for j := i; j > 0 && a[j] < a[j-1]; j-- {
			a[j], a[j-1] = a[j-1], a[j]
		}
	}
}

// ---------------------------------------------------------------- 对拍入口

var reserved = NewVarSet("data", "input")

type vector struct {
	note string
	body []Expr
	exp  []string
}

func vectors() []vector {
	return []vector{
		{"x", []Expr{&Bare{T: v("x")}}, nil},
		{"not x = 1", []Expr{&Not{Inner: []Expr{eq(v("x"), c("1"))}}}, nil},
		{"[x,[1]] = [1,[y]]", []Expr{eq(arr(v("x"), arr(c("1"))), arr(c("1"), arr(v("y"))))}, []string{"x", "y"}},
		{"{x,[1]} = {1,[y]}", []Expr{eq(st(v("x"), arr(c("1"))), st(c("1"), arr(v("y"))))}, nil},
		{"obj values", []Expr{eq(obj(c("foo"), v("x"), c("bar"), obj(c("baz"), c("1"))),
			obj(c("foo"), c("1"), c("bar"), obj(c("baz"), v("y"))))}, []string{"x", "y"}},
		{"obj keys are like sets", []Expr{eq(obj(c("foo"), v("x")), obj(v("y"), c("1")))}, nil},
		{"count([1,2,3], x)", []Expr{call("count", 1, arr(c("1"), c("2"), c("3")), v("x"))}, []string{"x"}},
		{"count(x) no arity", []Expr{call("count", -1, v("x"))}, nil},
		{"f(1,x) arity=-1", []Expr{call("f", -1, c("1"), v("x"))}, nil},
		{"f(1,x) arity=1", []Expr{call("f", 1, c("1"), v("x"))}, []string{"x"}},
		{"f(1,x) arity=2", []Expr{call("f", 2, c("1"), v("x"))}, nil},
		{"f(data.p[x], y)", []Expr{call("f", 1, ref(v("data"), c("p"), v("x")), v("y"))}, []string{"x", "y"}},
		{"f(x[1])", []Expr{call("f", 1, ref(v("x"), c("1")))}, nil},
		{"f(1,{x})", []Expr{call("f", 1, c("1"), st(v("x")))}, nil},
		{"f(1,{x:1})", []Expr{call("f", 1, c("1"), obj(c("x"), c("1")))}, nil},
		{"f(x,y) unsafe input", []Expr{call("f", 1, v("x"), v("y"))}, nil},
		{"1 with input as y", []Expr{&With{Inner: &Bare{T: c("1")}, Target: v("y")}}, nil},
		{"x = 1 with input as y", []Expr{&With{Inner: eq(v("x"), c("1")), Target: v("y")}}, nil},
		{"x = 1 with input as y (+y safe)", []Expr{&With{Inner: eq(v("x"), c("1")), Target: v("y")}}, []string{"x"}},
		{"data.p[x]", []Expr{&Bare{T: ref(v("data"), c("p"), v("x"))}}, []string{"x"}},
		{"p[x] unsafe head", []Expr{&Bare{T: ref(v("p"), v("x"))}}, nil},
		{"data.p[data.q[x]]", []Expr{&Bare{T: ref(v("data"), c("p"), ref(v("data"), c("q"), v("x")))}}, []string{"x"}},
		{"x = 1; y = x; z = y", []Expr{eq(v("x"), c("1")), eq(v("y"), v("x")), eq(v("z"), v("y"))},
			[]string{"x", "y", "z"}},
		{"{1,2}[1] = x", []Expr{eq(ref(st(c("1"), c("2")), c("1")), v("x"))}, []string{"x"}},
		{"x = 1; {x,2}[1] = y", []Expr{eq(v("x"), c("1")), eq(ref(st(v("x"), c("2")), c("1")), v("y"))},
			[]string{"x", "y"}},
		{"{x,2}[1] = y", []Expr{eq(ref(st(v("x"), c("2")), c("1")), v("y"))}, nil},
		{`z="abc"; x = split(z,"")[y]`, []Expr{eq(v("z"), c(`"abc"`)),
			eq(v("x"), ref(ct("split", v("z"), c(`""`)), v("y")))}, []string{"x", "y", "z"}},
		{`z="abc"; x = split(z,a)[y]`, []Expr{eq(v("z"), c(`"abc"`)),
			eq(v("x"), ref(ct("split", v("z"), v("a")), v("y")))}, []string{"z"}},
		{"every k,v in [1,2]", []Expr{&Every{Domain: arr(c("1"), c("2")), Keys: []string{"k", "v"}}}, nil},
		{"xs = []; every k,v in xs[i]", []Expr{eq(v("xs"), arr()),
			&Every{Domain: ref(v("xs"), v("i")), Keys: []string{"k", "v"}}}, []string{"i", "xs"}},
		{"every body 不外泄", []Expr{&Every{Domain: arr(), Keys: []string{"k", "v"},
			Body: []Expr{eq(v("i"), c("1"))}}}, nil},
	}
}

func main() {
	pass, fail := 0, 0
	for i, tc := range vectors() {
		safe := Union(VarSet{}, reserved)
		if i == 18 { // 第 19 条：extraSafe = {y}
			safe = Union(safe, NewVarSet("y"))
		}
		got := Sorted(outputVarsForBody(tc.body, safe))
		exp := tc.exp
		okv := len(got) == len(exp)
		if okv {
			for j := range got {
				if got[j] != exp[j] {
					okv = false
				}
			}
		}
		if okv {
			pass++
			fmt.Printf("ok   %-34s -> %v\n", tc.note, got)
		} else {
			fail++
			fmt.Printf("FAIL %-34s -> got %v want %v\n", tc.note, got, exp)
		}
	}

	// 重排演示
	g := NewVarSet("input", "data")
	body := []Expr{eq(v("b"), v("a")), eq(v("a"), ref(v("input"), c("i")))}
	ordered, errs := checkBodySafety(g, body)
	fmt.Printf("\nreorder: ")
	for _, e := range ordered {
		fmt.Printf("%s; ", e.String())
	}
	fmt.Printf("errs=%v\n", errs)

	// 对应官方 compile_test 的 `a := any([x])` -> 只报 "var x is unsafe"
	assignBody := []Expr{assign(v("n"), arr(v("x")))}
	_, errs2 := checkBodySafety(g, assignBody)
	fmt.Printf("n := [x] -> %v\n", errs2)

	fmt.Printf("\nPASS=%d FAIL=%d\n", pass, fail)
}
