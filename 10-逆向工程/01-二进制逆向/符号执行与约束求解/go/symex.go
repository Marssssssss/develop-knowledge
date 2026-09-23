// Package main 复刻 claripy 的位向量语义、朴素约束求解与 angr 的 stash 状态机。
//
// 原文实读：
//
//	claripy/claripy/ast/bv.py       位序、chop、get_byte、concat、扩展、_from_int
//	angr/angr/sim_state.py          SimState 的默认插件清单
//	angr/angr/sim_manager.py        _integral_stashes、ALL/DROP、explore 的 num_find
package main

import "sort"

// EnumBits 是暴力枚举的完备位宽上限。
const EnumBits = 8

// BV 是位向量 AST。
type BV struct {
	Op    string
	Args  []interface{}
	Size  int
	Name  string
	Value int
}

// BVS 建一个符号。
func BVS(name string, size int) *BV { return &BV{Op: "BVS", Size: size, Name: name} }

// BVV 建一个常量。
func BVV(value, size int) *BV {
	m := (1 << size) - 1
	return &BV{Op: "BVV", Size: size, Value: value & m}
}

// Extract 取 [hi:lo] 位（hi 是更靠左的位）。
func Extract(hi, lo int, b *BV) *BV {
	return &BV{Op: "Extract", Args: []interface{}{hi, lo, b}, Size: hi - lo + 1}
}

// Index 是 a[i] 与 a[hi:lo]：a[31] 是最左（最高）位。
func Index(b *BV, hi, lo int) *BV { return Extract(hi, lo, b) }

// Concat 拼接，第一个参数在最高位。
func Concat(args ...*BV) *BV {
	n := 0
	for _, a := range args {
		n += a.Size
	}
	xs := make([]interface{}, len(args))
	for i, a := range args {
		xs[i] = a
	}
	return &BV{Op: "Concat", Args: xs, Size: n}
}

// ZeroExt 零扩展。
func ZeroExt(n int, b *BV) *BV { return &BV{Op: "ZeroExt", Args: []interface{}{n, b}, Size: b.Size + n} }

// SignExt 符号扩展。
func SignExt(n int, b *BV) *BV { return &BV{Op: "SignExt", Args: []interface{}{n, b}, Size: b.Size + n} }

// Add 按位宽回绕相加（整数被强制成左操作数的位宽）。
func Add(a *BV, v int) *BV {
	return &BV{Op: "Add", Args: []interface{}{a, v}, Size: a.Size}
}

// Gt / Lt / Eq 生成布尔约束。
func Gt(a *BV, v int) *BV { return &BV{Op: "Gt", Args: []interface{}{a, v}, Size: 1} }

// Lt 小于。
func Lt(a *BV, v int) *BV { return &BV{Op: "Lt", Args: []interface{}{a, v}, Size: 1} }

// Eq 相等。
func Eq(a *BV, v int) *BV { return &BV{Op: "Eq", Args: []interface{}{a, v}, Size: 1} }

// Symbols 收集表达式里的符号名。
func Symbols(b *BV) []string {
	if b.Op == "BVS" {
		return []string{b.Name}
	}
	out := []string{}
	for _, a := range b.Args {
		if sub, ok := a.(*BV); ok {
			out = append(out, Symbols(sub)...)
		}
	}
	seen := map[string]bool{}
	res := []string{}
	for _, s := range out {
		if !seen[s] {
			seen[s] = true
			res = append(res, s)
		}
	}
	sort.Strings(res)
	return res
}

// Eval 在赋值下求值。
func Eval(b *BV, env map[string]int) int {
	switch b.Op {
	case "BVS":
		return env[b.Name] & ((1 << b.Size) - 1)
	case "BVV":
		return b.Value
	case "Extract":
		lo := b.Args[1].(int)
		sub := b.Args[2].(*BV)
		return (Eval(sub, env) >> lo) & ((1 << b.Size) - 1)
	case "Concat":
		acc := 0
		for _, a := range b.Args {
			s := a.(*BV)
			acc = acc<<s.Size | Eval(s, env)
		}
		return acc
	case "ZeroExt":
		return Eval(b.Args[1].(*BV), env)
	case "SignExt":
		n := b.Args[0].(int)
		sub := b.Args[1].(*BV)
		v := Eval(sub, env)
		if v>>(sub.Size-1)&1 == 1 {
			return v | ((1<<n - 1) << sub.Size)
		}
		return v
	case "Add":
		sub := b.Args[0].(*BV)
		v := b.Args[1].(int)
		return (Eval(sub, env) + v) & ((1 << b.Size) - 1)
	case "Gt":
		sub := b.Args[0].(*BV)
		v := b.Args[1].(int)
		if Eval(sub, env) > v {
			return 1
		}
		return 0
	case "Lt":
		sub := b.Args[0].(*BV)
		v := b.Args[1].(int)
		if Eval(sub, env) < v {
			return 1
		}
		return 0
	case "Eq":
		sub := b.Args[0].(*BV)
		v := b.Args[1].(int)
		if Eval(sub, env) == v {
			return 1
		}
		return 0
	}
	return 0
}

// Chop 切分；返回的第一个元素是最左（最高）的那一段。
func Chop(b *BV, bits int) []*BV {
	if b.Size%bits != 0 {
		return nil
	}
	if b.Size == bits {
		return []*BV{b}
	}
	parts := []*BV{}
	for n := 0; n < b.Size/bits; n++ {
		parts = append(parts, Index(b, (n+1)*bits-1, n*bits))
	}
	// 源码是 reversed(...)：先取最低位段，再反转成「最高段在前」
	for i, j := 0, len(parts)-1; i < j; i, j = i+1, j-1 {
		parts[i], parts[j] = parts[j], parts[i]
	}
	return parts
}

// GetByte 是大端字节序号，0 是最高字节。
func GetByte(b *BV, idx int) *BV {
	pos := (b.Size+7)/8 - 1 - idx
	if pos < 0 {
		return nil
	}
	hi := pos*8 + 7
	if hi > b.Size-1 {
		hi = b.Size - 1
	}
	return Index(b, hi, pos*8)
}

// Solve 暴力枚举满足所有约束的赋值。
func Solve(constraints []*BV, syms []string, limit int) []map[string]int {
	out := []map[string]int{}
	env := map[string]int{}
	var rec func(i int)
	rec = func(i int) {
		if limit > 0 && len(out) >= limit {
			return
		}
		if i == len(syms) {
			for _, c := range constraints {
				if Eval(c, env) == 0 {
					return
				}
			}
			cp := map[string]int{}
			for k, v := range env {
				cp[k] = v
			}
			out = append(out, cp)
			return
		}
		for v := 0; v < 1<<EnumBits; v++ {
			env[syms[i]] = v
			rec(i + 1)
		}
		delete(env, syms[i])
	}
	rec(0)
	return out
}
