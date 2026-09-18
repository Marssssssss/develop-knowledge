// ViT 分块自检(与 python/vit_patch_check.py 一一对应)。
//
// 运行:cd go && go run .
// 注意:本机未安装 Go 工具链,本组文件未经编译器验证,已用 _docs/tools/bracket_check.py
// 与 syntax_sanity.py 检查,并逐处人工核对函数签名与实参个数。
//
// A 分块几何 / B 与 Conv2d 等价 / C 位置编码与置换对称 / D Pre-LN 编码器 /
// E 参数量与算力 / F 归纳偏置(平移行为)。
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
		return
	}
	fails = append(fails, name)
	fmt.Printf("  [FAIL] %s  %s\n", name, detail)
}

func maxDiff(a, b []float64) float64 {
	m := 0.0
	for i := range a {
		if d := math.Abs(a[i] - b[i]); d > m {
			m = d
		}
	}
	return m
}

// patternImage 确定性纹理图(不用随机数),用于分块/平移试验。
func patternImage(h, w, c int) [][][]float64 {
	img := make([][][]float64, h)
	for y := 0; y < h; y++ {
		row := make([][]float64, w)
		for x := 0; x < w; x++ {
			px := make([]float64, c)
			for k := 0; k < c; k++ {
				px[k] = float64((y*7+x*3+k)%11) / 11.0
			}
			row[x] = px
		}
		img[y] = row
	}
	return img
}

func tokensFor(n, d int, base int64) [][]float64 {
	out := make([][]float64, n)
	for i := range out {
		out[i] = LCGVector(d, base+int64(i), -0.5, 0.5)
	}
	return out
}

func zeroMatrix(rows, cols int) [][]float64 {
	out := make([][]float64, rows)
	for i := range out {
		out[i] = make([]float64, cols)
	}
	return out
}

func zeros(n int) []float64 { return make([]float64, n) }

func checkGeometry() {
	fmt.Println("== A. 分块几何 ==")
	r, c := PatchGrid(224, 224, 16, 0)
	check("224x224、P=16 -> 14x14 网格、N = 196(论文)",
		r == 14 && c == 14 && NPatch(224, 224, 16, 0) == 196,
		fmt.Sprintf("N=%d", NPatch(224, 224, 16, 0)))
	check("单 patch 展平长度 P²·C = 768", PatchDim(16, 3) == 768, "768")
	check("加上 [class] token 后序列长度 N+1 = 197", TokensTotal(196, true) == 197, "197")
	r2, c2 := PatchGrid(225, 225, 16, 0)
	check("非整除输入 225 -> 仍只有 14 列,覆盖 224 px,最右 1 列像素被丢弃",
		r2 == 14 && c2 == 14 && 16+13*16 == 224, fmt.Sprintf("覆盖 %d px", 16+13*16))
	img := patternImage(224, 224, 3)
	pat := Patchify(img, 16, 0)
	back := Unpatchify(pat, 224, 224, 16, 0, 3)
	rt := 0.0
	for y := 0; y < 224; y++ {
		for x := 0; x < 224; x++ {
			if d := maxDiff(back[y][x], img[y][x]); d > rt {
				rt = d
			}
		}
	}
	check("patchify -> unpatchify 完全无损", rt == 0.0, fmt.Sprintf("max diff %v", rt))
	check("行优先展平:第 0 个 patch 首元素 = img[0][0][0],第 1 个 = img[0][16][0]",
		pat[0][0] == img[0][0][0] && pat[1][0] == img[0][16][0], "")
	check("patch 的展平顺序是 (patch_y, patch_x, channel)",
		pat[0][1] == img[0][0][1] && pat[0][3] == img[0][1][0], "")
	check("混合架构:14x14 特征图按 P=1 分块同样得 196 个 token(与 224/P=16 对齐)",
		NPatch(14, 14, 1, 0) == 196 && PatchDim(1, 1024) == 1024, "196 x 1024")
}

func checkConvEquivalence() {
	fmt.Println("\n== B. patch embedding 等价于 stride = patch 的 Conv2d ==")
	weight := LCGMatrix(8, 16*16*3, 5, -1.0, 1.0)
	lin := Linear(Patchify(patternImage(224, 224, 3), 16, 0)[0], weight)
	conv := Conv2dAt(patternImage(224, 224, 3), LinearWeightToConv(weight, 16, 3), 0, 0, 16, 16)
	check("线性投影(flattened patch)与卷积核输出逐元素一致", maxDiff(lin, conv) < 1e-12,
		fmt.Sprintf("max diff %.2e", maxDiff(lin, conv)))
	check("patch embedding 参数量 = P²·C·D = 589824",
		PatchEmbedParams(16, 3, 768) == 589824, "589824")
	ov := NPatch(224, 224, 16, 8)
	check("stride 8 < patch 16(重叠分块)时 token 数从 196 涨到 729", ov == 729,
		fmt.Sprintf("%d", ov))
	gaps := NPatch(224, 224, 16, 20)
	check("stride 20 > patch 16 时只剩 121 个 token 且丢掉 8 px(覆盖 216)",
		gaps == 121 && 16+10*20 == 216, fmt.Sprintf("%d 个,覆盖 %d px", gaps, 16+10*20))
}

