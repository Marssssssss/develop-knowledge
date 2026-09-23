package main

import "fmt"

func lcgPoints(n, dim, seed int) [][]float64 {
	x := seed & 0x7FFFFFFF
	pts := make([][]float64, n)
	for i := 0; i < n; i++ {
		v := make([]float64, dim)
		for d := 0; d < dim; d++ {
			x = (1103515245*x + 12345) & 0x7FFFFFFF
			v[d] = float64(x%100000) / 100000.0
		}
		pts[i] = v
	}
	return pts
}

func main() {
	const dim = 8
	data := lcgPoints(600, dim, 1234)
	qs := lcgPoints(20, dim, 99)

	fmt.Println("== code_size = ceil(nbits*M/8) ==")
	for _, c := range [][3]int{{8, 2, 8}, {12, 3, 6}, {8, 4, 8}, {8, 8, 4}} {
		p := NewPQ(c[0], c[1], c[2])
		fmt.Printf("  d=%2d M=%d nbits=%2d -> dsub=%d ksub=%4d code_size=%d 压缩比 %.1fx\n",
			c[0], c[1], c[2], p.Dsub, p.Ksub, p.CodeSize, float64(c[0]*4)/float64(p.CodeSize))
	}

	fmt.Println("\n== ADC 恒等式：对一个查询结果 Table 求和 == ||x - decode(code)||^2 ==")
	p := NewPQ(dim, 4, 8)
	p.Train(data, 8, 7)
	x := data[0]
	code := p.ComputeCode(x)
	tbl := p.ComputeDistanceTable(x)
	adt := p.AdcL2(tbl, code)
	direct := L2Sqr(x, p.Decode(code))
	fmt.Printf("  ADC=%.12f  直接算=%.12f  差=%.3e\n", adt, direct, adt-direct)
	ipt := p.ComputeInnerProdTable(x)
	asym := p.AdcIPAsymmetric(x, ipt, code)
	fmt.Printf("  内积 ADC 经 ||x||^2+||y||^2-2<x,y> 还原=%.12f  差=%.3e\n",
		asym, asym-direct)

	fmt.Println("\n== 量化误差 ==")
	for _, c := range [][2]int{{2, 8}, {4, 4}, {4, 8}, {8, 4}} {
		q := NewPQ(dim, c[0], c[1])
		q.Train(data, 8, 7)
		fmt.Printf("  M=%d nbits=%2d code=%dB  平均重构误差 %.6f\n",
			c[0], c[1], q.CodeSize, q.ReconstructionError(data))
	}

	fmt.Println("\n== nprobe 与召回（nlist=16） ==")
	ix := NewIndexIVFPQ(dim, 16, 4, 8, "L2", true)
	ix.Train(data, 8, 5)
	ix.Add(data)
	for _, np := range []int{1, 2, 4, 8, 16, 64} {
		fmt.Printf("  nprobe=%3d (有效 %2d)  recall@5 = %.3f\n",
			np, ix.EffectiveNprobe(np), ix.RecallAtK(qs, 5, np))
	}

	fmt.Println("\n== 预计算表决策（M*ksub*nlist*4 vs 2 GiB） ==")
	for _, nlist := range []int{16, 100, 1 << 16, 1 << 21} {
		i2 := NewIndexIVFPQ(dim, nlist, 4, 8, "L2", true)
		t := i2.PrecomputeTable()
		fmt.Printf("  nlist=%8d -> use_precomputed_table=%d  表 %d 字节\n",
			nlist, t, i2.TableSize)
	}
}
