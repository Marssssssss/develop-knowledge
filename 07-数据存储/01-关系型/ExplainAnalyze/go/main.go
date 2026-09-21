package main

import "fmt"

func main() {
	fmt.Println("EXPLAIN ANALYZE 读数模型(Go 转写版)")

	fmt.Println("\n== 1. Nested Loop 示例(文档 14.1.2 原数字) ==")
	top := &Node{NodeType: "Nested Loop", EstStartup: 4.65, EstTotal: 118.50, EstRows: 10, EstWidth: 488,
		Extra: [][2]string{{"Buffers", "shared hit=36 read=6"}}}
	heap := &Node{NodeType: "Bitmap Heap Scan on tenk1 t1", EstStartup: 4.36, EstTotal: 39.38, EstRows: 10, EstWidth: 244}
	bidx := &Node{NodeType: "Bitmap Index Scan on tenk1_unique1", EstStartup: 0, EstTotal: 4.36, EstRows: 10}
	heap.Children = []*Node{bidx}
	idx := &Node{NodeType: "Index Scan using tenk2_unique2 on tenk2 t2", EstStartup: 0.29, EstTotal: 7.90, EstRows: 1, EstWidth: 244}
	top.Children = []*Node{heap, idx}

	top.Run(10, 10, 0.017, 0.051, 0)
	heap.Run(10, 10, 0.009, 0.017, 0)
	bidx.Run(10, 10, 0.004, 0.004, 0)
	for i := 0; i < 10; i++ {
		idx.Run(1, 1, 0.003, 0.003, 0)
	}
	fmt.Println(top.Render(0, true))
	_, last := idx.TotalTime()
	fmt.Printf("  内层 actual rows=%.2f loops=%d, 总行数=%.0f, 总耗时=%.3f ms\n",
		idx.ActualRows(), idx.NumLoops, idx.TotalRows(), last)

	fmt.Println("\n== 2. Filter 与估算误差 ==")
	s := &Node{NodeType: "Seq Scan on tenk1", EstTotal: 470, EstRows: 7000, EstWidth: 244,
		Extra: [][2]string{{"Filter", "(ten < 7)"}}}
	s.Run(7000, 10000, 0.030, 1.995, 3000)
	fmt.Println(s.Render(0, true))
	fmt.Printf("  seq_scan_cost(345, 10000) = %.2f, + 10000*0.0025 = %.2f\n",
		SeqScanCost(345, 10000), SeqScanCost(345, 10000)+25)

	fmt.Println("\n== 3. 读数陷阱 ==")
	band := &Node{NodeType: "BitmapAnd", EstRows: 40}
	band.Run(40, 40, 0.001, 0.002, 0)
	fmt.Println("  BitmapAnd actual rows =", band.ActualRows(), "(恒 0)")
	lim := &Node{NodeType: "Limit", EstStartup: 0.29, EstTotal: 14.33, EstRows: 2, EstWidth: 244}
	child := &Node{NodeType: "Index Scan", EstStartup: 0.29, EstTotal: 70.50, EstRows: 10, EstWidth: 244}
	lim.Children = []*Node{child}
	lim.Run(2, 2, 0.051, 0.071, 0)
	child.Run(2, 287, 0.051, 0.070, 287)
	fmt.Println(child.Render(1, true))
	mj := &Node{NodeType: "Merge Join", EstRows: 10, EstWidth: 16}
	mi := &Node{NodeType: "Seq Scan(内层)", EstRows: 100, EstWidth: 8}
	mj.Children = []*Node{mi}
	mj.Run(10, 10, 0.1, 0.9, 0)
	for i := 0; i < 3; i++ {
		mi.Run(100, 100, 0.01, 0.2, 0)
	}
	fmt.Printf("  外层 3 个重复键 -> 内层报 %.0f 行(关系真实 100 行)\n", mi.TotalRows())
}
