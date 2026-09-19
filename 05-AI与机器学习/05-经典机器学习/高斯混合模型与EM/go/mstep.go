// 同属 main 包,与 num.go / gmm.go 一起用 `go run .` 编译。
package main

import (
	"math"
)

func (g *GaussianMixture) mStep(X [][]float64, resp [][]float64) {
	n, d := len(X), len(X[0])
	nk := make([]float64, g.K)
	for i := range X {
		for k := 0; k < g.K; k++ {
			nk[k] += resp[i][k]
		}
	}
	g.Weights = make([]float64, g.K)
	g.Means = make([][]float64, g.K)
	for k := 0; k < g.K; k++ {
		g.Weights[k] = math.Max(nk[k], 1e-300) / float64(n)
		g.Means[k] = make([]float64, d)
		for j := 0; j < d; j++ {
			s := 0.0
			for i := 0; i < n; i++ {
				s += resp[i][k] * X[i][j]
			}
			g.Means[k][j] = s / math.Max(nk[k], 1e-300)
		}
	}
	switch g.CovType {
	case "full":
		covs := make([][][]float64, g.K)
		for k := 0; k < g.K; k++ {
			c := make([][]float64, d)
			for a := 0; a < d; a++ {
				c[a] = make([]float64, d)
				for b := 0; b < d; b++ {
					s := 0.0
					for i := 0; i < n; i++ {
						s += resp[i][k] * (X[i][a] - g.Means[k][a]) * (X[i][b] - g.Means[k][b])
					}
					c[a][b] = s / math.Max(nk[k], 1e-300)
				}
				c[a][a] += g.RegCovar // reg_covar 只加对角
			}
			covs[k] = c
		}
		g.Cov = covs
	case "tied":
		c := make([][]float64, d)
		for a := 0; a < d; a++ {
			c[a] = make([]float64, d)
			for b := 0; b < d; b++ {
				s := 0.0
				for i := 0; i < n; i++ {
					for k := 0; k < g.K; k++ {
						s += resp[i][k] * (X[i][a] - g.Means[k][a]) * (X[i][b] - g.Means[k][b])
					}
				}
				c[a][b] = s / float64(n)
			}
			c[a][a] += g.RegCovar
		}
		g.Cov = [][][]float64{c}
	case "diag":
		rows := make([][]float64, g.K)
		for k := 0; k < g.K; k++ {
			row := make([]float64, d)
			for j := 0; j < d; j++ {
				s := 0.0
				for i := 0; i < n; i++ {
					s += resp[i][k] * X[i][j] * X[i][j]
				}
				row[j] = s/math.Max(nk[k], 1e-300) - g.Means[k][j]*g.Means[k][j] + g.RegCovar
			}
			rows[k] = row
		}
		g.Cov = rows
	default: // spherical = diag 各维的均值
		vals := make([]float64, g.K)
		for k := 0; k < g.K; k++ {
			s := 0.0
			for j := 0; j < d; j++ {
				acc := 0.0
				for i := 0; i < n; i++ {
					acc += resp[i][k] * X[i][j] * X[i][j]
				}
				s += acc/math.Max(nk[k], 1e-300) - g.Means[k][j]*g.Means[k][j] + g.RegCovar
			}
			vals[k] = s / float64(d)
		}
		g.Cov = vals
	}
}
