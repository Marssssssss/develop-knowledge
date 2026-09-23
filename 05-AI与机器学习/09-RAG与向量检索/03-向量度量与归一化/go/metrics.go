package main

// 向量度量空间与归一化 —— 按 hnswlib / faiss 官方定义转写。
//
// 依据（本轮实读）：
//   - hnswlib space_l2.h：`L2Sqr` 返回的是**平方**距离，不开根
//   - hnswlib space_ip.h：`InnerProductDistance = 1.0f - InnerProduct(...)`；
//     hnswlib **没有 cosine 空间**，要余弦就得自己先归一化再用 ip
//   - hnswlib space_ip.h::InnerProductSpace(size_t dim)：距离函数按 dim 整除性派发
//   - faiss MetricType.h：`METRIC_INNER_PRODUCT` 是 maximum inner product search，
//     `METRIC_L2` 是 squared L2 search

import (
	"fmt"
	"math"
	"sort"
)

// L2Sqr 对应 hnswlib `L2Sqr`：Σ(a_i-b_i)²，**不开根**。
// 刻意用朴素累加，对应 C++ 的 for 循环。
func L2Sqr(a, b []float64) float64 {
	var s float64
	for i := range a {
		t := a[i] - b[i]
		s += t * t
	}
	return s
}

// L2 真正的欧氏距离（仅供对照，hnswlib 不用它）。
func L2(a, b []float64) float64 { return math.Sqrt(L2Sqr(a, b)) }

// InnerProduct 对应 hnswlib `InnerProduct`。
func InnerProduct(a, b []float64) float64 {
	var s float64
	for i := range a {
		s += a[i] * b[i]
	}
	return s
}

// InnerProductDistance 对应 `1.0f - InnerProduct(a, b)`；可以为负，且不是余弦距离。
func InnerProductDistance(a, b []float64) float64 {
	return 1.0 - InnerProduct(a, b)
}

// Norm L2 模长。
func Norm(v []float64) float64 { return math.Sqrt(InnerProduct(v, v)) }

// Normalize L2 归一化；零向量会 panic。
func Normalize(v []float64) []float64 {
	n := Norm(v)
	if n == 0.0 {
		panic("cannot normalize the zero vector")
	}
	out := make([]float64, len(v))
	for i := range v {
		out[i] = v[i] / n
	}
	return out
}

// Cosine 余弦相似度。
func Cosine(a, b []float64) float64 {
	na, nb := Norm(a), Norm(b)
	if na == 0.0 || nb == 0.0 {
		panic("cosine undefined for zero vector")
	}
	return InnerProduct(a, b) / (na * nb)
}

// SimdRoute 复刻 `InnerProductSpace(size_t dim)` 的派发顺序。
func SimdRoute(dim int) string {
	switch {
	case dim%16 == 0:
		return "SIMD16Ext"
	case dim%4 == 0:
		return "SIMD4Ext"
	case dim > 16:
		return "SIMD16ExtResiduals"
	case dim > 4:
		return "SIMD4ExtResiduals"
	default:
		return "scalar"
	}
}

// BlockedSum 按 width 宽度分块后横向归约。
func BlockedSum(a, b []float64, width int) float64 {
	var total float64
	for base := 0; base < len(a); base += width {
		var part float64
		for i := base; i < base+width && i < len(a); i++ {
			part += a[i] * b[i]
		}
		total += part
	}
	return total
}

// ResidualSum 先按 width 吃到整数倍，残差用标量补齐。
func ResidualSum(a, b []float64, width int) float64 {
	blocks := (len(a) / width) * width
	total := BlockedSum(a[:blocks], b[:blocks], width)
	for i := blocks; i < len(a); i++ {
		total += a[i] * b[i]
	}
	return total
}

// Item 是排序后的一个 (分数, 下标) 对。
type Item struct {
	Score float64
	Node  int
}

// RankByL2 按 L2 平方距离升序。
func RankByL2(q []float64, vs [][]float64) []int {
	it := make([]Item, len(vs))
	for i, v := range vs {
		it[i] = Item{L2Sqr(q, v), i}
	}
	sort.Slice(it, func(a, b int) bool {
		if it[a].Score != it[b].Score {
			return it[a].Score < it[b].Score
		}
		return it[a].Node < it[b].Node
	})
	out := make([]int, len(it))
	for i, x := range it {
		out[i] = x.Node
	}
	return out
}

// RankByIP 按内积降序（faiss METRIC_INNER_PRODUCT）。
func RankByIP(q []float64, vs [][]float64) []int {
	it := make([]Item, len(vs))
	for i, v := range vs {
		it[i] = Item{InnerProduct(q, v), i}
	}
	sort.Slice(it, func(a, b int) bool {
		if it[a].Score != it[b].Score {
			return it[a].Score > it[b].Score
		}
		return it[a].Node < it[b].Node
	})
	out := make([]int, len(it))
	for i, x := range it {
		out[i] = x.Node
	}
	return out
}

