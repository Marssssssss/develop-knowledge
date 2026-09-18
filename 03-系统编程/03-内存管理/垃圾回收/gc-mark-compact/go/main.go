// 标记-压缩式 GC —— 5 组实验(Go 版)。
//
// 算法在 compact.go,实验与断言在 demos.go。运行: go run .
package main

import (
	"fmt"
	"os"
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

func newObj(oid string, payloadWords int) *object {
	return &object{oid: oid, payloadWords: payloadWords, info: "INFO(" + oid + ")"}
}

// fixture 地址序 A | g1 | B | g2 | C | D;引用 A->B->C->D;根 = A。
func fixture() *heap {
	a := newObj("A", 1)
	g1 := newObj("g1", 2)
	b := newObj("B", 1)
	g2 := newObj("g2", 3)
	c := newObj("C", 1)
	d := newObj("D", 0)
	a.newSlot("toB", false).val = "B"
	b.newSlot("toC", false).val = "C"
	c.newSlot("toD", false).val = "D"
	h := &heap{cells: []*object{a, g1, b, g2, c, d}}
	h.root("r1", "A", false)
	return h
}

// ---------------------------------------------------------------------------
// 断言辅助
// ---------------------------------------------------------------------------

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

func sameSet(got map[string]bool, want ...string) bool {
	if len(got) != len(want) {
		return false
	}
	for _, k := range want {
		if !got[k] {
			return false
		}
	}
	return true
}

func increasing(v []int) bool {
	for i := 1; i < len(v); i++ {
		if v[i] <= v[i-1] {
			return false
		}
	}
	return true
}

func eqBools(a, b []bool) bool {
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

func oidsOf(cells []*object) []string {
	out := make([]string, 0, len(cells))
	for _, o := range cells {
		out = append(out, o.oid)
	}
	return out
}

func liveOids(h *heap, live map[string]bool) []string {
	out := []string{}
	for _, o := range h.cells {
		if live[o.oid] {
			out = append(out, o.oid)
		}
	}
	return out
}

func filterLive(order []string, live map[string]bool) []string {
	out := []string{}
	for _, oid := range order {
		if live[oid] {
			out = append(out, oid)
		}
	}
	return out
}

func tagsOf(o *object) []bool {
	out := make([]bool, 0, len(o.fields))
	for _, f := range o.fields {
		out = append(out, f.tagged)
	}
	return out
}

func indexOfStr(xs []string, s string) int {
	for i, x := range xs {
		if x == s {
			return i
		}
	}
	return -1
}

func containsStr(xs []string, s string) bool { return indexOfStr(xs, s) >= 0 }

func absInt(x int) int {
	if x < 0 {
		return -x
	}
	return x
}

// addrSet 把转发表反转为"合法新地址"集合。
func addrSet(fwd map[string]int) map[string]bool {
	out := map[string]bool{}
	for _, v := range fwd {
		out[addrStr(v)] = true
	}
	return out
}

// danglingSlots 找出仍持有旧 oid(即不落在任何合法新地址上)的槽。
func danglingSlots(h *heap, fwd map[string]int) []*slot {
	valid := addrSet(fwd)
	var out []*slot
	for _, s := range h.allSlots() {
		if v, ok := slotOid(s); ok && !valid[v] {
			out = append(out, s)
		}
	}
	return out
}

// contiguous 存活对象在新布局中首尾相接。
func contiguous(fwd map[string]int, cells []*object) bool {
	for i := 0; i+1 < len(cells); i++ {
		if fwd[cells[i].oid]+cells[i].sizeWords() != fwd[cells[i+1].oid] {
			return false
		}
	}
	return true
}

// holeRuns 数压缩前堆里"空洞"(死对象连续段)的个数。
func holeRuns(h *heap, live map[string]bool) int {
	runs, inHole := 0, false
	for _, o := range h.cells {
		if live[o.oid] {
			inHole = false
			continue
		}
		if !inHole {
			runs++
		}
		inHole = true
	}
	return runs
}

// compactedLayout 把压缩后的堆表示成"块序列",末位空串代表尾部那段空闲。
func compactedLayout(cells []*object, live map[string]bool, heapWords int) []string {
	out := []string{}
	acc := 0
	for _, o := range cells {
		if live[o.oid] {
			out = append(out, o.oid)
			acc += o.sizeWords()
		}
	}
	if acc < heapWords {
		out = append(out, "") // 空闲被合并成一整块,落在尾部
	}
	return out
}

func countBlock(layout []string, block string) int {
	n := 0
	for _, x := range layout {
		if x == block {
			n++
		}
	}
	return n
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
