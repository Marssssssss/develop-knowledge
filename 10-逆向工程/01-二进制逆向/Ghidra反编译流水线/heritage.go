package main

// heritage.go — Heritage 的后半段:phi 放置 / 重命名 / 校验 / 打印
// (与 pcode_ssa.go 同包,`go run .` 一并编译;拆文件只为守住单文件 ≤300 行)

import (
	"fmt"
	"sort"
)


// PlacePhis 在工作列表上迭代到不动点,返回插入的 phi 条数。
func (f *Func) PlacePhis() int {
	vars := map[string]Vn{}
	for _, n := range f.Order {
		for _, op := range f.Blocks[n].Ops {
			if op.Out != nil {
				vars[op.Out.Name] = *op.Out
			}
		}
	}
	var names []string
	for k := range vars {
		names = append(names, k)
	}
	sort.Strings(names)
	frontier := f.DF()
	liveIn, _ := f.Liveness()
	inserted := 0
	for _, name := range names {
		var work []string
		for _, n := range f.Order {
			for _, op := range f.Blocks[n].Ops {
				if op.Out != nil && op.Out.Name == name {
					work = append(work, n)
				}
			}
		}
		placed := map[string]bool{}
		for len(work) > 0 {
			d := work[len(work)-1]
			work = work[:len(work)-1]
			for _, y := range frontier[d] {
				if placed[y] || !liveIn[y][name] {
					continue
				}
				placed[y] = true
				b := f.Blocks[y]
				b.Phis = append(b.Phis, Phi{Out: vars[name], SrcName: vars[name],
					Ins: make([]Vn, len(b.Preds))})
				inserted++
				work = append(work, y)
			}
		}
	}
	return inserted
}

// Rename 以支配树 DFS + 每变量版本栈做标准重命名,产出 SSA 形式。
func (f *Func) Rename() map[string][]string {
	idom, _ := f.Idoms()
	children := map[string][]string{}
	for _, n := range f.Order {
		if d := idom[n]; n != d {
			children[d] = append(children[d], n)
		}
	}
	stacks := map[string][]Vn{}
	counters := map[string]int{}
	versions := map[string][]string{}
	fresh := func(v Vn) Vn {
		counters[v.Name]++
		nv := Vn{v.Space, fmt.Sprintf("%s#%d", v.Name, counters[v.Name])}
		stacks[v.Name] = append(stacks[v.Name], nv)
		versions[v.Name] = append(versions[v.Name], nv.Name)
		return nv
	}
	top := func(name string) Vn {
		if st := stacks[name]; len(st) > 0 {
			return st[len(st)-1]
		}
		return Vn{"register", name + "#0"}
	}
	var walk func(n string)
	walk = func(n string) {
		b := f.Blocks[n]
		var pushed []string
		for i := range b.Phis {
			b.Phis[i].Out = fresh(b.Phis[i].SrcName)
			pushed = append(pushed, b.Phis[i].SrcName.Name)
		}
		for i := range b.Ops {
			for j := range b.Ops[i].Ins {
				if !b.Ops[i].Ins[j].IsConst() {
					b.Ops[i].Ins[j] = top(baseName(b.Ops[i].Ins[j].Name))
				}
			}
			if b.Ops[i].Out != nil {
				if !b.Ops[i].Out.IsConst() {
					nv := fresh(Vn{b.Ops[i].Out.Space, baseName(b.Ops[i].Out.Name)})
					b.Ops[i].Out = &nv
					pushed = append(pushed, baseName(nv.Name))
				}
			}
		}
		for _, s := range b.Succ {
			sb := f.Blocks[s]
			k := 0
			for i, p := range sb.Preds {
				if p == n {
					k = i
				}
			}
			for i := range sb.Phis {
				sb.Phis[i].Ins[k] = top(sb.Phis[i].SrcName.Name)
			}
		}
		for _, c := range children[n] {
			walk(c)
		}
		for _, name := range pushed {
			stacks[name] = stacks[name][:len(stacks[name])-1]
		}
	}
	walk(f.Entry)
	return versions
}

func baseName(s string) string {
	for i := 0; i < len(s); i++ {
		if s[i] == '#' {
			return s[:i]
		}
	}
	return s
}

// Verify 校验唯一命名与「定义支配使用」;phi 入边按前驱判定。
func (f *Func) Verify() []string {
	defs := map[string]string{}
	var problems []string
	for _, n := range f.Order {
		b := f.Blocks[n]
		for _, ph := range b.Phis {
			if _, dup := defs[ph.Out.Name]; dup {
				problems = append(problems, "重复定义 "+ph.Out.Name)
			}
			defs[ph.Out.Name] = n
			if len(ph.Ins) != len(b.Preds) {
				problems = append(problems, "phi 入边数与前驱数不等 @"+n)
			}
		}
		for _, op := range b.Ops {
			if op.Out == nil || op.Out.IsConst() {
				continue
			}
			if _, dup := defs[op.Out.Name]; dup {
				problems = append(problems, "重复定义 "+op.Out.Name)
			}
			defs[op.Out.Name] = n
		}
	}
	idom, _ := f.Idoms()
	dominates := func(a, b string) bool {
		for x := b; ; x = idom[x] {
			if x == a {
				return true
			}
			if idom[x] == x {
				return false
			}
		}
	}
	for _, n := range f.Order {
		b := f.Blocks[n]
		for _, op := range b.Ops {
			for _, v := range op.Ins {
				if v.IsConst() {
					continue
				}
				if d, ok := defs[v.Name]; ok && !dominates(d, n) {
					problems = append(problems, v.Name+" 的使用点不被定义点支配 @"+n)
				}
			}
		}
		for _, ph := range b.Phis {
			for k, v := range ph.Ins {
				if v.IsConst() {
					continue
				}
				if d, ok := defs[v.Name]; ok && !dominates(d, b.Preds[k]) {
					problems = append(problems, fmt.Sprintf("phi @%s 第 %d 入边不被支配", n, k))
				}
			}
		}
	}
	return problems
}

func (f *Func) Dump(title string) {
	fmt.Println("## " + title)
	for _, n := range f.Order {
		b := f.Blocks[n]
		fmt.Printf("   %-4s (pred=%s succ=%s)\n", n, join(b.Preds), join(b.Succ))
		for _, ph := range b.Phis {
			ins := make([]string, 0, len(ph.Ins))
			for _, v := range ph.Ins {
				ins = append(ins, v.Name)
			}
			fmt.Printf("       %s = MULTIEQUAL %s\n", ph.Out.Name, join(ins))
		}
		for _, op := range b.Ops {
			fmt.Println("       " + op.String())
		}
	}
}
