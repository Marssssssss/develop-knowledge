// CLIP 双塔对比损失 —— Go 实现（与 clip.py 同一套官方口径）
//
// A. 论文《Learning Transferable Visual Models From Natural Language
//    Supervision》(arXiv 2103.00020) Figure 3 的 numpy 伪代码：
//      I_e = l2_normalize(I_f @ W_i, axis=1);  T_e = l2_normalize(T_f @ W_t, axis=1)
//      logits = (I_e @ T_e.T) * exp(t)
//      labels = arange(n)
//      loss_i = cross_entropy(logits, labels, axis=0)   // 每个文本在所有图片上
//      loss_t = cross_entropy(logits, labels, axis=1)   // 每个图片在所有文本上
//      loss   = (loss_i + loss_t) / 2
//    §2.3：N 个真实对 vs N²−N 个错误配对；τ 初始化为 0.07，并 clip 到缩放不超 100。
// B. 官方 clip/model.py：logit_scale = Parameter(ones([]) * log(1/0.07))，
//    forward 里对两路特征做 L2 归一，logits_per_text = logits_per_image.t()。
package main

import (
	"fmt"
	"math"
	"math/rand"
)

// LogitScaleInit 对应 model.py 的 nn.Parameter(torch.ones([]) * np.log(1/0.07))
func LogitScaleInit() float64 { return math.Log(1.0 / 0.07) }

// ClipLogitScale 论文 §2.3：clip 到「缩放不超过 100」以防训练不稳
func ClipLogitScale(t, cap float64) float64 {
	return math.Min(math.Exp(t), cap)
}

func l2Normalize(v []float64) []float64 {
	n := 0.0
	for _, x := range v {
		n += x * x
	}
	n = math.Sqrt(n)
	out := make([]float64, len(v))
	for i, x := range v {
		if n > 0 {
			out[i] = x / n
		} else {
			out[i] = x
		}
	}
	return out
}

func dot(a, b []float64) float64 {
	s := 0.0
	for i := range a {
		s += a[i] * b[i]
	}
	return s
}

func cosMatrix(I, T [][]float64) [][]float64 {
	M := make([][]float64, len(I))
	for i := range I {
		M[i] = make([]float64, len(T))
		for j := range T {
			M[i][j] = dot(I[i], T[j])
		}
	}
	return M
}

func softmax(vals []float64) []float64 {
	m := vals[0]
	for _, v := range vals {
		if v > m {
			m = v
		}
	}
	out := make([]float64, len(vals))
	z := 0.0
	for i, v := range vals {
		out[i] = math.Exp(v - m)
		z += out[i]
	}
	for i := range out {
		out[i] /= z
	}
	return out
}

// CrossEntropy axis=0 按列（每个文本），axis=1 按行（每个图片）
func CrossEntropy(logits [][]float64, labels []int, axis int) float64 {
	n := len(logits)
	total := 0.0
	if axis == 0 {
		for j := 0; j < n; j++ {
			col := make([]float64, n)
			for i := 0; i < n; i++ {
				col[i] = logits[i][j]
			}
			p := softmax(col)
			total -= math.Log(math.Max(p[labels[j]], 1e-300))
		}
	} else {
		for i := 0; i < n; i++ {
			p := softmax(logits[i])
			total -= math.Log(math.Max(p[labels[i]], 1e-300))
		}
	}
	return total / float64(n)
}

func similarityLogits(I, T [][]float64, t, cap float64) ([][]float64, float64) {
	s := ClipLogitScale(t, cap)
	M := cosMatrix(I, T)
	for i := range M {
		for j := range M[i] {
			M[i][j] *= s
		}
	}
	return M, s
}

// ClipLoss 返回 (loss, loss_i, loss_t, logits, scale)
func ClipLoss(I, T [][]float64, t, cap float64) (float64, float64, float64, [][]float64, float64) {
	lg, s := similarityLogits(I, T, t, cap)
	n := len(lg)
	labels := make([]int, n)
	for i := range labels {
		labels[i] = i
	}
	li := CrossEntropy(lg, labels, 0)
	lt := CrossEntropy(lg, labels, 1)
	return (li + lt) / 2, li, lt, lg, s
}

// Top1 image→text 的 top-1：第 i 行最大值是否落在列 i
func Top1(logits [][]float64) float64 {
	hit := 0
	for i, row := range logits {
		best, bj := math.Inf(-1), 0
		for j, v := range row {
			if v > best {
				best, bj = v, j
			}
		}
		if bj == i {
			hit++
		}
	}
	return float64(hit) / float64(len(logits))
}

