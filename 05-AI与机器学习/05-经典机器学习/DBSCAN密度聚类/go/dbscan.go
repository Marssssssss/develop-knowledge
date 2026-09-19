// Package main:DBSCAN 密度聚类的 Go 版(仅标准库),严格照 Ester et al. 1996 的伪码。
//
// 权威依据(与 python/dbscan.py 同源,URL 见同目录 README.md):
// 论文 KDD-96 的 Definition 1~6 与 ExpandCluster 伪码;scikit-learn《2.3.7 DBSCAN》
// 与 `_dbscan.py` 的 min_samples 口径("includes the point itself")、噪声标签 −1。
package main

import "math"

// 与 sklearn 一致:噪声标签为 −1;未访问用 −2(论文里的 UNCLASSIFIED)。
const (
	noise        = -1
	unclassified = -2
)

// regionQuery 返回下标 i 的 eps-邻域(Definition 1:dist ≤ eps,**含 i 自身**)。
func regionQuery(X [][]float64, i int, eps float64, stats *int) []int {
	if stats != nil {
		*stats++
	}
	out := []int{}
	for j, x := range X {
		s := 0.0
		for k := range x {
			d := X[i][k] - x[k]
			s += d * d
		}
		if math.Sqrt(s) <= eps {
			out = append(out, j)
		}
	}
	return out
}

func isCore(X [][]float64, i int, eps float64, minPts int) bool {
	return len(regionQuery(X, i, eps, nil)) >= minPts
}

// dbscan 返回 (labels, core)。labels:簇号 ≥ 0,噪声 = −1。
func dbscan(X [][]float64, eps float64, minPts int, stats *int) ([]int, []int) {
	n := len(X)
	labels := make([]int, n)
	isc := make([]bool, n)
	for i := range labels {
		labels[i] = unclassified
	}
	cid := 0
	for i := 0; i < n; i++ {
		if labels[i] != unclassified {
			continue
		}
		seeds := regionQuery(X, i, eps, stats)
		if len(seeds) < minPts { // no core point
			labels[i] = noise // 之后可能被核心点收编为边界点
			continue
		}
		isc[i] = true
		// 伪码 changeCiIds(seeds, ClId):种子整体打簇号,但不覆盖已有簇标签
		// (论文:同属两簇的点归**先被发现**的簇)。
		for _, s := range seeds {
			if labels[s] == unclassified || labels[s] == noise {
				labels[s] = cid
			}
		}
		queue := []int{}
		for _, s := range seeds {
			if s != i {
				queue = append(queue, s)
			}
		}
		for len(queue) > 0 {
			cur := queue[0]
			queue = queue[1:]
			result := regionQuery(X, cur, eps, stats)
			if len(result) >= minPts { // cur 是核心点
				isc[cur] = true
				for _, r := range result {
					if labels[r] == unclassified || labels[r] == noise {
						if labels[r] == unclassified {
							queue = append(queue, r)
						}
						labels[r] = cid // 原标 NOISE 的边界点在此被改写
					}
				}
			}
		}
		cid++
	}
	core := []int{}
	for i := range isc {
		if isc[i] {
			core = append(core, i)
		}
	}
	for i := range labels {
		if labels[i] == unclassified {
			labels[i] = noise
		}
	}
	return labels, core
}

// kdist 每个点到第 k 近邻的距离(§4.2 sorted k-dist 图)。
func kdist(X [][]float64, k int) []float64 {
	out := make([]float64, len(X))
	for i := range X {
		ds := []float64{}
		for j := range X {
			if j == i {
				continue
			}
			s := 0.0
			for t := range X[i] {
				d := X[i][t] - X[j][t]
				s += d * d
			}
			ds = append(ds, math.Sqrt(s))
		}
		for a := 1; a < len(ds); a++ { // 插入排序(数据量小,避免 sort 包依赖)
			for b := a; b > 0 && ds[b] < ds[b-1]; b-- {
				ds[b], ds[b-1] = ds[b-1], ds[b]
			}
		}
		out[i] = ds[k-1]
	}
	return out
}

type rng struct{ st uint64 }

func newRNG(seed uint64) *rng { return &rng{st: seed} }

func (r *rng) next() float64 {
	r.st = r.st*6364136223846793005 + 1442695040888963407
	return float64(r.st>>11) / float64(1<<53)
}

// ari 调整兰德指数:比较两个划分是否等价(忽略簇标签置换)。
func ari(a, b []int) float64 {
	n := len(a)
	ma, mb := 0, 0
	for i := range a {
		if a[i] > ma {
			ma = a[i]
		}
		if b[i] > mb {
			mb = b[i]
		}
	}
	sa, sb := make([]int, ma+1), make([]int, mb+1)
	tab := map[[2]int]int{}
	for i := range a {
		sa[a[i]]++
		sb[b[i]]++
		tab[[2]int{a[i], b[i]}]++
	}
	c := func(v int) float64 { return float64(v) * float64(v-1) / 2 }
	sij, xa, xb := 0.0, 0.0, 0.0
	for _, v := range tab {
		sij += c(v)
	}
	for _, v := range sa {
		xa += c(v)
	}
	for _, v := range sb {
		xb += c(v)
	}
	exp, mxp := xa*xb/c(n), (xa+xb)/2
	if math.Abs(mxp-exp) < 1e-12 {
		return 1.0
	}
	return (sij - exp) / (mxp - exp)
}
