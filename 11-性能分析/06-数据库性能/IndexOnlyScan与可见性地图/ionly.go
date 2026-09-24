// ionly.go — 与 ionly.py 同语义的 Go 复刻(静态审查用)。
package main

import "fmt"

type Heap struct{ vm []bool }

func NewHeap(pages int) *Heap { return &Heap{vm: make([]bool, pages)} }

// Vacuum 置位,Modify 清零。
func (h *Heap) Vacuum(ids ...int) {
	for _, p := range ids {
		h.vm[p] = true
	}
}

func (h *Heap) Modify(p int) { h.vm[p] = false }

func (h *Heap) AllVisibleFraction() float64 {
	n := 0
	for _, v := range h.vm {
		if v {
			n++
		}
	}
	return float64(n) / float64(len(h.vm))
}

// ExecuteIOS:VM 位未置的页必须回堆。
func ExecuteIOS(entries [][2]int, heap *Heap) int {
	fetches := 0
	for _, e := range entries {
		if !heap.vm[e[0]] {
			fetches++
		}
	}
	return fetches
}

// IndexOnlyEligible:类型支持 + 查询列 ⊆ 索引列。
func IndexOnlyEligible(indexType string, indexCols, queryCols []string) (bool, string) {
	supported := map[string]bool{"btree": true, "gist": false, "spgist": false,
		"gin": false, "brin": false}
	if !supported[indexType] {
		return false, "索引类型不支持(" + indexType + ")"
	}
	set := map[string]bool{}
	for _, c := range indexCols {
		set[c] = true
	}
	for _, c := range queryCols {
		if !set[c] {
			return false, "查询引用了索引外列"
		}
	}
	return true, "ok"
}

func main() {
	heap := NewHeap(4)
	entries := [][2]int{{0, 0}, {1, 0}, {2, 0}, {3, 0}}
	fmt.Println("before vacuum fetches:", ExecuteIOS(entries, heap)) // 4
	heap.Vacuum(0, 1, 2)
	fmt.Println("after vacuum fetches:", ExecuteIOS(entries, heap)) // 1
	heap.Modify(1)
	fmt.Println("after modify fetches:", ExecuteIOS(entries, heap)) // 2

	ok, why := IndexOnlyEligible("btree", []string{"x", "y"}, []string{"x", "z"})
	fmt.Println("btree(x,y) for x,z:", ok, why)
	ok2, _ := IndexOnlyEligible("gin", []string{"x"}, []string{"x"})
	fmt.Println("gin:", ok2)
}
