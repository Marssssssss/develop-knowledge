// DBSCAN 自检(Go 侧):与 python/dbscan_check.py 同一组结论,交叉验证两语言实现一致。
package main

import (
	"fmt"
	"math"
	"os"
)

var total, passed int
var fails []string

func check(name string, cond bool, detail string) {
	total++
	if cond {
		passed++
		fmt.Printf("  [PASS] %s  %s\n", name, detail)
	} else {
		fails = append(fails, name)
		fmt.Printf("  [FAIL] %s  %s\n", name, detail)
	}
}

func blobs(n int, seed uint64, noise int) ([][]float64, []int) {
	r := newRNG(seed)
	X, truth := [][]float64{}, []int{}
	for i := 0; i < n; i++ {
		X = append(X, []float64{r.next() * 2, r.next() * 2})
		truth = append(truth, 0)
	}
	for i := 0; i < n; i++ {
		X = append(X, []float64{8 + r.next()*2, r.next() * 2})
		truth = append(truth, 1)
	}
	for i := 0; i < noise; i++ {
		X = append(X, []float64{4 + (r.next()*2-1)*30, 40 + r.next()*10})
		truth = append(truth, -1)
	}
	return X, truth
}

func rings(n int, seed uint64) ([][]float64, []int) {
	r := newRNG(seed)
	phase := r.next() * 2 * math.Pi
	X, truth := [][]float64{}, []int{}
	for c, base := range []float64{1.0, 2.6} {
		for i := 0; i < n; i++ {
			a := phase + 2*math.Pi*float64(i)/float64(n)
			rad := base + (r.next()-0.5)*0.05
			X = append(X, []float64{rad * math.Cos(a), rad * math.Sin(a)})
			truth = append(truth, c)
		}
	}
	return X, truth
}

// kmeans 用于"任意形状 vs 球形假设"的对照。
func kmeans(X [][]float64, k int, seed uint64) []int {
	r := newRNG(seed)
	n, d := len(X), len(X[0])
	centers := [][]float64{append([]float64(nil), X[int(r.next()*float64(n))]...)}
	for len(centers) < k {
		d2 := make([]float64, n)
		tot := 0.0
		for i, x := range X {
			best := math.Inf(1)
			for _, c := range centers {
				s := 0.0
				for j := 0; j < d; j++ {
					s += (x[j] - c[j]) * (x[j] - c[j])
				}
				if s < best {
					best = s
				}
			}
			d2[i] = best
			tot += best
		}
		acc, pick := 0.0, n-1
		target := r.next() * tot
		for i, v := range d2 {
			acc += v
			if acc >= target {
				pick = i
				break
			}
		}
		centers = append(centers, append([]float64(nil), X[pick]...))
	}
	lab := make([]int, n)
	for it := 0; it < 100; it++ {
		moved := false
		for i, x := range X {
			best, bc := math.Inf(1), 0
			for c, ct := range centers {
				s := 0.0
				for j := 0; j < d; j++ {
					s += (x[j] - ct[j]) * (x[j] - ct[j])
				}
				if s < best {
					best, bc = s, c
				}
			}
			if lab[i] != bc {
				lab[i], moved = bc, true
			}
		}
		for c := 0; c < k; c++ {
			cnt := 0
			sum := make([]float64, d)
			for i, l := range lab {
				if l == c {
					cnt++
					for j := 0; j < d; j++ {
						sum[j] += X[i][j]
					}
				}
			}
			if cnt > 0 {
				for j := 0; j < d; j++ {
					centers[c][j] = sum[j] / float64(cnt)
				}
			}
		}
		if !moved {
			break
		}
	}
	return lab
}