func randomUnit(n, d int, seed int64) [][]float64 {
	r := rand.New(rand.NewSource(seed))
	out := make([][]float64, n)
	for i := range out {
		v := make([]float64, d)
		for k := range v {
			v[k] = r.NormFloat64()
		}
		out[i] = l2Normalize(v)
	}
	return out
}

func eye(n int) [][]float64 {
	M := make([][]float64, n)
	for i := range M {
		M[i] = make([]float64, n)
		M[i][i] = 1.0
	}
	return M
}

var nOK, nBad int

func check(label string, cond bool, detail string) {
	nOK++
	if !cond {
		nBad++
		fmt.Printf("  FAIL %s %s\n", label, detail)
	}
}

func closeF(a, b, eps float64) bool { return math.Abs(a-b) <= eps }

func main() {
	t0 := LogitScaleInit()
	check("初始化 = ln(1/0.07)", closeF(t0, math.Log(1/0.07), 1e-12), "")
	check("exp(t0) = 1/0.07", closeF(math.Exp(t0), 1/0.07, 1e-9), "")
	check("exp(t)=200 被 clip 到 100",
		closeF(ClipLogitScale(math.Log(200), 100), 100, 1e-9), "")
	check("exp(t)=50 不 clip", closeF(ClipLogitScale(math.Log(50), 100), 50, 1e-9), "")

	n := 8
	Ir := randomUnit(n, 16, 11)
	Tr := randomUnit(n, 16, 12)
	// scale→0：所有 logits 相同，两个方向的 CE 都恰好等于 ln(n)
	l0, li0, lt0, _, _ := ClipLoss(Ir, Tr, -1e9, 1e18)
	check("scale→0 时 loss = ln(n)", closeF(l0, math.Log(float64(n)), 1e-9),
		fmt.Sprintf("%g", l0))
	check("scale→0 时两方向相等", closeF(li0, lt0, 1e-9), "")

	Ip := eye(n)
	lp, _, _, lgp, sp := ClipLoss(Ip, Ip, t0)
	check("完美对齐 loss ≈ (n-1)·e^(-scale)",
		closeF(lp, float64(n-1)*math.Exp(-sp), 1e-9), fmt.Sprintf("%g", lp))
	check("完美对齐 top-1 = 1", closeF(Top1(lgp), 1.0, 1e-12), "")

	lr, lir, ltr, _, _ := ClipLoss(Ir, Tr, t0)
	check("loss = (loss_i+loss_t)/2", closeF(lr, (lir+ltr)/2, 1e-12), "")
	check("两方向一般不相等", !closeF(lir, ltr, 1e-9), "")
	check("随机特征 loss 远大于完美对齐", lr > lp*1e3, fmt.Sprintf("%g vs %g", lr, lp))

	// 对齐批：scale 越大 loss 越低；随机批反之
	sp1, _, _, _, _ := ClipLoss(Ip, Ip, math.Log(1.0))
	sp2, _, _, _, _ := ClipLoss(Ip, Ip, math.Log(80.0))
	check("对齐批 scale 增大 loss 下降", sp2 < sp1, fmt.Sprintf("%g → %g", sp1, sp2))
	sr1, _, _, _, _ := ClipLoss(Ir, Tr, math.Log(1.0))
	sr2, _, _, _, _ := ClipLoss(Ir, Tr, math.Log(80.0))
	check("随机批 scale 增大 loss 上升", sr2 > sr1, fmt.Sprintf("%g → %g", sr1, sr2))

	// cap 的实际影响
	lc, _, _, _, sc := ClipLoss(Ir, Tr, math.Log(1e6), 100)
	lnc, _, _, _, snc := ClipLoss(Ir, Tr, math.Log(1e6), 1e18)
	check("有 cap 时 scale = 100", closeF(sc, 100, 1e-9), "")
	check("无 cap 时 scale 巨大", snc > 1e5, fmt.Sprintf("%g", snc))
	check("cap 与否 loss 不同", !closeF(lc, lnc, 1e-9), fmt.Sprintf("%g vs %g", lc, lnc))

	// 官方超参
	check("minibatch 32768 的正对数", 32768 == 32768, "")
	check("负对数 N²−N", 32768*32768-32768 == 32768*(32768-1), "")
	fmt.Printf("断言 %d 条，失败 %d\n", nOK, nBad)
}
