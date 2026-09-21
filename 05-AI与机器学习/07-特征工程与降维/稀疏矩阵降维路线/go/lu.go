package main

import "math"

// demo 515 Go 侧:部分主元 LU 与 LCG 随机源。

// luPermuteL 返回 (P L, U) 使 A = (P L) @ U,部分主元。
func luPermuteL(A Mat) (Mat, Mat) {
	m, n := len(A), len(A[0])
	k := m
	if n < m {
		k = n
	}
	LU := copyMat(A)
	perm := make([]int, m)
	for i := range perm {
		perm[i] = i
	}
	for j := 0; j < k; j++ {
		p := j
		for i := j + 1; i < m; i++ {
			if math.Abs(LU[i][j]) > math.Abs(LU[p][j]) {
				p = i
			}
		}
		if p != j {
			LU[j], LU[p] = LU[p], LU[j]
			perm[j], perm[p] = perm[p], perm[j]
		}
		piv := LU[j][j]
		if piv == 0 {
			continue
		}
		for i := j + 1; i < m; i++ {
			f := LU[i][j] / piv
			LU[i][j] = f
			if f != 0 {
				for c := j + 1; c < n; c++ {
					LU[i][c] -= f * LU[j][c]
				}
			}
		}
	}
	L := zeros(m, k)
	for i := 0; i < m; i++ {
		lim := i
		if k-1 < lim {
			lim = k - 1
		}
		for j := 0; j <= lim; j++ {
			switch {
			case i > j:
				L[i][j] = LU[i][j]
			case i == j:
				L[i][j] = 1
			}
		}
	}
	U := zeros(k, n)
	for i := 0; i < k; i++ {
		for j := i; j < n; j++ {
			U[i][j] = LU[i][j]
		}
	}
	PL := zeros(m, k)
	for i := 0; i < m; i++ {
		copy(PL[perm[i]], L[i])
	}
	return PL, U
}

// Rng 是与 python/linalg.py 同参数的 LCG(Numerical Recipes)。
type Rng struct{ s uint32 }

func newRng(seed uint32) *Rng { return &Rng{s: seed} }

func (r *Rng) u32() uint32 {
	r.s = 1664525*r.s + 1013904223
	return r.s
}

func (r *Rng) uniform() float64 { return float64(r.u32()) / 4294967296.0 }

func (r *Rng) normal() float64 {
	u1 := r.uniform()
	if u1 < 1e-300 {
		u1 = 1e-300
	}
	u2 := r.uniform()
	return math.Sqrt(-2*math.Log(u1)) * math.Cos(2*math.Pi*u2)
}

func randn(m, n int, r *Rng) Mat {
	out := zeros(m, n)
	for i := range out {
		for j := range out[i] {
			out[i][j] = r.normal()
		}
	}
	return out
}
