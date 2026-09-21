// Package main —— BinPRE §3.4 原子语义检测器库（Go 实现）。
//
// 与 python/main.py 同题：每个字段只看 I(f)（访问它的指令算子序列）与 V(f)（取值），
// 按论文 Table 2 的「当且仅当」规则判出 5 种语义类型 + 6 种语义功能。
//
// 语言差异：Python 用 frozenset + 生成器推导，这里用 map[string]bool 与显式循环；
// 「集合相等」的比较改成逐个键比对（Equal 函数）。
//
// 运行：go run .
package main

import (
	"fmt"
	"sort"
)

// mov 系列：不算 functional operation。
var movOps = map[string]bool{"mov": true, "movzx": true, "movsx": true, "lea": true, "push": true, "pop": true}

// 比较类：是 functional operation，但属于判据本身（Static 的「无额外 functional
// 操作」指除比较之外没有别的）。
var cmpOps = map[string]bool{"cmp": true, "test": true}

// Trace 一个字段的执行轨迹摘要（I(f) 与 V(f)）。
type Trace struct {
	Name                                                                     string
	Ops                                                                      []string
	CmpConsts                                                                []int
	CmpTrue                                                                  bool
	CmpMultiConsecutive, CmpLoopOutput                                       bool
	CmpSwitchConsts                                                          []int
	Functional, NonCmpFunctional                                             bool
	ArithOrBit                                                               bool
	InLoopSameOps, BytesAllSameStruct, ConsecutiveCmpSameConst               bool
	LoopTerminator, DelimitsNeighbors, JumpOnTrue                            bool
	LibAPILength, PtrIncCounterDec, FilenameLike                             bool
}

func isFunctionalOp(op string) bool { return !movOps[op] }
func isNonCmpFunctional(op string) bool {
	return !movOps[op] && !cmpOps[op]
}

// NewTrace 构造轨迹，并按 ops 自动推出 Functional 与 NonCmpFunctional。
func NewTrace(name string, ops []string) *Trace {
	t := &Trace{Name: name, Ops: ops}
	for _, o := range ops {
		if isFunctionalOp(o) {
			t.Functional = true
		}
		if isNonCmpFunctional(o) {
			t.NonCmpFunctional = true
		}
	}
	return t
}

// DetectType 返回命中的语义类型（论文 Table 2 上半部分）。
func DetectType(t *Trace) map[string]bool {
	h := map[string]bool{}
	if t.CmpTrue && len(t.CmpConsts) > 0 && !t.NonCmpFunctional {
		h["Static"] = true
	}
	if t.ArithOrBit || t.CmpMultiConsecutive {
		h["Integer"] = true
	}
	if len(uniq(t.CmpSwitchConsts)) >= 2 {
		h["Group"] = true
	}
	if t.InLoopSameOps && t.BytesAllSameStruct {
		h["Bytes"] = true
	}
	if t.InLoopSameOps && t.ConsecutiveCmpSameConst {
		h["String"] = true
	}
	return h
}

// DetectFunction 返回命中的语义功能（论文 Table 2 下半部分）。
func DetectFunction(t *Trace) map[string]bool {
	h := map[string]bool{}
	if t.CmpTrue && t.JumpOnTrue {
		h["Command"] = true
	}
	if t.LoopTerminator || t.LibAPILength || t.PtrIncCounterDec {
		h["Length"] = true
	}
	if t.LoopTerminator && t.DelimitsNeighbors {
		h["Delim"] = true
	}
	if t.CmpLoopOutput {
		h["Checksum"] = true
	}
	if t.FilenameLike {
		h["Filename"] = true
	}
	if !t.Functional {
		h["Aligned"] = true
	}
	return h
}

func uniq(xs []int) map[int]bool {
	m := map[int]bool{}
	for _, x := range xs {
		m[x] = true
	}
	return m
}

func keys(m map[string]bool) []string {
	out := []string{}
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func main() {
	// 协议版本字段：与固定值比较且命中，无额外 functional 操作
	ver := NewTrace("ver", []string{"cmp"})
	ver.CmpConsts = []int{0x03}
	ver.CmpTrue = true

	// 对齐填充：只有 mov
	pad := NewTrace("pad", []string{"mov"})

	// 命令码：命中后立刻跳转
	cmd := NewTrace("cmd", []string{"cmp"})
	cmd.CmpConsts = []int{0x03}
	cmd.CmpTrue = true
	cmd.JumpOnTrue = true

	// 论文 Example-3 的 f21,22：shl/or 是位运算 -> Integer；
	// 与「对连续字节迭代的循环输出」比较 -> Checksum
	sum := NewTrace("f21,22", []string{"movzx", "shl", "or", "cmp"})
	sum.ArithOrBit = true
	sum.CmpLoopOutput = true

	for _, t := range []*Trace{ver, pad, cmd, sum} {
		fmt.Printf("%-8s type=%-12v function=%v\n",
			t.Name, keys(DetectType(t)), keys(DetectFunction(t)))
	}
}
