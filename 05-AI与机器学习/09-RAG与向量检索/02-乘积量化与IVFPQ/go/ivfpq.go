package main

// IVF-PQ 倒排索引 —— 按 faiss 官方 IndexIVF / IndexIVFPQ 转写。
//
// 参见：
//   - faiss/IndexIVFPQ.cpp::IndexIVFPQ(...) 的 code_size / by_residual / nprobe 默认值
//   - faiss/IndexIVFPQ.cpp::initialize_IVFPQ_precomputed_table
//   - faiss/IndexIVFPQ.cpp::train_encoder_num_vectors
//   - faiss/IndexIVF.cpp 的 `cur_nprobe = std::min(nlist, params ? ... : this->nprobe)`
//   - faiss/IndexIVF.h 的 `size_t nprobe = 1;`

import "sort"

// PrecomputedTableMaxBytes 对应 `size_t precomputed_table_max_bytes = ((size_t)1) << 31;`
const PrecomputedTableMaxBytes = 1 << 31

// ClusteringParameters 的默认值。
const (
	MaxPointsPerCentroid = 256
	MinPointsPerCentroid = 39
	Niter                = 25
)

// Item 是检索结果里的一个 (距离, 结点) 对。
type Item struct {
	Dist float64
	Node int
}

// coarseDist 是粗质心的一次打分。
type coarseDist struct {
	idx int
	d   float64
}

// IndexIVFPQ 对应 faiss::IndexIVFPQ。
type IndexIVFPQ struct {
	D, Nlist      int
	Metric        string
	PQ            *ProductQuantizer
	CodeSize      int
	ByResidual    bool
	UsePrecompTbl int
	Nprobe        int
	Coarse        [][]float64
	Lists         [][]int
	Codes         [][][]int
	Vectors       [][]float64
	TableSize     int64
}

// NewIndexIVFPQ 构造；by_residual 默认 true（官方 IndexIVFPQ 构造函数里写死）。
func NewIndexIVFPQ(d, nlist, m, nbits int, metric string, byResidual bool) *IndexIVFPQ {
	pq := NewPQ(d, m, nbits)
	return &IndexIVFPQ{
		D: d, Nlist: nlist, Metric: metric, PQ: pq, CodeSize: pq.CodeSize,
		ByResidual: byResidual, UsePrecompTbl: 0, Nprobe: 1,
	}
}

// TrainEncoderNumVectors 对应 `pq.cp.max_points_per_centroid * pq.ksub`。
func (ix *IndexIVFPQ) TrainEncoderNumVectors() int {
	return MaxPointsPerCentroid * ix.PQ.Ksub
}

// CompressionRatio float32 原始向量 → PQ code 的压缩比。
func (ix *IndexIVFPQ) CompressionRatio() float64 {
	return float64(ix.D*4) / float64(ix.CodeSize)
}

func (ix *IndexIVFPQ) assignCoarse(x []float64) int {
	bi, bd := 0, 0.0
	for i, c := range ix.Coarse {
		if v := L2Sqr(x, c); i == 0 || v < bd {
			bd, bi = v, i
		}
	}
	return bi
}

// Train 训练粗量化器 + PQ（默认编码残差）。
func (ix *IndexIVFPQ) Train(xs [][]float64, niter, seed int) {
	if len(xs) < ix.Nlist {
		panic("training set smaller than nlist")
	}
	ix.Coarse = Kmeans(xs, ix.Nlist, niter, seed)
	if ix.ByResidual {
		resid := make([][]float64, len(xs))
		for i, x := range xs {
			resid[i] = SubVec(x, ix.Coarse[ix.assignCoarse(x)])
		}
		ix.PQ.Train(resid, niter, seed+101)
	} else {
		ix.PQ.Train(xs, niter, seed+101)
	}
	ix.PrecomputeTable()
}