// RankByCosine 按余弦降序。
func RankByCosine(q []float64, vs [][]float64) []int {
	it := make([]Item, len(vs))
	for i, v := range vs {
		it[i] = Item{Cosine(q, v), i}
	}
	sort.Slice(it, func(a, b int) bool {
		if it[a].Score != it[b].Score {
			return it[a].Score > it[b].Score
		}
		return it[a].Node < it[b].Node
	})
	out := make([]int, len(it))
	for i, x := range it {
		out[i] = x.Node
	}
	return out
}

// NaiveAccumulate 朴素累加（对照编程语言自带 sum 的补偿求和）。
func NaiveAccumulate(vals []float64) float64 {
	var s float64
	for _, v := range vals {
		s += v
	}
	return s
}

// FromCosine 用余弦反推 L2 平方距离（仅单位向量成立）：2 - 2cos。
// FromCosine 用余弦反推 L2 平方距离（仅单位向量成立）：2 - 2cos。
func FromCosine(a, b []float64) float64 { return 2.0 - 2.0*Cosine(a, b) }

func lcgPoints(n, dim, seed int) [][]float64 {
	x := seed & 0x7FFFFFFF
	pts := make([][]float64, n)
	for i := 0; i < n; i++ {
		v := make([]float64, dim)
		for d := 0; d < dim; d++ {
			x = (1103515245*x + 12345) & 0x7FFFFFFF
			v[d] = float64(x%100000) / 100000.0
		}
		pts[i] = v
	}
	return pts
}

func main() {
	raw := lcgPoints(40, 6, 11)
	unit := make([][]float64, len(raw))
	for i, v := range raw {
		unit[i] = Normalize(v)
	}
	qRaw := lcgPoints(1, 6, 12)[0]
	qUnit := Normalize(qRaw)

	println("== 归一化前后：三种度量的 top-5 是否一致 ==")
	for _, c := range []struct {
		tag  string
		pts  [][]float64
		q    []float64
	}{{"未归一化", raw, qRaw}, {"已归一化", unit, qUnit}} {
		rl := RankByL2(c.q, c.pts)[:5]
		ri := RankByIP(c.q, c.pts)[:5]
		rc := RankByCosine(c.q, c.pts)[:5]
		same := true
		for i := range rl {
			if rl[i] != ri[i] || rl[i] != rc[i] {
				same = false
			}
		}
		println("  " + c.tag + "  L2  top5 = " + fmtInts(rl))
		println("           IP  top5 = " + fmtInts(ri))
		println("           COS top5 = " + fmtInts(rc) + "   三者一致 = " + boolStr(same))
	}

	println("\n== InnerProductDistance = 1 - IP（可为负） ==")
	println("  正交      距离 = " + f2s(InnerProductDistance([]float64{1, 0}, []float64{0, 1})))
	println("  同向 长度2 距离 = " + f2s(InnerProductDistance([]float64{2, 0}, []float64{1, 0})))

	println("\n== SIMD 派发：由 dim 的整除性决定 ==")
	for _, dim := range []int{3, 4, 5, 7, 8, 12, 16, 17, 20, 32, 64} {
		println("  dim=" + itoa(dim) + " -> " + SimdRoute(dim))
	}

	println("\n== 求和顺序的代价（同一份数据） ==")
	cat := []float64{1e16, 1.0, 1.0, 1.0, -1e16, 1.0, 1.0, 1.0}
	ones := []float64{1, 1, 1, 1, 1, 1, 1, 1}
	println("  朴素累加(C++ 标量) " + f2s(InnerProduct(cat, ones)))
	println("  SIMD4  分块       " + f2s(BlockedSum(cat, ones, 4)))
	println("  SIMD16 分块       " + f2s(BlockedSum(cat, ones, 16)))

	println("\n== 单位向量恒等式 ||u-w||^2 = 2 - 2cos ==")
	u, w := Normalize([]float64{1, 2, 3}), Normalize([]float64{3, 1, 2})
	println("  ||u-w||^2 = " + f2s(L2Sqr(u, w)))
	println("  2-2<u,w>  = " + f2s(2.0-2.0*InnerProduct(u, w)))
	println("  2-2cos    = " + f2s(FromCosine(u, w)))
}

func f2s(f float64) string  { return fmt.Sprintf("%.4f", f) }
func itoa(i int) string     { return fmt.Sprintf("%3d", i) }

func fmtInts(a []int) string {
	out := "["
	for i, v := range a {
		if i > 0 {
			out += " "
		}
		out += fmt.Sprintf("%d", v)
	}
	return out + "]"
}

func boolStr(b bool) string {
	if b {
		return "true"
	}
	return "false"
}
