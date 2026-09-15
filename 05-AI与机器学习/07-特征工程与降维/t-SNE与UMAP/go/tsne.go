// t-SNE 核心(Go 版):perplexity 二分搜索 + 重尾 t 分布核上的 KL 最小化。
// 与 ../python/tsne.py 同题(van der Maaten & Hinton, JMLR 9:2579-2605, 2008)。
package main

import (
	"math"
	"math/rand"
)

func squaredDistances(X [][]float64) [][]float64 {
	n := len(X)
	D2 := make([][]float64, n)
	for i := 0; i < n; i++ {
		D2[i] = make([]float64, n)
	}
	for i := 0; i < n; i++ {
		for j := i + 1; j < n; j++ {
			s := 0.0
			for t := range X[i] {
				d := X[i][t] - X[j][t]
				s += d * d
			}
			D2[i][j], D2[j][i] = s, s
		}
	}
	return D2
}

// jointProbabilities 逐点二分搜带宽 beta,使条件分布的熵 = log(perplexity),再对称化。
func jointProbabilities(D2 [][]float64, perplexity float64) [][]float64 {
	n := len(D2)
	target := math.Log(perplexity)
	P := make([][]float64, n)
	for i := range P {
		P[i] = make([]float64, n)
	}
	for i := 0; i < n; i++ {
		beta, lo, hi := 1.0, 0.0, math.Inf(1)
		for iter := 0; iter < 60; iter++ {
			row := make([]float64, n)
			s := 0.0
			for j := 0; j < n; j++ {
				if j != i {
					row[j] = math.Exp(-beta * D2[i][j])
					s += row[j]
				}
			}
			if s <= 1e-300 { // beta 过大导致全下溢 ⇒ 收上界
				hi = beta
				beta = 0.5 * (lo + beta)
				continue
			}
			sumd := 0.0
			for j := 0; j < n; j++ {
				sumd += D2[i][j] * row[j]
			}
			entropy := math.Log(s) + beta*sumd/s // H = logZ + β·Σd²p
			if math.Abs(entropy-target) < 1e-5 {
				break
			}
			if entropy > target {
				lo = beta
			} else {
				hi = beta
			}
			if math.IsInf(hi, 1) {
				beta *= 2
			} else {
				beta = 0.5 * (lo + hi)
			}
		}
		row := make([]float64, n)
		s := 0.0
		for j := 0; j < n; j++ {
			if j != i {
				row[j] = math.Exp(-beta * D2[i][j])
				s += row[j]
			}
		}
		if s == 0 {
			s = 1
		}
		for j := 0; j < n; j++ {
			P[i][j] = row[j] / s
		}
	}
	for i := 0; i < n; i++ { // 对称化 P_ij = (P_{j|i}+P_{i|j})/(2n)
		for j := i + 1; j < n; j++ {
			v := (P[i][j] + P[j][i]) / (2.0 * float64(n))
			P[i][j], P[j][i] = v, v
		}
	}
	return P
}

// tSNE 返回 (Y, KL 末值, 快照)。snapshots 为"在第几步取快照"的集合。
func tSNE(X [][]float64, perplexity float64, nIter int, lr float64, seed int64,
	snapshots map[int]bool) ([][]float64, float64, map[int][][]float64) {
	rng := rand.New(rand.NewSource(seed))
	n := len(X)
	P := jointProbabilities(squaredDistances(X), perplexity)
	Y := make([][]float64, n)
	vel := make([][]float64, n)
	for i := 0; i < n; i++ {
		Y[i] = []float64{rng.NormFloat64() * 1e-4, rng.NormFloat64() * 1e-4}
		vel[i] = []float64{0, 0}
	}
	const exaggerItters = 100
	var kl float64
	snaps := map[int][][]float64{}
	for step := 1; step <= nIter; step++ {
		alpha := 1.0 // 早期夸张:前 100 步把 P 放大 12 倍,先让簇成形
		momentum := 0.8
		if step <= exaggerItters {
			alpha, momentum = 12.0, 0.5
		}
		inv := make([][]float64, n)
		for i := range inv {
			inv[i] = make([]float64, n)
		}
		total := 0.0
		for i := 0; i < n; i++ {
			for j := i + 1; j < n; j++ {
				dx := Y[i][0] - Y[j][0]
				dy := Y[i][1] - Y[j][1]
				v := 1.0 / (1.0 + dx*dx + dy*dy) // 自由度为 1 的 t 分布核
				inv[i][j], inv[j][i] = v, v
				total += 2 * v
			}
		}
		if total == 0 {
			total = 1
		}
		grad := make([][]float64, n)
		for i := 0; i < n; i++ {
			gx, gy := 0.0, 0.0
			for j := 0; j < n; j++ {
				if j == i {
					continue
				}
				w := (alpha*P[i][j] - inv[i][j]/total) * inv[i][j] // 4·(αP-Q)·(1+d²)^{-1}
				gx += w * (Y[i][0] - Y[j][0])
				gy += w * (Y[i][1] - Y[j][1])
			}
			grad[i] = []float64{4 * gx, 4 * gy}
		}
		for i := 0; i < n; i++ {
			for d := 0; d < 2; d++ {
				vel[i][d] = momentum*vel[i][d] - lr*grad[i][d]
				Y[i][d] += vel[i][d]
			}
		}
		kl = klDivergence(P, inv, total)
		if snapshots[step] {
			cp := make([][]float64, n)
			for i := range Y {
				cp[i] = []float64{Y[i][0], Y[i][1]}
			}
			snaps[step] = cp
		}
	}
	return Y, kl, snaps
}

func klDivergence(P, inv [][]float64, total float64) float64 {
	n := len(P)
	kl := 0.0
	for i := 0; i < n; i++ {
		for j := 0; j < n; j++ {
			if i != j && P[i][j] > 0 {
				kl += P[i][j] * math.Log(P[i][j]*total/inv[i][j])
			}
		}
	}
	return kl
}

// gaussianCluster 生成 n 个中心在 center、各维方差 sigma² 的点。
func gaussianCluster(n, dim int, center []float64, sigma float64, rng *rand.Rand) [][]float64 {
	pts := make([][]float64, n)
	for i := 0; i < n; i++ {
		pts[i] = make([]float64, dim)
		for t := 0; t < dim; t++ {
			pts[i][t] = center[t] + rng.NormFloat64()*sigma
		}
	}
	return pts
}
