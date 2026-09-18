// 分代式 GC —— 5 组实验(Go 版)。
//
// 模型在 gen_model.go 与 cpy_gc.go;实验与断言在 demos.go / demos2.go。运行: go run .
package main

import (
	"fmt"
	"os"
	"sort"
)

var (
	fails []string
	total int
)

func check(label string, cond bool, detail ...interface{}) {
	total++
	if cond {
		fmt.Println("  [ok]", label)
		return
	}
	fails = append(fails, label)
	fmt.Println("  [FAIL]", label, detail)
}

// sortedKeys 保证遍历顺序稳定(map 迭代顺序随机,断言与输出都需要确定性)。
func sortedKeys(m map[string]*obj) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func removeObj(xs []*obj, o *obj) []*obj {
	var out []*obj
	for _, x := range xs {
		if x != o {
			out = append(out, x)
		}
	}
	return out
}

// forceOld 把对象直接放进老年代(模拟它已经活过多轮 minor)。
func forceOld(h *genHeap, o *obj) *obj {
	h.eden = removeObj(h.eden, o)
	for i := 0; i < 2; i++ {
		h.surv[i] = removeObj(h.surv[i], o)
	}
	o.gen = 1
	o.age = h.tenuring
	h.old = append(h.old, o)
	return o
}

func oidsOf(cells []*obj) []string {
	out := make([]string, 0, len(cells))
	for _, o := range cells {
		out = append(out, o.oid)
	}
	return out
}

func eqStrs(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func eqInts(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func describeDangling(ds []danglingRef) string {
	out := "["
	for i, d := range ds {
		if i > 0 {
			out += " "
		}
		out += d.owner + "." + d.field + "->" + d.target
	}
	return out + "]"
}

func describeObjs(cells []*obj) string {
	out := "["
	for i, o := range cells {
		if i > 0 {
			out += " "
		}
		out += o.oid
	}
	return out + "]"
}

func main() {
	for _, fn := range []func(){demo1, demo2, demo3, demo4, demo5} {
		fn()
		fmt.Println()
	}
	fmt.Printf("断言总数 %d,失败 %d\n", total, len(fails))
	if len(fails) > 0 {
		for _, f := range fails {
			fmt.Println("  FAILED:", f)
		}
		os.Exit(1)
	}
	fmt.Println("全部通过")
}
