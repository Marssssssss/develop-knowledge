// ViT 图像分块:patchify、patch embedding 与 Conv2d 的等价性、位置编码几何。
//
// 权威口径:A.Dosovitskiy et al.《An Image is Worth 16x16 Words: Transformers for Image
// Recognition at Scale》(arXiv:2010.11929)。对应论文的表述:
//   - §3.1 "we reshape the image into a sequence of flattened 2D patches",N = H·W / P²
//     —— 224×224、P=16 得 N = 196;
//   - "map to D dimensions with a trainable linear projection"(即 patch embedding);
//   - "prepend a learnable embedding ... ([class] token)";
//   - "add position embeddings ... standard learnable 1D position embeddings ...
//     We did not observe performance gains from using more advanced 2D-aware position
//     embeddings"。
//
// 图像用 HWC 嵌套表示,与 Python 侧一致。不训练任何模型。
package main

// Patch 论文标题里的 16x16。
const Patch = 16

// Image 论文的标准输入分辨率。
const Image = 224

// Channels RGB。
const Channels = 3

// Latent ViT-Base 的隐层维度 D。
const Latent = 768

// LN_EPS LayerNorm 的数值稳定项。
const LN_EPS = 1e-5

// PatchGrid 返回 (行数, 列数);stride <= 0 时默认等于 patch(不重叠分块)。
func PatchGrid(h, w, patch, stride int) (int, int) {
	if stride <= 0 {
		stride = patch
	}
	if h < patch || w < patch {
		return 0, 0
	}
	return (h-patch)/stride + 1, (w-patch)/stride + 1
}

// NPatch 论文的 N = H·W / P²;非整除时向下取整,边缘像素被丢弃。
func NPatch(h, w, patch, stride int) int {
	rows, cols := PatchGrid(h, w, patch, stride)
	return rows * cols
}

// Patchify 按行优先展平 patch;每个 patch 的展平顺序是 (patch_y, patch_x, channel)。
func Patchify(img [][][]float64, patch, stride int) [][]float64 {
	if stride <= 0 {
		stride = patch
	}
	rows, cols := PatchGrid(len(img), len(img[0]), patch, stride)
	out := make([][]float64, 0, rows*cols)
	for ry := 0; ry < rows; ry++ {
		for rx := 0; rx < cols; rx++ {
			vec := make([]float64, 0, patch*patch*len(img[0][0]))
			for dy := 0; dy < patch; dy++ {
				for dx := 0; dx < patch; dx++ {
					vec = append(vec, img[ry*stride+dy][rx*stride+dx]...)
				}
			}
			out = append(out, vec)
		}
	}
	return out
}

// Unpatchify Patchify 的逆(构造用,便于验证搬运无损)。
func Unpatchify(patches [][]float64, h, w, patch, stride, channels int) [][][]float64 {
	if stride <= 0 {
		stride = patch
	}
	img := make([][][]float64, h)
	for y := range img {
		row := make([][]float64, w)
		for x := range row {
			row[x] = make([]float64, channels)
		}
		img[y] = row
	}
	_, cols := PatchGrid(h, w, patch, stride)
	for i, vec := range patches {
		ry, rx := i/cols, i%cols
		k := 0
		for dy := 0; dy < patch; dy++ {
			for dx := 0; dx < patch; dx++ {
				for c := 0; c < channels; c++ {
					img[ry*stride+dy][rx*stride+dx][c] = vec[k]
					k++
				}
			}
		}
	}
	return img
}

// PatchDim 一个 patch 展平后的长度 P²·C。
func PatchDim(patch, channels int) int { return patch * patch * channels }

// ShiftImage 整体平移图像(越界填 fill),用于检验分块的平移行为。
func ShiftImage(img [][][]float64, dx, dy int, fill float64) [][][]float64 {
	h, w, c := len(img), len(img[0]), len(img[0][0])
	out := make([][][]float64, h)
	for y := 0; y < h; y++ {
		row := make([][]float64, w)
		for x := 0; x < w; x++ {
			row[x] = make([]float64, c)
			sy, sx := y-dy, x-dx
			if sy >= 0 && sy < h && sx >= 0 && sx < w {
				copy(row[x], img[sy][sx])
			} else {
				for k := range row[x] {
					row[x][k] = fill
				}
			}
		}
		out[y] = row
	}
	return out
}

