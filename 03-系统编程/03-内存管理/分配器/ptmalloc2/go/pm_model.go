// ptmalloc2(glibc malloc)的三层结构:chunk / bins / tcache + arena(模型层)。
//
// 权威来源(实际联网阅读):
//   sourceware.org/glibc/wiki/MallocInternals
//   raw.githubusercontent.com/bminor/glibc/master/malloc/malloc.c
//   raw.githubusercontent.com/bminor/glibc/release/2.35/master/malloc/malloc.c
//   man7.org/linux/man-pages/man3/mallopt.3.html
//
// 保留 glibc 的**常量公式与查找顺序**;"物理相邻合并"简化为地址序链表的相邻合并。
package main

const (
	sizeSz      = 8
	mallocAlign = 2 * sizeSz // 16
	alignMask   = mallocAlign - 1
	chunkHdrSz  = 2 * sizeSz // 16
	minChunkSz  = 4 * sizeSz // offsetof(struct malloc_chunk, fd_nextsize) = 32
	minSize     = minChunkSz // 已对齐

	nBins       = 128
	nSmallBins  = 64
	smallCorr   = 0 // MALLOC_ALIGNMENT > CHUNK_HDR_SZ ? 1 : 0
	minLargeSz  = (nSmallBins - smallCorr) * mallocAlign // 1024
	maxFastSize = 160

	defaultMmapThreshold = 128 * 1024
	tcacheFillMaster     = 16 // glibc master
	tcacheFill235        = 7  // glibc 2.35
	tcacheMaxBinsMaster  = 64 + 12
	tcacheMaxBins235     = 64

	prevInuse    = 0x01
	isMmapped    = 0x02
	nonMainArena = 0x04

	maxArenasFactor     = 8 // arena 数上限 = 8 * CPU 核数
	topChunkSize        = 1 << 20
	defaultTrimThresh   = 128 * 1024
	defaultMmapMax      = 65536
)

// request2size 复现 malloc.c 的宏。
func request2size(req int) int {
	padded := req + sizeSz + alignMask
	if padded < minSize {
		return minSize
	}
	return padded &^ alignMask
}

func csize2tidx(x int) int { return (x - minSize) / mallocAlign }
func tidx2csize(i int) int { return i*mallocAlign + minSize }
func usize2tidx(x int) int { return csize2tidx(request2size(x)) }

func inSmallbinRange(sz int) bool { return sz < minLargeSz }
func smallbinIndex(sz int) int    { return (sz >> 4) + smallCorr }

func largebinIndex64(sz int) int {
	switch {
	case (sz >> 6) <= 48:
		return 48 + (sz >> 6)
	case (sz >> 9) <= 20:
		return 91 + (sz >> 9)
	case (sz >> 12) <= 10:
		return 110 + (sz >> 12)
	case (sz >> 15) <= 4:
		return 119 + (sz >> 15)
	case (sz >> 18) <= 2:
		return 124 + (sz >> 18)
	default:
		return 126
	}
}

func binIndex(sz int) int {
	if inSmallbinRange(sz) {
		return smallbinIndex(sz)
	}
	return largebinIndex64(sz)
}

type chunk struct {
	off, size int
	inuse     bool
	mmapped   bool
}

// prevSizeValid 手册:prev_size 只有在前一个 chunk 空闲(PREV_INUSE=0)时才有意义。
func (c *chunk) prevSizeValid() bool { return !c.inuse }