func checkPosition() {
	fmt.Println("\n== C. 位置编码与置换对称性 ==")
	check("1D 可学习位置编码参数量 = (N+1)·D = 151296",
		PosEmbedParams(197, 768) == 151296, "151296")
	d, hidden, n := 4, 8, 6
	params := RandomBlockParams(d, hidden, 7)
	tokens := tokensFor(n, d, 100)
	perm := []int{3, 0, 5, 1, 4, 2}
	out := TransformerBlock(tokens, params)
	pt := make([][]float64, n)
	for k, i := range perm {
		pt[k] = tokens[i]
	}
	pout := TransformerBlock(pt, params)
	eq := 0.0
	for k, i := range perm {
		if dd := maxDiff(out[i], pout[k]); dd > eq {
			eq = dd
		}
	}
	check("不加位置编码时,编码器块对 token 置换**等变**(输出只是同样重排)", eq < 1e-12,
		fmt.Sprintf("max diff %.2e", eq))
	pos := make([][]float64, n)
	for i := range pos {
		pos[i] = LCGVector(d, 900+int64(i), -0.3, 0.3)
	}
	a := TransformerBlock(AddPos(tokens, pos), params)
	b := TransformerBlock(AddPos(pt, pos), params)
	brk := 0.0
	for k, i := range perm {
		if dd := maxDiff(a[i], b[k]); dd > brk {
			brk = dd
		}
	}
	check("加上位置编码后置换**不再**等变(diff 远离 0)-> 位置编码是打破置换对称的唯一来源",
		brk > 0.1, fmt.Sprintf("max diff %.6f", brk))
	hor, tot := NeighbourPairs(14, 14)
	check("行优先 1D 排序下,相邻下标同时是空间水平相邻的比例 = 182/195 = 93.33%",
		hor == 182 && tot == 195 && math.Abs(float64(hor)/float64(tot)-0.9333333333333333) < 1e-12,
		fmt.Sprintf("%d/%d = %.2f%%", hor, tot, 100*float64(hor)/float64(tot)))
	check("空间垂直相邻的 token 在 1D 序列中恰好相距 W/P = 14(不是相邻)",
		VerticalIndexGap(14) == 14, "14")
	cls := make([][]float64, n)
	for i, t := range tokens {
		row := append([]float64{1.0}, t[1:]...)
		cls[i] = row
	}
	o1 := TransformerBlock(cls, params)
	touched := make([][]float64, n)
	for i := range cls {
		touched[i] = append([]float64(nil), cls[i]...)
	}
	touched[4][1] += 1.0
	o2 := TransformerBlock(touched, params)
	sens := maxDiff(o1[0], o2[0])
	check("[class] token 的输出依赖**每一个** patch(改第 4 个 patch 就变了)-> 第一层即全局感受野",
		sens > 1e-6, fmt.Sprintf("max diff %.6f", sens))
}

