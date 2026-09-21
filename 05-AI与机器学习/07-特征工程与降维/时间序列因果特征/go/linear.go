package main

// demo 516 Go 侧:最小二乘底座,与 python/model.py 逐条对应。
// 只为把"泄漏有多大"量化成 R²,不追求数值稳健 —— 设计矩阵都是标准化过的小矩阵。

import "math"

// Solve 部分主元高斯消元。主元为 0 时跳过该列(对应 Python 的 continue)。
func Solve(A [][]float64, b []float64) []float64 {
	n := len(A)
	M := make([][]float64, n)
	for i := 0; i < n; i++ {
		row := make([]float64, 0, n+1)
		row = append(row, A[i]...)
		M[i] = append(row, b[i])
	}
	for k := 0; k < n; k++ {
		p := k
		for i := k + 1; i < n; i++ {
			if math.Abs(M[i][k]) > math.Abs(M[p][k]) {
				p = i
			}
		}
		if p != k {
			M[k], M[p] = M[p], M[k]
		}
		piv := M[k][k]
		if piv == 0.0 {
			continue
		}
		for i := k + 1; i < n; i++ {
			f := M[i][k] / piv
			M[i][k] = 0.0
			for j := k + 1; j <= n; j++ {
				M[i][j] -= f * M[k][j]
			}
		}
	}
	x := make([]float64, n)
	for i := n - 1; i >= 0; i-- {
		s := M[i][n]
		for j := i + 1; j < n; j++ {
			s -= M[i][j] * x[j]
		}
		if M[i][i] != 0.0 {
			x[i] = s / M[i][i]
		}
	}
	return x
}

// FitOLS 岭回归式稳定化:l2 只加在对角上,免得完全共线时崩。
func FitOLS(X [][]float64, y []float64, l2 float64) []float64 {
	n := len(X)
	p := len(X[0])
	A := make([][]float64, p)
	for a := 0; a < p; a++ {
		A[a] = make([]float64, p)
		for bb := 0; bb < p; bb++ {
			s := 0.0
			for i := 0; i < n; i++ {
				s += X[i][a] * X[i][bb]
			}
			if a == bb {
				s += l2
			}
			A[a][bb] = s
		}
	}
	rhs := make([]float64, p)
	for a := 0; a < p; a++ {
		s := 0.0
		for i := 0; i < n; i++ {
			s += X[i][a] * y[i]
		}
		rhs[a] = s
	}
	return Solve(A, rhs)
}

func Predict(X [][]float64, w []float64) []float64 {
	out := make([]float64, len(X))
	for i := range X {
		s := 0.0
		for j := range w {
			s += X[i][j] * w[j]
		}
		out[i] = s
	}
	return out
}

// R2 的 ssTot <= 0 分支返回 0.0(常量 y)。注意它**可以取负**,不要加 max(·,0) 截断。
func R2(y, pred []float64) float64 {
	mu := meanF(y)
	ssTot := 0.0
	for _, v := range y {
		d := v - mu
		ssTot += d * d
	}
	if ssTot <= 0 {
		return 0.0
	}
	ssRes := 0.0
	for i := range y {
		d := y[i] - pred[i]
		ssRes += d * d
	}
	return 1.0 - ssRes/ssTot
}

func MSE(y, pred []float64) float64 {
	if len(y) == 0 {
		return math.NaN()
	}
	s := 0.0
	for i := range y {
		d := y[i] - pred[i]
		s += d * d
	}
	return s / float64(len(y))
}
