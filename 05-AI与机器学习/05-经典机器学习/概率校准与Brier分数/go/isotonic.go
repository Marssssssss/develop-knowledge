// 保序回归(isotonic regression,PAVA)—— 从 calibration.go 拆出,逻辑零变化。
//
// 权威依据:scikit-learn《1.16. Probability calibration》method="isotonic":
// 最小化 Σ(y_i − f̂_i)²,约束 f_i ≥ f_j ⇒ f̂_i ≥ f̂_j,输出**阶跃非降函数**。
package main

// IsotonicRegression 保序回归(PAVA),输出阶跃函数。
type IsotonicRegression struct {
	bx     []float64
	blocks []float64
}

func (iso *IsotonicRegression) Fit(x, y []float64) {
	idx := make([]int, len(x))
	for i := range idx {
		idx[i] = i
	}
	for i := 1; i < len(idx); i++ {
		for j := i; j > 0 && x[idx[j]] < x[idx[j-1]]; j-- {
			idx[j], idx[j-1] = idx[j-1], idx[j]
		}
	}
	vals := make([]float64, len(idx))
	for i, k := range idx {
		vals[i] = y[k]
	}
	bx := make([]float64, len(idx))
	for i, k := range idx {
		bx[i] = x[k]
	}
	blocks := append([]float64(nil), vals...)
	sizes := make([]float64, len(vals))
	for i := range sizes {
		sizes[i] = 1
	}
	i := 0
	for i+1 < len(blocks) {
		if blocks[i] <= blocks[i+1]+1e-15 {
			i++
			continue
		}
		n1, n2 := sizes[i], sizes[i+1]
		merged := (blocks[i]*n1 + blocks[i+1]*n2) / (n1 + n2)
		blocks = append(blocks[:i], append([]float64{merged}, blocks[i+2:]...)...)
		sizes = append(sizes[:i], append([]float64{n1 + n2}, sizes[i+2:]...)...)
		bx = append(bx[:i], append([]float64{bx[i]}, bx[i+2:]...)...)
		for i > 0 && blocks[i-1] > blocks[i]+1e-15 {
			n1, n2 = sizes[i-1], sizes[i]
			m := (blocks[i-1]*n1 + blocks[i]*n2) / (n1 + n2)
			blocks = append(blocks[:i-1], append([]float64{m}, blocks[i+1:]...)...)
			sizes = append(sizes[:i-1], append([]float64{n1 + n2}, sizes[i+1:]...)...)
			bx = append(bx[:i-1], append([]float64{bx[i-1]}, bx[i+1:]...)...)
			i--
		}
	}
	iso.bx, iso.blocks = bx, blocks
}

func (iso *IsotonicRegression) Predict(x []float64) []float64 {
	out := make([]float64, len(x))
	for i, v := range x {
		if v <= iso.bx[0] {
			out[i] = iso.blocks[0]
			continue
		}
		if v >= iso.bx[len(iso.bx)-1] {
			out[i] = iso.blocks[len(iso.blocks)-1]
			continue
		}
		k := 0
		for k+1 < len(iso.bx) && iso.bx[k+1] <= v {
			k++
		}
		out[i] = iso.blocks[k]
	}
	return out
}