// PatchEmbedParams patch embedding 的参数量 = P²·C·D(线性投影,无 bias)。
func PatchEmbedParams(patch, channels, dim int) int {
	return PatchDim(patch, channels) * dim
}

// PosEmbedParams 1D 可学习位置编码参数量 = (N+1)·D。
func PosEmbedParams(nTokens, dim int) int { return nTokens * dim }

// TokensTotal 加上 [class] token 后的序列长度。
func TokensTotal(n int, withClass bool) int {
	if withClass {
		return n + 1
	}
	return n
}

// NeighbourPairs 行优先展平后,1D 相邻下标同时是空间水平相邻的对数,以及全部相邻对数。
func NeighbourPairs(rows, cols int) (int, int) {
	return rows * (cols - 1), rows*cols - 1
}

// VerticalIndexGap 行优先展平后,空间垂直相邻的 token 在 1D 序列里的距离 = W/P。
func VerticalIndexGap(cols int) int { return cols }

// LCGVector 确定性伪随机向量(避免依赖 math/rand,保证与 Python 侧逐位一致)。
func LCGVector(n int, seed int64, lo, hi float64) []float64 {
	out := make([]float64, n)
	state := seed
	for i := range out {
		state = (state*1103515245 + 12345) % (1 << 31)
		out[i] = lo + (hi-lo)*(float64(state)/float64(int64(1)<<31))
	}
	return out
}

// LCGMatrix 按行填出的确定性伪随机矩阵。
func LCGMatrix(rows, cols int, seed int64, lo, hi float64) [][]float64 {
	flat := LCGVector(rows*cols, seed, lo, hi)
	out := make([][]float64, rows)
	for i := range out {
		out[i] = flat[i*cols : (i+1)*cols]
	}
	return out
}

// Linear weight 为 [out_dim][in_dim]。
func Linear(vec []float64, weight [][]float64) []float64 {
	out := make([]float64, len(weight))
	for o, row := range weight {
		s := 0.0
		for i, w := range row {
			s += w * vec[i]
		}
		out[o] = s
	}
	return out
}

// LinearWeightToConv 把 [out_dim][P²·C] 的线性权重改排成 [out_dim][C][P][P] 的卷积核。
//
// 两者只是内存排布不同:线性层那一维的索引是 (dy·P + dx)·C + c。
func LinearWeightToConv(weight [][]float64, patch, channels int) [][][][]float64 {
	out := make([][][][]float64, len(weight))
	for o, row := range weight {
		kernel := make([][][]float64, channels)
		for c := range kernel {
			plane := make([][]float64, patch)
			for dy := range plane {
				plane[dy] = make([]float64, patch)
			}
			kernel[c] = plane
		}
		for dy := 0; dy < patch; dy++ {
			for dx := 0; dx < patch; dx++ {
				for c := 0; c < channels; c++ {
					kernel[c][dy][dx] = row[(dy*patch+dx)*channels+c]
				}
			}
		}
		out[o] = kernel
	}
	return out
}

// Conv2dAt 在 (ry, rx) 处对 HWC 图像做一次卷积输出;weight 为 [out_dim][C][P][P]。
//
// 这是"patch embedding 等价于 stride = patch 的 Conv2d"这条论文结论的卷积侧实现。
func Conv2dAt(img [][][]float64, weight [][][][]float64, ry, rx, patch, stride int) []float64 {
	ch := len(img[0][0])
	res := make([]float64, len(weight))
	for o, kernel := range weight {
		s := 0.0
		for dy := 0; dy < patch; dy++ {
			for dx := 0; dx < patch; dx++ {
				px := img[ry*stride+dy][rx*stride+dx]
				for c := 0; c < ch; c++ {
					s += kernel[c][dy][dx] * px[c]
				}
			}
		}
		res[o] = s
	}
	return res
}

// AddPos token 与位置编码逐元素相加(论文式 (1) 里的 "+ E_pos")。
func AddPos(tokens, pos [][]float64) [][]float64 {
	out := make([][]float64, len(tokens))
	for i, t := range tokens {
		row := make([]float64, len(t))
		for j := range t {
			row[j] = t[j] + pos[i][j]
		}
		out[i] = row
	}
	return out
}