// Add 入库。
func (ix *IndexIVFPQ) Add(xs [][]float64) {
	ix.Lists = make([][]int, ix.Nlist)
	ix.Codes = make([][][]int, ix.Nlist)
	base := len(ix.Vectors)
	for i, x := range xs {
		ix.Vectors = append(ix.Vectors, x)
		ci := ix.assignCoarse(x)
		src := x
		if ix.ByResidual {
			src = SubVec(x, ix.Coarse[ci])
		}
		ix.Lists[ci] = append(ix.Lists[ci], base+i)
		ix.Codes[ci] = append(ix.Codes[ci], ix.PQ.ComputeCode(src))
	}
}

// PrecomputeTable 对应 initialize_IVFPQ_precomputed_table 的三条决策路径。
func (ix *IndexIVFPQ) PrecomputeTable() int {
	if ix.UsePrecompTbl == -1 {
		ix.TableSize = 0
		return 0
	}
	mKsub := int64(ix.PQ.M * ix.PQ.Ksub)
	if ix.UsePrecompTbl == 0 {
		// 只有 L2 且开残差才值得预计算
		if !(ix.Metric == "L2" && ix.ByResidual) {
			ix.TableSize = 0
			return 0
		}
		size := mKsub * int64(ix.Nlist) * 4
		if size > PrecomputedTableMaxBytes {
			ix.TableSize = 0
			return 0
		}
		ix.UsePrecompTbl = 1
		ix.TableSize = size
	} else {
		ix.TableSize = mKsub * int64(ix.Nlist) * 4
	}
	return ix.UsePrecompTbl
}

// EffectiveNprobe 对应 `cur_nprobe = std::min(nlist, nprobe)`。
func (ix *IndexIVFPQ) EffectiveNprobe(nprobe int) int {
	if nprobe <= 0 {
		panic("nprobe must be > 0")
	}
	if ix.Nlist < nprobe {
		return ix.Nlist
	}
	return nprobe
}

// RecallAtK 在给定查询集上的召回。
func (ix *IndexIVFPQ) RecallAtK(qs [][]float64, k, nprobe int) float64 {
	hit, tot := 0, 0
	for _, q := range qs {
		got := map[int]bool{}
		for _, it := range ix.Search(q, k, nprobe) {
			got[it.Node] = true
		}
		brute := make([]Item, len(ix.Vectors))
		for i, v := range ix.Vectors {
			brute[i] = Item{Dist: L2Sqr(q, v), Node: i}
		}
		sort.Slice(brute, func(a, b int) bool { return brute[a].Dist < brute[b].Dist })
		for i := 0; i < k && i < len(brute); i++ {
			if got[brute[i].Node] {
				hit++
			}
		}
		tot += k
	}
	return float64(hit) / float64(tot)
}

// Search 检索：取 nprobe 个倒排链，逐条 ADC 打分。
func (ix *IndexIVFPQ) Search(q []float64, k, nprobe int) []Item {
	cur := ix.EffectiveNprobe(nprobe)
	cds := make([]coarseDist, ix.Nlist)
	for i := range cds {
		cds[i] = coarseDist{i, L2Sqr(q, ix.Coarse[i])}
	}
	sort.Slice(cds, func(a, b int) bool { return cds[a].d < cds[b].d })

	res := []Item{}
	for t := 0; t < cur; t++ {
		li := cds[t].idx
		src := q
		if ix.ByResidual {
			src = SubVec(q, ix.Coarse[li])
		}
		tbl := ix.PQ.ComputeDistanceTable(src)
		for j, id := range ix.Lists[li] {
			code := ix.Codes[li][j]
			res = append(res, Item{Dist: ix.PQ.AdcL2(tbl, code), Node: id})
		}
	}
	sort.Slice(res, func(a, b int) bool {
		if res[a].Dist != res[b].Dist {
			return res[a].Dist < res[b].Dist
		}
		return res[a].Node < res[b].Node
	})
	if len(res) > k {
		res = res[:k]
	}
	return res
}