func main() {
	fmt.Println("DBSCAN 密度聚类 自检(Go)")
	fmt.Println("==========================================================================")

	// A. 邻域含自身 / MinPts 口径
	X2 := [][]float64{{0}, {1}}
	check("A1 NEps(p) 含 p 自身", len(regionQuery(X2, 0, 1.5, nil)) == 2, "")
	lab2, core2 := dbscan(X2, 1.5, 2, nil)
	check("A2 MinPts=2 时两点都是核心点(含自身口径)", len(core2) == 2, fmt.Sprintf("core=%v", core2))
	check("A3 判为同一簇", lab2[0] == lab2[1] && lab2[0] >= 0, fmt.Sprintf("%v", lab2))

	// B. 核心-边界的非对称性
	Xb := [][]float64{{0}, {0.5}, {1.0}, {1.9}}
	check("B1 0 是核心点", isCore(Xb, 0, 1.2, 3), "")
	check("B2 3 不是核心点", !isCore(Xb, 3, 1.2, 3), "")
	inN := false
	for _, v := range regionQuery(Xb, 2, 1.2, nil) {
		if v == 3 {
			inN = true
		}
	}
	check("B3 3 ∈ NEps(2) 且 2 是核心 → 单向可达成立", inN && isCore(Xb, 2, 1.2, 3), "")

	// C. 共享边界点归先发现的簇
	A := [][]float64{{-0.95, 0}, {-1.05, 0}, {-1.15, 0}, {-1.25, 0}}
	B := [][]float64{{0.95, 0}, {1.05, 0}, {1.15, 0}, {1.25, 0}}
	S := [][]float64{{0, 0}}
	Xf := append(append([][]float64{}, A...), append(append([][]float64{}, S...), B...)...)
	Xr := append(append([][]float64{}, B...), append(append([][]float64{}, S...), A...)...)
	lf, _ := dbscan(Xf, 1.0, 4, nil)
	lr, _ := dbscan(Xr, 1.0, 4, nil)
	check("C1 先访问 A:共享点归 A", lf[4] == lf[0], fmt.Sprintf("共享=%d A0=%d", lf[4], lf[0]))
	check("C2 先访问 B:共享点归 B(结果依赖访问顺序)", lr[4] == lr[0] && lr[0] != lr[8],
		fmt.Sprintf("共享=%d B0=%d A0=%d", lr[4], lr[0], lr[8]))

	// D. NOISE 可被改写为边界点
	Xd := [][]float64{{5}, {0}, {0.4}, {0.8}}
	ld, _ := dbscan(Xd, 0.6, 3, nil)
	check("D1 孤立点保持噪声", ld[0] == -1, fmt.Sprintf("%v", ld))
	Xe := [][]float64{{0.9}, {0}, {0.4}, {0.8}}
	le, _ := dbscan(Xe, 0.6, 3, nil)
	check("D2 核心点邻域内的先访问点被收编为边界点", le[0] >= 0 && le[0] == le[1], fmt.Sprintf("%v", le))

	// E. 每簇至少 MinPts 个点
	X, _ := blobs(40, 5, 6)
	okE := true
	for _, mp := range []int{2, 3, 4, 5} {
		lab, _ := dbscan(X, 1.2, mp, nil)
		sizes := map[int]int{}
		for _, l := range lab {
			if l >= 0 {
				sizes[l]++
			}
		}
		if len(sizes) != 2 {
			okE = false
		}
		for _, v := range sizes {
			if v < mp {
				okE = false
			}
		}
	}
	check("E 每个簇的规模都 ≥ MinPts(2/3/4/5 全测)", okE, "")

	// F. 参数敏感性
	lbase, _ := dbscan(X, 1.2, 4, nil)
	check("F1 合适 eps 下噪声数 == 6", countNoise(lbase) == 6, fmt.Sprintf("%d", countNoise(lbase)))
	lbig, _ := dbscan(X, 12.0, 4, nil)
	check("F2 eps 过大 → 两簇合并", countLabel(lbig, lbig[0]) == 80, fmt.Sprintf("%d", countLabel(lbig, lbig[0])))
	check("F3 eps 过大时部分噪声点自己抱团成簇", countNoise(lbig) < 6, fmt.Sprintf("噪声=%d", countNoise(lbig)))
	_, c4 := dbscan(X, 1.2, 4, nil)
	_, c25 := dbscan(X, 1.2, 25, nil)
	check("F4 MinPts 4→25 核心点减少", len(c25) < len(c4), fmt.Sprintf("%d → %d", len(c4), len(c25)))

	// G. 每点至多一次 region query
	stats := 0
	dbscan(X[:60], 1.2, 4, &stats)
	check("G1 region query 次数 ≤ n", stats <= 60, fmt.Sprintf("%d / n=60", stats))

	// H. 任意形状
	Xr2, tr := rings(90, 9)
	lring, _ := dbscan(Xr2, 0.4, 3, nil)
	lkm := kmeans(Xr2, 2, 1)
	check("H1 DBSCAN 完美分离同心圆(ARI==1)", math.Abs(ari(tr, lring)-1) < 1e-9,
		fmt.Sprintf("%.4f", ari(tr, lring)))
	check("H2 k-means 近乎随机(ARI < 0.2)", ari(tr, lkm) < 0.2, fmt.Sprintf("%.4f", ari(tr, lkm)))

	// I. k-dist 启发式
	kd := kdist(X, 4)
	inner, nois := 0.0, math.Inf(1)
	for i, v := range kd {
		if i < 80 {
			if v > inner {
				inner = v
			}
		} else if v < nois {
			nois = v
		}
	}
	check("I1 簇内 4-dist 显著小于孤立点", inner < nois, fmt.Sprintf("%.3f vs %.3f", inner, nois))
	check("I2 k-dist 距离 d 的 d-邻域 ≥ k+1 个点", len(regionQuery(X, 0, kd[0], nil)) >= 5,
		fmt.Sprintf("%d", len(regionQuery(X, 0, kd[0], nil))))

	fmt.Println("--------------------------------------------------------------------------")
	fmt.Printf("断言 %d/%d 通过\n", passed, total)
	if len(fails) > 0 {
		fmt.Println("失败项:", fails)
		os.Exit(1)
	}
	fmt.Println("全部通过")
}

func countNoise(lab []int) int {
	c := 0
	for _, l := range lab {
		if l == -1 {
			c++
		}
	}
	return c
}

func countLabel(lab []int, target int) int {
	c := 0
	for _, l := range lab {
		if l == target {
			c++
		}
	}
	return c
}
