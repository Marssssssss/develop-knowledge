package main

// demo 515 Go 侧:随机化截断 SVD 与 TruncatedSVD。
// 语义与 python/rsvd.py 逐条对应;python 侧已与 sklearn 1.9.1 对拍到 114/114。

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func sliceRows(A Mat, k int) Mat {
	out := make(Mat, k)
	for i := 0; i < k; i++ {
		out[i] = append([]float64(nil), A[i]...)
	}
	return out
}

func sliceCols(A Mat, k int) Mat {
	m := len(A)
	out := make(Mat, m)
	for i := 0; i < m; i++ {
		out[i] = append([]float64(nil), A[i][:k]...)
	}
	return out
}

func normalizeStep(A, Q Mat, norm string) Mat {
	P := matmul(A, Q)
	switch norm {
	case "QR":
		o, _ := qrHouse(P)
		return o
	case "LU":
		o, _ := luPermuteL(P)
		return o
	default: // "none"
		return P
	}
}

// randomizedRangeFinder 求 A 值域的正交基 Q(shape = m x size)。
func randomizedRangeFinder(A Mat, size, nIter int, normalizer string, r *Rng) Mat {
	n := len(A[0])
	Q := randn(n, size, r) // 是 (列数, size),不是 (行数, size)
	norm := normalizer
	if norm == "auto" {
		if nIter <= 2 {
			norm = "none"
		} else {
			norm = "LU"
		}
	}
	At := transpose(A)
	for i := 0; i < nIter; i++ {
		Q = normalizeStep(A, Q, norm)
		Q = normalizeStep(At, Q, norm)
	}
	out, _ := qrHouse(matmul(A, Q))
	return out
}

// randomizedSVD 返回 (U, s, Vt)。nIter < 0 表示官方语义的 "auto";
// transposed 取 "auto" / "true" / "false"。
func randomizedSVD(M Mat, nComp, nOversamples, nIter int, normalizer, transposed string,
	flipSign bool, r *Rng) (Mat, []float64, Mat) {
	size := nComp + nOversamples
	m, n := len(M), len(M[0])
	nIt := nIter
	if nIt < 0 {
		if float64(nComp) < 0.1*float64(minInt(m, n)) {
			nIt = 7
		} else {
			nIt = 4
		}
	}
	tr := m < n
	if transposed == "true" {
		tr = true
	} else if transposed == "false" {
		tr = false
	}
	A := M
	if tr {
		A = transpose(M)
	}
	Q := randomizedRangeFinder(A, size, nIt, normalizer, r)
	B := matmul(transpose(Q), A)
	Uhat, s, Vt := jacobiSVD(B, 1e-14, 60)
	U := matmul(Q, Uhat)
	if flipSign {
		U, Vt = svdFlip(U, Vt, !tr) // 非 transpose 走 u_based;transpose 走 v_based
	}
	if tr {
		return transpose(sliceRows(Vt, nComp)), append([]float64(nil), s[:nComp]...),
			transpose(sliceCols(U, nComp))
	}
	return sliceCols(U, nComp), append([]float64(nil), s[:nComp]...), sliceRows(Vt, nComp)
}

// TruncatedSVD 不中心化的截断 SVD。
type TruncatedSVD struct {
	NComponents              int
	NIter                    int
	NOversamples             int
	PowerIterationNormalizer string
	Components               Mat
	SingularValues           []float64
	ExplainedVariance        []float64
	ExplainedVarianceRatio   []float64
}

func (t *TruncatedSVD) FitTransform(X Mat, r *Rng) Mat {
	n := len(X[0])
	if t.NComponents > n {
		panic("n_components must be <= n_features")
	}
	_, Sigma, VT := randomizedSVD(X, t.NComponents, t.NOversamples, t.NIter,
		t.PowerIterationNormalizer, "auto", false, r)
	_, VT = svdFlip(nil, VT, false)
	t.Components = VT
	Z := matmul(X, transpose(t.Components))
	t.ExplainedVariance = make([]float64, len(Z[0]))
	for c := range t.ExplainedVariance {
		col := make([]float64, len(Z))
		for i := range Z {
			col[i] = Z[i][c]
		}
		t.ExplainedVariance[c] = variance(col)
	}
	full := 0.0
	for q := 0; q < n; q++ {
		col := make([]float64, len(X))
		for i := range X {
			col[i] = X[i][q]
		}
		full += variance(col)
	}
	t.ExplainedVarianceRatio = make([]float64, len(t.ExplainedVariance))
	for c := range t.ExplainedVariance {
		t.ExplainedVarianceRatio[c] = t.ExplainedVariance[c] / full
	}
	t.SingularValues = Sigma
	return Z
}

func (t *TruncatedSVD) Transform(X Mat) Mat {
	return matmul(X, transpose(t.Components))
}

func (t *TruncatedSVD) InverseTransform(X Mat) Mat {
	return matmul(X, t.Components)
}

// variance 是总体方差(ddof = 0),对应 np.var 默认行为。
func variance(col []float64) float64 {
	mu := 0.0
	for _, v := range col {
		mu += v
	}
	mu /= float64(len(col))
	s := 0.0
	for _, v := range col {
		d := v - mu
		s += d * d
	}
	return s / float64(len(col))
}
