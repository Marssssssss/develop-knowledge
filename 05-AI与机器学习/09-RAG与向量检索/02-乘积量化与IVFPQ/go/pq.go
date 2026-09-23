package main

// 乘积量化 —— 按 faiss 官方 faiss/impl/ProductQuantizer.{h,cpp} 转写。
//
// 关键事实：
//   - dsub = d / M（要求 d % M == 0），ksub = 1 << nbits，code_size = (nbits*M+7)/8
//   - nbits > 24 官方直接抛「nbits larger than 24 is not practical.」
//   - centroids 布局是 (M, ksub, dsub)，另有 centroids_sq_lengths 布局 (M, ksub)
//   - L2 距离表 dis_table(m, j) = ||x_m - c_(m,j)||²；内积表是 <x_m, c_(m,j)>
//   - ADC 只需对距离表求和，不必还原向量

// L2Sqr 平方欧氏距离（faiss 全程用平方距离，不开根）。
func L2Sqr(a, b []float64) float64 {
	var s float64
	for i := range a {
		t := a[i] - b[i]
		s += t * t
	}
	return s
}

// Dot 内积。
func Dot(a, b []float64) float64 {
	var s float64
	for i := range a {
		s += a[i] * b[i]
	}
	return s
}

// SubVec 逐元素相减（残差编码用）。
func SubVec(a, b []float64) []float64 {
	out := make([]float64, len(a))
	for i := range a {
		out[i] = a[i] - b[i]
	}
	return out
}

// Kmeans 确定性 k-means（k-means++ 初始化 + Lloyd）。
// faiss 侧默认 `ClusteringParameters{niter=25, min_points_per_centroid=39,
// max_points_per_centroid=256}`；这里用定种子 LCG 复刻可复现的初始化。
func Kmeans(points [][]float64, k, niter, seed int) [][]float64 {
	if len(points) == 0 {
		panic("empty points")
	}
	d := len(points[0])
	x := seed & 0x7FFFFFFF
	nxt := func() float64 {
		x = (1103515245*x + 12345) & 0x7FFFFFFF
		return float64(x%1000000) / 1000000.0
	}

	cent := [][]float64{append([]float64{}, points[int(nxt()*float64(len(points)))%len(points)]...)}
	for len(cent) < k {
		best, bestScore := points[0], -1.0
		for _, p := range points {
			dd := L2Sqr(p, cent[0])
			for _, c := range cent[1:] {
				if v := L2Sqr(p, c); v < dd {
					dd = v
				}
			}
			score := dd * (0.5 + nxt())
			if score > bestScore {
				bestScore, best = score, p
			}
		}
		cent = append(cent, append([]float64{}, best...))
	}

	for it := 0; it < niter; it++ {
		buckets := make([][][]float64, k)
		for _, p := range points {
			bi, bd := 0, 0.0
			for ci, c := range cent {
				if v := L2Sqr(p, c); ci == 0 || v < bd {
					bd, bi = v, ci
				}
			}
			buckets[bi] = append(buckets[bi], p)
		}
		moved := 0.0
		for ci, b := range buckets {
			if len(b) == 0 {
				continue // 官方对空簇不更新质心
			}
			next := make([]float64, d)
			for _, v := range b {
				for j := range next {
					next[j] += v[j]
				}
			}
			for j := range next {
				next[j] /= float64(len(b))
			}
			moved += L2Sqr(next, cent[ci])
			cent[ci] = next
		}
		if moved < 1e-18 {
			break
		}
	}
	return cent
}

// ProductQuantizer 对应 faiss::ProductQuantizer。
type ProductQuantizer struct {
	D, M, Nbits   int
	Dsub, Ksub    int
	CodeSize      int
	Centroids     [][][]float64 // 布局 (M, ksub, dsub)
	CentroidsSQL  [][]float64   // 布局 (M, ksub)，即 centroids_sq_lengths
}

// NewPQ 构造并按 `set_derived_values` 校验。
func NewPQ(d, m, nbits int) *ProductQuantizer {
	p := &ProductQuantizer{D: d, M: m, Nbits: nbits}
	p.setDerivedValues()
	return p
}

