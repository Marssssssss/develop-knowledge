// ViT 编码器与参数统计:LayerNorm、单头自注意力、MLP、Pre-LN 残差块、参数量与算力。
//
// 论文式 (1) z_0 = [x_class; x_p^1 E; …; x_p^N E] + E_pos
// 式 (2) z'_ℓ = MSA(LN(z_{ℓ-1})) + z_{ℓ-1}
// 式 (3) z_ℓ   = MLP(LN(z'_ℓ)) + z'_ℓ
// 式 (4) y     = LN(z_L^0)(取 [class] token 的输出做分类)
// 即 Pre-LN 残差结构;本文件实现单头、单层版本,便于把结构性质逐条断言。
package main

import "math"

// LayerNorm 对单个 token 归一化;gamma/beta 为 nil 时只做归一化。
func LayerNorm(x, gamma, beta []float64, eps float64) []float64 {
	n := len(x)
	mean := 0.0
	for _, v := range x {
		mean += v
	}
	mean /= float64(n)
	variance := 0.0
	for _, v := range x {
		variance += (v - mean) * (v - mean)
	}
	variance /= float64(n)
	inv := 1.0 / math.Sqrt(variance+eps)
	out := make([]float64, n)
	for i, v := range x {
		out[i] = (v - mean) * inv
		if gamma != nil {
			out[i] = out[i]*gamma[i] + beta[i]
		}
	}
	return out
}

// GELU GELU 的 tanh 近似(Hendrycks & Gimpel 原式;ViT 的 MLP 用 GELU)。
func GELU(x float64) float64 {
	return 0.5 * x * (1.0 + math.Tanh(math.Sqrt(2.0/math.Pi)*(x+0.044715*x*x*x)))
}

// SoftmaxRow 按行 softmax(数值稳定:先减最大值)。
func SoftmaxRow(v []float64) []float64 {
	m := v[0]
	for _, x := range v[1:] {
		if x > m {
			m = x
		}
	}
	ex := make([]float64, len(v))
	s := 0.0
	for i, x := range v {
		ex[i] = math.Exp(x - m)
		s += ex[i]
	}
	for i := range ex {
		ex[i] /= s
	}
	return ex
}

// Attention 单头自注意力:softmax(QKᵀ/√d)·V。
func Attention(tokens, wq, wk, wv [][]float64, d int) [][]float64 {
	n := len(tokens)
	q := make([][]float64, n)
	k := make([][]float64, n)
	v := make([][]float64, n)
	for i, t := range tokens {
		q[i] = Linear(t, wq)
		k[i] = Linear(t, wk)
		v[i] = Linear(t, wv)
	}
	scale := math.Sqrt(float64(d))
	out := make([][]float64, n)
	for i := 0; i < n; i++ {
		scores := make([]float64, n)
		for j := 0; j < n; j++ {
			s := 0.0
			for c := range q[i] {
				s += q[i][c] * k[j][c]
			}
			scores[j] = s / scale
		}
		probs := SoftmaxRow(scores)
		row := make([]float64, len(v[0]))
		for c := range row {
			s := 0.0
			for j := 0; j < n; j++ {
				s += probs[j] * v[j][c]
			}
			row[c] = s
		}
		out[i] = row
	}
	return out
}

// MLPBlock 两层 MLP + GELU(论文里的 MLP 子层)。
func MLPBlock(x []float64, w1 [][]float64, b1 []float64, w2 [][]float64, b2 []float64) []float64 {
	h := Linear(x, w1)
	for i := range h {
		h[i] = GELU(h[i] + b1[i])
	}
	out := Linear(h, w2)
	for i := range out {
		out[i] += b2[i]
	}
	return out
}

// BlockParams 一层编码器的全部参数,字段顺序与 Python 侧元组一致。
type BlockParams struct {
	Wq, Wk, Wv [][]float64
	W1         [][]float64
	B1         []float64
	W2         [][]float64
	B2, G1, Be1, G2, Be2 []float64
}

