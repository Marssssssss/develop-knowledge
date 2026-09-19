// Package main:凝聚层次聚类与四种连接准则的 Go 版(仅标准库)。
//
// 权威依据(与 python/agglomerative.py 同源,URL 见同目录 README.md):
// scikit-learn《2.3.6 Hierarchical clustering》(Ward = 方差最小化 / complete = 最小最大距离 /
// average = 平均距离 / single = 最近观测对;"rich get richer";Ward 只能用欧氏),
// Lance & Williams 1967 的递推系数(自检里与"逐步直接重算"交叉验证,不依赖记忆数值)。
package main

import "math"

// lwCoeffs 返回 Lance-Williams 递推 d(ij,k)=α_i·d(i,k)+α_j·d(j,k)+β·d(i,j)+γ·|d(i,k)−d(j,k)| 的系数。
func lwCoeffs(linkage string, ni, nj, nk int) (ai, aj, beta, gamma float64) {
	switch linkage {
	case "single":
		return 0.5, 0.5, 0.0, -0.5
	case "complete":
		return 0.5, 0.5, 0.0, 0.5
	case "average":
		tot := float64(ni + nj)
		return float64(ni) / tot, float64(nj) / tot, 0.0, 0.0
	default: // ward
		tot := float64(ni + nj + nk)
		return float64(ni+nk) / tot, float64(nj+nk) / tot, -float64(nk) / tot, 0.0
	}
}

func eDist(a, b []float64) float64 {
	s := 0.0
	for i := range a {
		d := a[i] - b[i]
		s += d * d
	}
	return math.Sqrt(s)
}

// wardGain 合并 i、j 引起的簇内平方和增量:ΔSSE = (n_i·n_j/(n_i+n_j))·||c_i−c_j||²
func wardGain(X [][]float64, mi, mj []int) float64 {
	ni, nj := float64(len(mi)), float64(len(mj))
	d := len(X[0])
	ci, cj := make([]float64, d), make([]float64, d)
	for _, i := range mi {
		for t := 0; t < d; t++ {
			ci[t] += X[i][t]
		}
	}
	for _, i := range mj {
		for t := 0; t < d; t++ {
			cj[t] += X[i][t]
		}
	}
	for t := 0; t < d; t++ {
		ci[t] /= ni
		cj[t] /= nj
	}
	s := 0.0
	for t := 0; t < d; t++ {
		v := ci[t] - cj[t]
		s += v * v
	}
	return ni * nj / (ni + nj) * s
}

type merge struct {
	a, b int
	h    float64
	size int
}

type Agg struct {
	Linkage string
	X       [][]float64
	Merges  []merge
	Members map[int][]int
	Updates int
}

func key(a, b int) (int, int) {
	if a < b {
		return a, b
	}
	return b, a
}

// Fit 凝聚聚类。ward 的距离矩阵用**半平方距离 d²/2**,使取值恰好等于 ΔSSE;
// 其余三种用欧氏距离。
func (g *Agg) Fit(X [][]float64) {
	g.X = X
	n := len(X)
	sq := g.Linkage == "ward"
	D := map[[2]int]float64{}
	for i := 0; i < n; i++ {
		for j := i + 1; j < n; j++ {
			d := eDist(X[i], X[j])
			if sq {
				d = d * d / 2
			}
			D[[2]int{i, j}] = d
		}
	}
	members := map[int][]int{}
	active := []int{}
	for i := 0; i < n; i++ {
		members[i] = []int{i}
		active = append(active, i)
	}
	g.Merges, g.Updates = nil, 0
	nxt := n
	for len(active) > 1 {
		bi, bj, bv := -1, -1, math.Inf(1)
		for a := 0; a < len(active); a++ {
			for b := a + 1; b < len(active); b++ {
				v := D[key(active[a], active[b])]
				if v < bv-1e-15 {
					bi, bj, bv = active[a], active[b], v
				}
			}
		}
		i, j := bi, bj
		h := D[key(i, j)]
		delete(D, key(i, j))
		mi, mj := members[i], members[j]
		nu := nxt
		nxt++
		members[nu] = append(append([]int{}, mi...), mj...)
		for _, k := range active {
			if k == i || k == j {
				continue
			}
			ai, aj, beta, gamma := lwCoeffs(g.Linkage, len(mi), len(mj), len(members[k]))
			dik := D[key(i, k)]
			djk := D[key(j, k)]
			delete(D, key(i, k))
			delete(D, key(j, k))
			val := ai*dik + aj*djk + beta*h + gamma*math.Abs(dik-djk)
			D[key(nu, k)] = val
			g.Updates++
		}
		na := []int{}
		for _, c := range active {
			if c != i && c != j {
				na = append(na, c)
			}
		}
		active = append(na, nu)
		g.Merges = append(g.Merges, merge{i, j, h, len(members[nu])})
	}
	g.Members = members
}

// PairDist 直接按准则重算两簇距离(用于校验递推),members 传下标集合。
func (g *Agg) PairDist(ma, mb []int) float64 {
	switch g.Linkage {
	case "single":
		best := math.Inf(1)
		for _, i := range ma {
			for _, j := range mb {
				if v := eDist(g.X[i], g.X[j]); v < best {
					best = v
				}
			}
		}
		return best
	case "complete":
		best := 0.0
		for _, i := range ma {
			for _, j := range mb {
				if v := eDist(g.X[i], g.X[j]); v > best {
					best = v
				}
			}
		}
		return best
	case "average":
		s, cnt := 0.0, 0
		for _, i := range ma {
			for _, j := range mb {
				s += eDist(g.X[i], g.X[j])
				cnt++
			}
		}
		return s / float64(cnt)
	default:
		return wardGain(g.X, ma, mb)
	}
}

// Heights 返回合并高度序列。
func (g *Agg) Heights() []float64 {
	out := make([]float64, len(g.Merges))
	for i, m := range g.Merges {
		out[i] = m.h
	}
	return out
}

// CutTree 按合并序列回放,切成恰好 k 个簇。
func CutTree(merges []merge, n, k int) []int {
	groups := map[int][]int{}
	for i := 0; i < n; i++ {
		groups[i] = []int{i}
	}
	for step, m := range merges {
		if step >= n-k {
			break
		}
		ga, gb := groups[m.a], groups[m.b]
		delete(groups, m.a)
		delete(groups, m.b)
		groups[n+step] = append(append([]int{}, ga...), gb...)
	}
	labels := make([]int, n)
	c := 0
	for i := 0; i < n+len(merges); i++ {
		if mem, ok := groups[i]; ok {
			for _, idx := range mem {
				labels[idx] = c
			}
			c++
		}
	}
	return labels
}
