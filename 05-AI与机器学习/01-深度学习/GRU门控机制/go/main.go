package main

import "fmt"

func main() {
	H, X := 4, 3
	x := []float64{1.0, 0.5, -2.0}
	h := []float64{0.3, -0.7, 1.2, 0.0}

	fmt.Println("== 参数规模约定 ==")
	fmt.Printf("  GRU gate_size = 3H = %d（LSTM 是 %d）\n", GateSize("gru", H), GateSize("lstm", H))
	fmt.Printf("  stdv = 1/sqrt(H) = %.6f\n", Stdv(H))

	fmt.Println("\n== 更新门 z 的三种极端 ==")
	for _, c := range []struct {
		zb  float64
		tag string
	}{{30.0, "z→1"}, {-30.0, "z→0"}, {0.0, "z=0.5"}} {
		p := Params{H: H, WIH: make([][]float64, 3*H), WHH: make([][]float64, 3*H),
			BIH: make([]float64, 3*H), BHH: make([]float64, 3*H)}
		for i := 0; i < 3*H; i++ {
			p.WIH[i] = make([]float64, X)
			p.WHH[i] = make([]float64, H)
		}
		for i := H; i < 2*H; i++ {
			p.BIH[i] = c.zb
			p.BHH[i] = c.zb
		}
		for i := 0; i < H; i++ {
			p.BIH[2*H+i] = 0.7
		}
		hp, _, z, n := GruCell(x, h, p, false)
		fmt.Printf("  %-6s z=%.6f n=%.6f h'=%v\n", c.tag, z[0], n[0], hp)
	}

	fmt.Println("\n== 两种 n_t 写法 ==")
	p := Params{H: H, WIH: make([][]float64, 3*H), WHH: make([][]float64, 3*H),
		BIH: make([]float64, 3*H), BHH: make([]float64, 3*H)}
	for i := 0; i < 3*H; i++ {
		p.WIH[i] = make([]float64, X)
		p.WHH[i] = make([]float64, H)
	}
	p.WHH[2*H] = []float64{0, 1, 0, 0}
	p.BHH[2*H] = 1.0
	_, r, _, nt := GruCell(x, h, p, false)
	_, _, _, np := GruCell(x, h, p, true)
	fmt.Printf("  r = %.6f\n  PyTorch n_0 = %.9f\n  原论文  n_0 = %.9f\n", r[0], nt[0], np[0])

	fmt.Println("\n== 长程梯度 ==")
	seq := [][][]float64{{{0.4}}, {{-0.3}}, {{1.1}}, {{0.2}}, {{-0.8}}}
	for _, zb := range []float64{0.6, 1.2, 2.5, 20.0} {
		q := Params{H: 1, WIH: [][]float64{{0}, {0}, {0}}, WHH: [][]float64{{0}, {0}, {0}},
			BIH: []float64{-40, zb, 0.9}, BHH: []float64{-40, zb, 0}}
		z := Sigmoid(2 * zb)
		_, hA := GruLayer(seq, [][]float64{{0.5}}, q, nil, false)
		eps := 1e-6
		_, hB := GruLayer(seq, [][]float64{{0.5 + eps}}, q, nil, false)
		fmt.Printf("  z=%.6f → 数值导数 %.9f，z^5 = %.9f\n", z, (hB[0][0]-hA[0][0])/eps, mathPow(z, 5))
	}
}

func mathPow(a float64, n int) float64 {
	out := 1.0
	for i := 0; i < n; i++ {
		out *= a
	}
	return out
}