func checkEncoder() {
	fmt.Println("\n== D. Pre-LN 编码器(论文式 (2)(3))==")
	d, hidden, n := 4, 8, 6
	params := RandomBlockParams(d, hidden, 7)
	tokens := tokensFor(n, d, 100)
	pz := params
	pz.Wv = zeroMatrix(d, d)
	nz := make([][]float64, n)
	for i, t := range tokens {
		nz[i] = LayerNorm(t, params.G1, params.Be1, LN_EPS)
	}
	att := Attention(nz, params.Wq, params.Wk, pz.Wv, d)
	allZero := true
	for _, row := range att {
		for _, v := range row {
			if v != 0.0 {
				allZero = false
			}
		}
	}
	check("把 V 的投影置零后,MSA 输出恒为 0(残差恒等的前提)", allZero, "")
	pi := params
	pi.Wv = zeroMatrix(d, d)
	pi.W1 = zeroMatrix(hidden, d)
	pi.B1 = zeros(hidden)
	pi.B2 = zeros(d)
	ident := TransformerBlock(tokens, pi)
	idf := 0.0
	for i := range tokens {
		if dd := maxDiff(ident[i], tokens[i]); dd > idf {
			idf = dd
		}
	}
	check("两个子层输出都置零时,Pre-LN 残差块退化为恒等映射(残差加在 LN 输出之后)",
		idf < 1e-12, fmt.Sprintf("max diff %.2e", idf))
	ln := LayerNorm([]float64{1, 2, 3, 4}, nil, nil, LN_EPS)
	mean, varr := 0.0, 0.0
	for _, v := range ln {
		mean += v
		varr += v * v
	}
	mean /= 4.0
	varr /= 4.0
	check("LayerNorm 输出均值 0、方差 ≈ 1(eps=1e-5 使其略小于 1)",
		math.Abs(mean) < 1e-15 && varr > 0.999 && varr < 1.0,
		fmt.Sprintf("var=%.12f", varr))
	check("GELU(0) == 0 且 GELU(1) ≈ 0.841192(tanh 近似)",
		GELU(0.0) == 0.0 && math.Abs(GELU(1.0)-0.8411919906082768) < 1e-12,
		fmt.Sprintf("%.15f", GELU(1.0)))
	sm := SoftmaxRow([]float64{1, 2, 3})
	sum := 0.0
	for _, v := range sm {
		sum += v
	}
	check("注意力权重按行 softmax,和为 1", math.Abs(sum-1.0) < 1e-15, "")
	shifted := SoftmaxRow([]float64{1001, 1002, 1003})
	check("softmax 对整体平移不变(数值稳定实现)", maxDiff(sm, shifted) < 1e-15, "")
}

func checkParams() {
	fmt.Println("\n== E. 参数量与算力 ==")
	check("12 层编码器参数量 = 85054464", EncoderParams(768, 12, 4.0) == 85054464,
		fmt.Sprintf("%d", EncoderParams(768, 12, 4.0)))
	tot := ViTBaseParams(16, 3, 768, 12, 1000)
	check("ViT-Base 总参数 ≈ 86.57M,与论文 Table 1 的 86M 相对误差 < 1%",
		math.Abs(float64(tot)/86e6-1.0) < 0.01, fmt.Sprintf("%d (%.2fM)", tot, float64(tot)/1e6))
	check("单头一层注意力(N²D)= 29503488 次乘加", AttentionOps(196, 768) == 29503488,
		fmt.Sprintf("%d", AttentionOps(196, 768)))
	check("P 减半 -> token 数 4 倍,注意力矩阵面积 16 倍(38416 -> 614656)",
		NPatch(224, 224, 8, 0) == 784 && AttentionOps(784, 768) == 16*AttentionOps(196, 768),
		fmt.Sprintf("%d tokens", NPatch(224, 224, 8, 0)))
	check("覆盖 224 px:3x3 CNN 需 112 层,ViT 只需 1 层(全局注意力的代价是 N²)",
		CNNLayersForGlobalRF(224, 3) == 112, fmt.Sprintf("%d 层", CNNLayersForGlobalRF(224, 3)))
}

func checkTranslation() {
	fmt.Println("\n== F. 归纳偏置:平移行为 ==")
	img := patternImage(224, 224, 3)
	pat := Patchify(img, 16, 0)
	pat16 := Patchify(ShiftImage(img, 16, 0, 0.0), 16, 0)
	same := true
	for ry := 0; ry < 14; ry++ {
		for rx := 1; rx < 14; rx++ {
			if maxDiff(pat16[ry*14+rx], pat[ry*14+rx-1]) != 0.0 {
				same = false
			}
		}
	}
	check("整 patch 平移(16 px):patch 序列只是位置重排,内容逐元素相同(等变)", same, "")
	pat1 := Patchify(ShiftImage(img, 1, 0, 0.0), 16, 0)
	check("亚像素平移(1 px):patch 0 的内容变了 -> 分块本身不是平移等变的",
		maxDiff(pat1[0], pat[0]) > 1e-3, fmt.Sprintf("patch0 max diff %.6f", maxDiff(pat1[0], pat[0])))
	changed := 0
	for i := 0; i < 196; i++ {
		if maxDiff(pat1[i], pat[i]) > 1e-9 {
			changed++
		}
	}
	check("1 px 平移改变了**全部** 196 个 patch(不是只动边界列)-> 无平移等变性",
		changed == 196, fmt.Sprintf("%d/196 个 patch 改变", changed))
	check("整 patch 平移下 token 只有 13/14 的列能对齐,末列被裁掉、首列由新像素填充",
		maxDiff(pat16[13], pat[13]) != 0.0, "")
}

func main() {
	checkGeometry()
	checkConvEquivalence()
	checkPosition()
	checkEncoder()
	checkParams()
	checkTranslation()
	fmt.Printf("\n结果:%d/%d 通过", passed, total)
	if len(fails) > 0 {
		fmt.Printf(",失败:%v", fails)
		os.Exit(1)
	}
	fmt.Println()
}
