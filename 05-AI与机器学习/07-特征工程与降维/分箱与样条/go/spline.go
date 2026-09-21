package main

// SplineTransformer 的纯标准库 Go 实现,与 python/spline_core.py 同口径。
// 对齐对象:scikit-learn 1.9.1 sklearn/preprocessing/_polynomial.py。
// 基函数求值见 basis.go。

import "math"

// ---------------------------------------------------------------- 结位置与结扩展

func BaseKnotPositions(col []float64, nKnots int, knots string) []float64 {
	if knots == "quantile" {
		levels := Linspace(0, 100, nKnots)
		out := make([]float64, nKnots)
		for i, lv := range levels {
			out[i] = PercentileLinear(col, lv/100.0)
		}
		return out
	}
	lo, hi := minMax(col)
	return Linspace(lo, hi, nKnots)
}

// BuildKnotVector 返回 (完整结向量, n_splines)。
// 端外结**不重复**首末结,而是沿用首/末两结间距(Eilers & Marx 的建议)。
func BuildKnotVector(base []float64, degree int) ([]float64, int) {
	nSplines := len(base) + degree - 1
	distMin := base[1] - base[0]
	distMax := base[len(base)-1] - base[len(base)-2]
	t := Linspace(base[0]-float64(degree)*distMin, base[0]-distMin, degree)
	t = append(t, base...)
	t = append(t, Linspace(base[len(base)-1]+distMax,
		base[len(base)-1]+float64(degree)*distMax, degree)...)
	return t, nSplines
}

// BuildKnotVectorPeriodic 按周期平移出端外结;n_splines = n_knots - 1。
func BuildKnotVectorPeriodic(base []float64, degree int) ([]float64, int) {
	nSplines := len(base) - 1
	period := base[len(base)-1] - base[0]
	t := []float64{}
	for i := len(base) - degree - 1; i < len(base)-1; i++ {
		t = append(t, base[i]-period)
	}
	t = append(t, base...)
	for i := 1; i <= degree; i++ {
		t = append(t, base[i]+period)
	}
	return t, nSplines
}

// ---------------------------------------------------------------- 变换器

type SplineTransformer struct {
	NKnots        int
	Degree        int
	Knots         string
	Extrapolation string
	IncludeBias   bool
	KnotVecs      [][]float64
	NSplines      int
	NFeaturesOut  int
}

func NewSpline(nKnots, degree int, extrapolation string, includeBias bool) *SplineTransformer {
	return &SplineTransformer{NKnots: nKnots, Degree: degree, Knots: "uniform",
		Extrapolation: extrapolation, IncludeBias: includeBias}
}

func NewSplineKnots(nKnots, degree int, knots, extrapolation string, includeBias bool) *SplineTransformer {
	return &SplineTransformer{NKnots: nKnots, Degree: degree, Knots: knots,
		Extrapolation: extrapolation, IncludeBias: includeBias}
}

func (s *SplineTransformer) Fit(X [][]float64) *SplineTransformer {
	nFeat := len(X[0])
	s.KnotVecs = s.KnotVecs[:0]
	for j := 0; j < nFeat; j++ {
		col := make([]float64, len(X))
		for i := range X {
			col[i] = X[i][j]
		}
		base := BaseKnotPositions(col, s.NKnots, s.Knots)
		var t []float64
		var ns int
		if s.Extrapolation == "periodic" {
			t, ns = BuildKnotVectorPeriodic(base, s.Degree)
		} else {
			t, ns = BuildKnotVector(base, s.Degree)
		}
		s.KnotVecs = append(s.KnotVecs, t)
		s.NSplines = ns
	}
	per := s.NSplines
	if !s.IncludeBias {
		per--
	}
	s.NFeaturesOut = nFeat * per
	return s
}

// columnRow 返回单列、单个样本值对应的长度 NSplines 的基值。
func (s *SplineTransformer) columnRow(j int, x float64) []float64 {
	t := s.KnotVecs[j]
	d := s.Degree
	ns := s.NSplines
	xmin, xmax := t[d], t[len(t)-d-1]
	switch s.Extrapolation {
	case "periodic":
		span := t[len(t)-d-1] - t[d] // = 周期,不是 t[ns]-t[d]
		xx := 0.0
		if span > 0 {
			// Go 的 math.Mod 取被除数符号,负值会跑到区间外;先归一到 [0, span)
			r := math.Mod(x-t[d], span)
			if r < 0 {
				r += span
			}
			xx = t[d] + r
		}
		full := BsplineValues(t, d, xx, true) // 长度 ns + degree
		out := make([]float64, ns)
		for i := 0; i < d; i++ {
			out[i] = full[i] + full[ns+i]
		}
		copy(out[d:], full[d:ns])
		return out
	case "continue":
		return BsplineValues(t, d, x, true)
	case "error":
		if x < xmin || x > xmax {
			panic("X contains values beyond the limits of the knots")
		}
		return BsplineValues(t, d, x, false)
	}
	clamped := x
	if clamped < xmin {
		clamped = xmin
	}
	if clamped > xmax {
		clamped = xmax
	}
	if s.Extrapolation == "constant" {
		if x < xmin {
			fmin := BsplineValues(t, d, xmin, false)
			out := make([]float64, ns)
			copy(out[:d], fmin[:d])
			return out
		}
		if x > xmax {
			fmax := BsplineValues(t, d, xmax, false)
			out := make([]float64, ns)
			copy(out[ns-d:], fmax[ns-d:])
			return out
		}
		return BsplineValues(t, d, clamped, false)
	}
	// linear:只有首/末 degree 个基非零,按边界导数线性延拓
	steps := d
	if d <= 1 {
		steps = d + 1
	}
	out := make([]float64, ns)
	if x < xmin {
		fmin := BsplineValues(t, d, xmin, false)
		fpm := BsplineDerivative(t, d, xmin)
		for k := 0; k < steps; k++ {
			out[k] = fmin[k] + (x-xmin)*fpm[k]
		}
		return out
	}
	if x > xmax {
		fmax := BsplineValues(t, d, xmax, false)
		fpm := BsplineDerivative(t, d, xmax)
		for k := 0; k < steps; k++ {
			idx := ns - 1 - k
			out[idx] = fmax[idx] + (x-xmax)*fpm[idx]
		}
		return out
	}
	return BsplineValues(t, d, clamped, false)
}

func (s *SplineTransformer) Transform(X [][]float64) [][]float64 {
	out := make([][]float64, len(X))
	for i, row := range X {
		enc := []float64{}
		for j, x := range row {
			block := s.columnRow(j, x)
			if s.IncludeBias {
				enc = append(enc, block...)
			} else {
				enc = append(enc, block[:len(block)-1]...)
			}
		}
		out[i] = enc
	}
	return out
}
