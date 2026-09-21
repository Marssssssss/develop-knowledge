// Package main —— 标记-清除 + 空闲链表合并的 Go 镜像（与 python/main.py 同构）。
//
// 依据同 python 版：Doug Lea《A Memory Allocator》(dlmalloc) 设计说明。
// 本机无 Go 工具链，本文件只做人工审查 + bracket_check/go_sanity 静态校验，
// 运行证据由 python/selfcheck_ms.py 的 60 条断言提供。
// 堆本体与分箱在本文件；mark-sweep 部分见 marksweep.go。
package main

import (
	"fmt"
	"math"
)

// ---- dlmalloc 常量 ----
const (
	Word             = 8 // 64 位：1 word = 8 字节
	HdrWords         = 1
	MinChunkBytes32  = 16
	MinChunkBytes64  = 24
	MinChunkWords64  = MinChunkBytes64 / Word // 3
	NBins            = 128
	SmallBinLimit    = 512
	SmallBinSpacing  = 8
	DefaultMmapThres = 1 << 20
)

// Block 是一个 chunk：addr 为 word 下标，size 为**含头**总字数。
type Block struct {
	Addr      int
	Size      int
	Free      bool
	Seq       int
	Mmapped   bool
	HasFooter bool
}

// Payload 返回用户可见的 word 下标（跳过头部）。
func (b *Block) Payload() int { return b.Addr + HdrWords }

// Usable 返回用户可用的 word 数。
func (b *Block) Usable() int { return b.Size - HdrWords }

// Heap 是按地址有序的 chunk 列表。
type Heap struct {
	N             int
	Policy        string // first | best | dlmalloc
	Defer         bool   // 延迟合并
	ClassicTags   bool   // 经典边界标记：使用中 chunk 也带 trailer
	MmapThreshold int
	seq           int
	mmapBase      int
	Blocks        []*Block
}

// NewHeap 造一个整块空闲的堆。
func NewHeap(nwords int, policy string, deferCoalesce bool, classic bool) *Heap {
	h := &Heap{N: nwords, Policy: policy, Defer: deferCoalesce,
		ClassicTags: classic, MmapThreshold: DefaultMmapThres, mmapBase: nwords}
	h.Blocks = append(h.Blocks, &Block{Addr: 0, Size: nwords, Free: true, HasFooter: classic})
	return h
}

func (h *Heap) nextSeq() int { h.seq++; return h.seq }

// BinIndex 计算 dlmalloc 口径的 bin 编号。
func BinIndex(nbytes int) int {
	if nbytes < SmallBinLimit {
		i := (nbytes + SmallBinSpacing - 1) / SmallBinSpacing
		if i < 1 {
			return 1
		}
		return i
	}
	step := math.Log2(float64(nbytes) / float64(SmallBinLimit))
	if step < 0 {
		step = 0
	}
	idx := SmallBinLimit/SmallBinSpacing + int(step*4)
	if idx > NBins-1 {
		return NBins - 1
	}
	return idx
}

// Wilderness 返回地址最高的那块（唯一可以靠 sbrk 无限延伸的块）。
func (h *Heap) Wilderness() *Block { return h.Blocks[len(h.Blocks)-1] }

func (h *Heap) findFit(need int) int {
	idx := -1
	for i, b := range h.Blocks {
		if b.Free && b.Size >= need {
			idx = i
			if h.Policy == "first" {
				return idx
			}
			break
		}
	}
	if idx < 0 {
		return -1
	}
	best := idx
	w := h.Wilderness()
	for i, b := range h.Blocks {
		if !b.Free || b.Size < need {
			continue
		}
		if h.Policy == "dlmalloc" && b == w {
			continue // wilderness 视为“最大”，只在没有别的选择时才用
		}
		if h.Blocks[best].Size > b.Size || (h.Policy == "dlmalloc" && h.Blocks[best] == w) {
			best = i
		}
	}
	return best
}

