// t-SNE 读图实验(Go 版):簇大小无意义、随机噪声也能长出团块、迭代不足不可读。
// UMAP 对照见 ../python/umap_lite.py(Go 版只做 t-SNE 主干)。
//
// 编译运行:go run .
package main

import (
	"fmt"
	"math"
	"math/rand"
	"sort"
)

func centroid(P [][]float64, idx []int) []float64 {
	c := make([]float64, len(P[0]))
	for _, i := range idx {
		for d := range P[i] {
			c[d] += P[i][d]
		}
	}
	for d := range c {
		c[d] /= float64(len(idx))
	}
	return c
}

func rmsRadius(P [][]float64, idx []int) float64 {
	c := centroid(P, idx)
	s := 0.0
	for _, i := range idx {
		for d := 0; d < 2; d++ {
			s += (P[i][d] - c[d]) * (P[i][d] - c[d])
		}
	}
	return math.Sqrt(s / float64(len(idx)))
}

// nnDistances 每个点到最近邻的距离。
func nnDistances(P [][]float64) []float64 {
	out := make([]float64, len(P))
	for i := range P {
		best := math.Inf(1)
		for j := range P {
			if i == j {
				continue
			}
			d := math.Hypot(P[i][0]-P[j][0], P[i][1]-P[j][1])
			if d < best {
				best = d
			}
		}
		out[i] = best
	}
	return out
}

// tightFraction 近邻距离 < 0.5×中位数的点占比:抱团越多,值越大。
func tightFraction(P [][]float64) float64 {
	d := nnDistances(P)
	sort.Float64s(d)
	thr := 0.5 * d[len(d)/2]
	cnt := 0
	for _, x := range d {
		if x < thr {
			cnt++
		}
	}
	return float64(cnt) / float64(len(d))
}

func main() {
	dim := 20
	r1 := rand.New(rand.NewSource(1))
	X := append(gaussianCluster(40, dim, make([]float64, dim), 1.0, r1),
		gaussianCluster(160, dim, repeat(8.0, dim), 1.0, r1)...)
	Y, kl, _ := tSNE(X, 30.0, 250, 200.0, 3, map[int]bool{})
	rS := rmsRadius(Y, intRange(0, 40))
	rL := rmsRadius(Y, intRange(40, 200))
	fmt.Println("== 实验 1:簇的大小在图里没有意义 ==")
	fmt.Printf("  真实点数 40 vs 160(比值 4.00)\n")
	fmt.Printf("  嵌入 RMS 半径 %.2f vs %.2f → 比值 %.2f(KL=%.3f)\n", rS, rL, rL/rS, kl)
	fmt.Println("  结论:不能从 t-SNE 图里读簇的相对大小 —— 算法会按局部密度把大小簇抹平")
	if !(rL/rS < 2.0) {
		panic("cluster-size experiment unexpected")
	}

	r2 := rand.New(rand.NewSource(2))
	noise := gaussianCluster(150, 100, make([]float64, 100), 1.0, r2)
	fmt.Println("\n== 实验 2:纯随机噪声也会长出团块(100 维高斯,毫无结构) ==")
	fmt.Println("  perplexity   抱团点占比   近邻距离 p90/p10")
	tight2, tight30 := 0.0, 0.0
	for _, perp := range []float64{2.0, 5.0, 30.0, 100.0} {
		Py, _, _ := tSNE(noise, perp, 200, 200.0, 4, map[int]bool{})
		tf := tightFraction(Py)
		if perp == 2.0 {
			tight2 = tf
		}
		if perp == 100.0 {
			tight30 = tf
		}
		fmt.Printf("  %10.0f   %9.3f   %14.2f\n", perp, tf, percentileRatio(Py))
	}
	fmt.Println("  结论:perplexity 很小(相对点数)时,随机数据也会被画出一堆「团块」;")
	fmt.Println("        高 perplexity 下的均匀铺开才接近高维高斯的真相")
	if !(tight2 > tight30) {
		panic("noise-clump experiment unexpected")
	}

	r3 := rand.New(rand.NewSource(3))
	X3 := append(gaussianCluster(60, dim, make([]float64, dim), 1.0, r3),
		gaussianCluster(60, dim, repeat(12.0, dim), 1.0, r3)...)
	snaps := map[int]bool{10: true, 20: true, 60: true, 120: true, 600: true}
	_, _, S := tSNE(X3, 30.0, 600, 200.0, 7, snaps)
	fmt.Println("\n== 实验 3:迭代不足时簇尺度还在剧烈摆动(不可读图) ==")
	fmt.Println("  步数   簇A半径   簇B半径")
	for _, step := range []int{10, 20, 60, 120, 600} {
		fmt.Printf("  %6d   %7.2f   %7.2f\n", step,
			rmsRadius(S[step], intRange(0, 60)), rmsRadius(S[step], intRange(60, 120)))
	}
	fmt.Println("  结论:没有固定步数能保证稳定 —— 必须迭代到构型不再变化再读图")
}

func repeat(v float64, n int) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = v
	}
	return out
}

func intRange(a, b int) []int {
	out := make([]int, 0, b-a)
	for i := a; i < b; i++ {
		out = append(out, i)
	}
	return out
}

// percentileRatio 近邻距离的 p90/p10:越小说明点云铺得越均匀。
func percentileRatio(P [][]float64) float64 {
	d := nnDistances(P)
	sort.Float64s(d)
	n := len(d)
	hi := d[min(int(0.9*float64(n)), n-1)]
	lo := d[int(0.1*float64(n))]
	if lo == 0 {
		return math.Inf(1)
	}
	return hi / lo
}
