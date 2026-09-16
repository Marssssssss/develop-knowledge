// pcode_ssa.go — P-code 的 SSA 构造(Heritage)与校验,同题 Go 实现
//
// 依据: Ghidra 官方反编译器文档「Main Work Flow」15 阶段中的
//   02 Generate Raw P-code / 03 Generate Basic Blocks and the CFG /
//   06 The Main Simplification Loop (a Generate SSA Form) / 08 Exit SSA Form。
// 官方原文要点: phi 放置基于**支配树 + 支配边界**的标准算法,重命名是标准算法,
// 且反编译器内部 SSA 是增量式的(需 1 次或多次额外遍历才能完全建成)。
//
// 与 arm64_decode.c 同样,本机无 Go 工具链,靠人工审查 + 括号配平程序化校验。
//
// 构建: go build ./...    运行: go run .
package main

import (
	"fmt"
)

// Vn 是一个 varnode: 常量空间的 Name 为十进制字面量,其余空间为变量名。
type Vn struct {
	Space string
	Name  string
}

func Const(v int) Vn    { return Vn{"const", fmt.Sprintf("%d", v)} }
func Rv(name string) Vn { return Vn{"register", name} }
func (v Vn) IsConst() bool { return v.Space == "const" }
func (v Vn) String() string { return v.Name }

type Op struct {
	Code string
	Out  *Vn
	Ins  []Vn
}

func (o Op) String() string {
	parts := make([]string, 0, len(o.Ins))
	for _, v := range o.Ins {
		parts = append(parts, v.Name)
	}
	if o.Out == nil {
		return o.Code + " " + join(parts)
	}
	return o.Out.Name + " = " + o.Code + " " + join(parts)
}

func join(xs []string) string {
	s := ""
	for i, x := range xs {
		if i > 0 {
			s += " "
		}
		s += x
	}
	return s
}

// Phi 的入边是「边上的使用」: 第 k 条入边在前驱 k -> 本块这条边上求值。
type Phi struct {
	Out     Vn
	SrcName Vn
	Ins     []Vn
}

type Block struct {
	Name  string
	Ops   []Op
	Succ  []string
	Preds []string
	Phis  []Phi
}

type Func struct {
	Blocks map[string]*Block
	Order  []string
	Entry  string
}

func NewFunc(entry string, blocks ...*Block) *Func {
	f := &Func{Blocks: map[string]*Block{}, Entry: entry}
	for _, b := range blocks {
		f.Blocks[b.Name] = b
		f.Order = append(f.Order, b.Name)
	}
	for _, b := range blocks {
		for _, s := range b.Succ {
			f.Blocks[s].Preds = append(f.Blocks[s].Preds, b.Name)
		}
	}
	return f
}

// RPO 逆后序: 支配树迭代算法的前提(入口先于被支配者)。
func (f *Func) RPO() []string {
	seen := map[string]bool{}
	var post []string
	var dfs func(n string)
	dfs = func(n string) {
		seen[n] = true
		for _, s := range f.Blocks[n].Succ {
			if !seen[s] {
				dfs(s)
			}
		}
		post = append(post, n)
	}
	dfs(f.Entry)
	for i, j := 0, len(post)-1; i < j; i, j = i+1, j-1 {
		post[i], post[j] = post[j], post[i]
	}
	return post
}

// Idoms 迭代式立即支配者(Cooper-Harvey-Kennedy),用逆后序下标比较支配先后。
func (f *Func) Idoms() (map[string]string, map[string]int) {
	order := f.RPO()
	idx := map[string]int{}
	for i, n := range order {
		idx[n] = i
	}
	idom := map[string]string{f.Entry: f.Entry}
	for changed := true; changed; {
		changed = false
		for _, n := range order[1:] {
			var newID string
			for _, p := range f.Blocks[n].Preds {
				if _, ok := idx[p]; !ok {
					continue
				}
				if _, ok := idom[p]; !ok {
					continue
				}
				if newID == "" {
					newID = p
				} else {
					newID = intersect(newID, p, idom, idx)
				}
			}
			if newID != "" && idom[n] != newID {
				idom[n] = newID
				changed = true
			}
		}
	}
	return idom, idx
}

func intersect(a, b string, idom map[string]string, idx map[string]int) string {
	for a != b {
		for idx[a] > idx[b] {
			a = idom[a]
		}
		for idx[b] > idx[a] {
			b = idom[b]
		}
	}
	return a
}

// DF 支配边界: DF[x] = {y | x 支配 y 的某前驱,但 x 不严格支配 y}。
func (f *Func) DF() map[string][]string {
	idom, _ := f.Idoms()
	frontier := map[string][]string{}
	for _, y := range f.RPO() {
		var preds []string
		for _, p := range f.Blocks[y].Preds {
			if _, ok := idom[p]; ok {
				preds = append(preds, p)
			}
		}
		if len(preds) < 2 {
			continue
		}
		for _, p := range preds {
			for runner := p; runner != idom[y]; runner = idom[runner] {
				frontier[runner] = append(frontier[runner], y)
				if _, ok := idom[runner]; !ok {
					break
				}
			}
		}
	}
	return frontier
}

// Liveness 活跃性: 只有变量在汇合点入口活跃时才插 phi(最小 SSA 的要求)。
func (f *Func) Liveness() (map[string]map[string]bool, map[string]map[string]bool) {
	use := map[string]map[string]bool{}
	killed := map[string]map[string]bool{}
	for _, n := range f.Order {
		u, d := map[string]bool{}, map[string]bool{}
		for _, op := range f.Blocks[n].Ops {
			for _, v := range op.Ins {
				if !v.IsConst() && !d[v.Name] {
					u[v.Name] = true
				}
			}
			if op.Out != nil {
				d[op.Out.Name] = true
			}
		}
		use[n], killed[n] = u, d
	}
	liveIn := map[string]map[string]bool{}
	liveOut := map[string]map[string]bool{}
	for _, n := range f.Order {
		liveIn[n] = map[string]bool{}
		liveOut[n] = map[string]bool{}
		for k := range use[n] {
			liveIn[n][k] = true
		}
	}
	order := f.RPO()
	for changed := true; changed; {
		changed = false
		for i := len(order) - 1; i >= 0; i-- {
			n := order[i]
			out := map[string]bool{}
			for _, s := range f.Blocks[n].Succ {
				for k := range liveIn[s] {
					out[k] = true
				}
			}
			in := map[string]bool{}
			for k := range use[n] {
				in[k] = true
			}
			for k := range out {
				if !killed[n][k] {
					in[k] = true
				}
			}
			if len(out) != len(liveOut[n]) || len(in) != len(liveIn[n]) {
				changed = true
			}
			liveOut[n], liveIn[n] = out, in
		}
	}
	return liveIn, liveOut
}
