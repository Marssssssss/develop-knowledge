package main

import "fmt"

func main() {
	fmt.Println("并行查询 worker 数(Go 转写版, 公式来自 allpaths.c)")

	fmt.Println("\n== 1. 表越大 worker 越多 ==")
	fmt.Println("  页数        log3    默认上限2")
	for _, pages := range []int{512, 1023, 1024, 3071, 3072, 9216, 100000, 1000000} {
		raw := Log3Workers(pages, minTableScanPages)
		real, _ := ComputeParallelWorker(Params{HeapPages: pages, MaxWorkers: defaultMaxGather,
			RelParallel: -1, IsBaseRel: true})
		fmt.Printf("  %-10d %-7d %d\n", pages, raw, real)
	}

	fmt.Println("\n== 2. 旋钮 ==")
	show("1023 页 BASEREL", Params{HeapPages: 1023, MaxWorkers: 2, RelParallel: -1, IsBaseRel: true})
	show("1023 页 继承子表", Params{HeapPages: 1023, MaxWorkers: 2, RelParallel: -1, IsBaseRel: false})
	show("1023 页 reloption=4", Params{HeapPages: 1023, MaxWorkers: 8, RelParallel: 4, IsBaseRel: true})
	show("100000 页 上限 8", Params{HeapPages: 100000, MaxWorkers: 8, RelParallel: -1, IsBaseRel: true})
	show("100000 页 上限 0", Params{HeapPages: 100000, MaxWorkers: 0, RelParallel: -1, IsBaseRel: true})

	fmt.Println("\n== 3. 并行计划代价 ==")
	for _, w := range []int{0, 1, 2, 4} {
		fmt.Printf("  workers=%d -> %.2f\n", w, ParallelPlanCost(216018.33, 1.0, w, true))
	}

	fmt.Println("\n== 4. 并行安全性 ==")
	cases := []struct {
		label string
		s     Safety
	}{
		{"普通只读查询", Safety{MaxPerGather: 2}},
		{"UPDATE/INSERT..SELECT", Safety{MaxPerGather: 2, Writes: true}},
		{"DECLARE CURSOR", Safety{MaxPerGather: 2, Suspendable: true}},
		{"自定义函数(默认 UNSAFE)", Safety{MaxPerGather: 2, UnsafeFunction: true}},
		{"并行内嵌套查询", Safety{MaxPerGather: 2, NestedInParallel: true}},
	}
	for _, c := range cases {
		okp, why := CanParallelize(c.s)
		fmt.Printf("  %-24s 可并行=%-5v %v\n", c.label, okp, why)
	}
}

func show(label string, p Params) {
	w, why := ComputeParallelWorker(p)
	fmt.Printf("  %-20s workers=%d (%s)\n", label, w, why)
}