func (p *ProductQuantizer) setDerivedValues() {
	if p.M <= 0 {
		panic("M must be > 0")
	}
	if p.D%p.M != 0 {
		panic("The dimension of the vector (d) should be a multiple of the number of subquantizers (M)")
	}
	if p.D > 0 && p.Nbits > 24 {
		panic("nbits larger than 24 is not practical.")
	}
	p.Dsub = p.D / p.M
	p.CodeSize = (p.Nbits*p.M + 7) / 8
	p.Ksub = 1 << uint(p.Nbits)
}

// Train 逐子空间跑 k-means。
func (p *ProductQuantizer) Train(xs [][]float64, niter, seed int) {
	if len(xs) < p.Ksub {
		panic("training set smaller than ksub")
	}
	p.Centroids = make([][][]float64, p.M)
	p.CentroidsSQL = make([][]float64, p.M)
	for m := 0; m < p.M; m++ {
		sub := make([][]float64, len(xs))
		for i, x := range xs {
			sub[i] = x[m*p.Dsub : (m+1)*p.Dsub]
		}
		cm := Kmeans(sub, p.Ksub, niter, seed+m)
		p.Centroids[m] = cm
		sq := make([]float64, p.Ksub)
		for i, c := range cm {
			sq[i] = Dot(c, c)
		}
		p.CentroidsSQL[m] = sq
	}
}

// ComputeCode 对应 compute_code：逐子空间取最近质心。
func (p *ProductQuantizer) ComputeCode(x []float64) []int {
	code := make([]int, p.M)
	for m := 0; m < p.M; m++ {
		xm := x[m*p.Dsub : (m+1)*p.Dsub]
		bi, bd := 0, 0.0
		for i, c := range p.Centroids[m] {
			if v := L2Sqr(xm, c); i == 0 || v < bd {
				bd, bi = v, i
			}
		}
		code[m] = bi
	}
	return code
}

// Decode 对应 decode：各子空间质心拼接。
func (p *ProductQuantizer) Decode(code []int) []float64 {
	out := make([]float64, 0, p.D)
	for m, i := range code {
		out = append(out, p.Centroids[m][i]...)
	}
	return out
}

// ComputeDistanceTable 对应 compute_distance_table：dis_table(m,j) = ||x_m - c_(m,j)||²
func (p *ProductQuantizer) ComputeDistanceTable(x []float64) [][]float64 {
	tbl := make([][]float64, p.M)
	for m := 0; m < p.M; m++ {
		xm := x[m*p.Dsub : (m+1)*p.Dsub]
		row := make([]float64, p.Ksub)
		for j, c := range p.Centroids[m] {
			row[j] = L2Sqr(xm, c)
		}
		tbl[m] = row
	}
	return tbl
}

// ComputeInnerProdTable 对应 compute_inner_prod_table：<x_m, c_(m,j)>
func (p *ProductQuantizer) ComputeInnerProdTable(x []float64) [][]float64 {
	tbl := make([][]float64, p.M)
	for m := 0; m < p.M; m++ {
		xm := x[m*p.Dsub : (m+1)*p.Dsub]
		row := make([]float64, p.Ksub)
		for j, c := range p.Centroids[m] {
			row[j] = Dot(xm, c)
		}
		tbl[m] = row
	}
	return tbl
}

// AdcL2 ADC：对距离表求和，不还原向量。
func (p *ProductQuantizer) AdcL2(tbl [][]float64, code []int) float64 {
	var s float64
	for m := range code {
		s += tbl[m][code[m]]
	}
	return s
}

// AdcIPAsymmetric 内积 ADC：||x-y||² = ||x||² + ||y||² - 2<x,y>
func (p *ProductQuantizer) AdcIPAsymmetric(x []float64, ip [][]float64, code []int) float64 {
	xsq := Dot(x, x)
	ysq, dot := 0.0, 0.0
	for m := range code {
		ysq += p.CentroidsSQL[m][code[m]]
		dot += ip[m][code[m]]
	}
	return xsq + ysq - 2.0*dot
}

// ReconstructionError 平均量化误差。
func (p *ProductQuantizer) ReconstructionError(xs [][]float64) float64 {
	var tot float64
	for _, x := range xs {
		tot += L2Sqr(x, p.Decode(p.ComputeCode(x)))
	}
	return tot / float64(len(xs))
}