// Malloc 分配 nwords 个可用 word，返回 payload 下标；失败返回 -1。
func (h *Heap) Malloc(nwords int) int {
	need := nwords + HdrWords
	if need < MinChunkWords64 {
		need = MinChunkWords64
	}
	if need*Word > h.MmapThreshold && !h.arenaCanFit(need) {
		return h.mmapAlloc(need)
	}
	i := h.findFit(need)
	if i < 0 {
		return -1
	}
	b := h.Blocks[i]
	rest := b.Size - need
	b.Free = false
	b.Seq = h.nextSeq()
	b.HasFooter = h.ClassicTags
	if rest >= MinChunkWords64 {
		b.Size = need
		nb := &Block{Addr: b.Addr + need, Size: rest, Free: true,
			Seq: h.nextSeq(), HasFooter: h.ClassicTags}
		out := make([]*Block, 0, len(h.Blocks)+1)
		out = append(out, h.Blocks[:i+1]...)
		out = append(out, nb)
		out = append(out, h.Blocks[i+1:]...)
		h.Blocks = out
	}
	return b.Payload()
}

func (h *Heap) arenaCanFit(need int) bool {
	for _, b := range h.Blocks {
		if b.Free && b.Size >= need {
			return true
		}
	}
	return false
}

func (h *Heap) mmapAlloc(need int) int {
	b := &Block{Addr: h.mmapBase, Size: need, Free: false,
		Seq: h.nextSeq(), Mmapped: true}
	h.Blocks = append(h.Blocks, b)
	h.mmapBase += need + 1
	return b.Payload()
}

// Free 释放 payload；Defer 为假时立即与相邻空闲块合并。
func (h *Heap) Free(payload int) {
	for _, b := range h.Blocks {
		if b.Payload() == payload {
			b.Free = true
			b.Seq = h.nextSeq()
			b.HasFooter = true
			if !h.Defer {
				h.Coalesce()
			}
			return
		}
	}
	panic("free: bad payload")
}

// Coalesce 合并所有相邻空闲块；mmap 块永不参与合并。
func (h *Heap) Coalesce() int {
	out := make([]*Block, 0, len(h.Blocks))
	for _, b := range h.Blocks {
		if len(out) > 0 {
			p := out[len(out)-1]
			if p.Free && b.Free && p.Addr+p.Size == b.Addr && !p.Mmapped && !b.Mmapped {
				p.Size += b.Size
				continue
			}
		}
		out = append(out, b)
	}
	h.Blocks = out
	return len(out)
}

// FooterWords 统计整堆为尾部标记付出的字数。
func (h *Heap) FooterWords() int {
	n := 0
	for _, b := range h.Blocks {
		if b.Free || b.HasFooter {
			n++
		}
	}
	return n
}

// Bins 按 dlmalloc 口径分箱，箱内按 (size, seq) 升序（oldest-first）。
func (h *Heap) Bins() map[int][]*Block {
	d := map[int][]*Block{}
	for _, b := range h.Blocks {
		if b.Free {
			k := BinIndex(b.Usable() * Word)
			d[k] = append(d[k], b)
		}
	}
	for _, v := range d {
		for i := 1; i < len(v); i++ {
			for j := i; j > 0; j-- {
				if v[j].Size < v[j-1].Size || (v[j].Size == v[j-1].Size && v[j].Seq < v[j-1].Seq) {
					v[j], v[j-1] = v[j-1], v[j]
				}
			}
		}
	}
	return d
}

func main() {
	// 与 python/selfcheck_ms.py §3 同构：三块全释放后合并成整堆
	h := NewHeap(64, "best", false, false)
	a := h.Malloc(4)
	b := h.Malloc(4)
	c := h.Malloc(4)
	fmt.Println("payload:", a, b, c)
	h.Free(b)
	fmt.Println("blocks after free(b) =", len(h.Blocks))
	h.Free(a)
	h.Free(c)
	fmt.Println("blocks after coalesce =", len(h.Blocks), "size =", h.Blocks[0].Size)

	// 与 §10 同构：环形垃圾
	g := NewMSHeap(32, "best")
	x := g.New(3, nil)
	y := g.New(3, []int{x})
	g.Refs[x] = []int{y}
	fmt.Println("collected (cycle, no roots) =", g.Sweep(),
		" markVisited =", g.MarkVisited, " sweepScanned =", g.SweepScanned)
}