// TransformerBlock 论文式 (2)(3):z' = MSA(LN(z)) + z;z = MLP(LN(z')) + z'。
func TransformerBlock(tokens [][]float64, p BlockParams) [][]float64 {
	d := len(tokens[0])
	nz := make([][]float64, len(tokens))
	for i, t := range tokens {
		nz[i] = LayerNorm(t, p.G1, p.Be1, LN_EPS)
	}
	att := Attention(nz, p.Wq, p.Wk, p.Wv, d)
	z1 := make([][]float64, len(tokens))
	for i, t := range tokens {
		row := make([]float64, len(t))
		for j := range t {
			row[j] = t[j] + att[i][j]
		}
		z1[i] = row
	}
	out := make([][]float64, len(tokens))
	for i, t := range z1 {
		normed := LayerNorm(t, p.G2, p.Be2, LN_EPS)
		m := MLPBlock(normed, p.W1, p.B1, p.W2, p.B2)
		row := make([]float64, len(t))
		for j := range t {
			row[j] = t[j] + m[j]
		}
		out[i] = row
	}
	return out
}

// RandomBlockParams 生成一套确定性参数,形状与 ViT 的一个编码器层一致。
func RandomBlockParams(d, hidden int, seed int64) BlockParams {
	return BlockParams{
		Wq: LCGMatrix(d, d, seed, -0.5, 0.5),
		Wk: LCGMatrix(d, d, seed+1, -0.5, 0.5),
		Wv: LCGMatrix(d, d, seed+2, -0.5, 0.5),
		W1: LCGMatrix(hidden, d, seed+3, -0.5, 0.5),
		B1: LCGVector(hidden, seed+4, -0.1, 0.1),
		W2: LCGMatrix(d, hidden, seed+5, -0.5, 0.5),
		B2: LCGVector(d, seed+6, -0.1, 0.1),
		G1: LCGVector(d, seed+7, 0.9, 1.1),
		Be1: LCGVector(d, seed+8, -0.1, 0.1),
		G2: LCGVector(d, seed+9, 0.9, 1.1),
		Be2: LCGVector(d, seed+10, -0.1, 0.1),
	}
}

// EncoderParams 一层 = 注意力(4D²+4D) + MLP(2·4D² + 4D + D) + 两个 LN(各 2D)。
func EncoderParams(d, layers int, mlpRatio float64) int {
	attn := 4*d*d + 4*d
	hidden := int(float64(d) * mlpRatio)
	mlp := 2*d*hidden + hidden + d
	ln := 2 * (2 * d)
	return layers * (attn + mlp + ln)
}

// ViTBaseParams ViT-Base(12 层、D=768、MLP 隐层 3072、1000 类)的总参数量估算。
//
// 论文 Table 1 给出 ViT-Base 是 86M;这里按"patch 嵌入(无 bias)+ 位置编码 + 12 层编码器 +
// 末尾 LN + 单层分类头(有 bias)"逐项相加,用来核对那个 86M 是怎么来的。
func ViTBaseParams(patch, channels, dim, layers, classes int) int {
	n := NPatch(Image, Image, patch, 0)
	total := PatchEmbedParams(patch, channels, dim)
	total += PosEmbedParams(TokensTotal(n, true), dim)
	total += EncoderParams(dim, layers, 4.0)
	total += 2 * dim
	total += dim*classes + classes
	return total
}

// AttentionOps 单个头一层注意力里 QKᵀ 那一项的乘加量(N²D)。
func AttentionOps(nTokens, d int) int { return nTokens * nTokens * d }

// CNNLayersForGlobalRF 3×3 卷积堆到覆盖整幅图需要多少层(stride=1 时感受野 = 1 + 2L)。
func CNNLayersForGlobalRF(size, kernel int) int {
	span := kernel - 1
	l, rf := 0, 1
	for rf < size {
		rf += span
		l++
	}
	return l
}
