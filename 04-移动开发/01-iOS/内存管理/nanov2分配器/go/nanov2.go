// Nano V2 分配器的地址布局与块分配状态机(Go 侧转写)。
// 对照 apple-oss-distributions/libmalloc@main 的
// src/nano_zone_common.h、src/nanov2_zone.h、src/nanov2_malloc.c。
package main

// ---- nano_zone_common.h ----
const (
	nanoMaxSize         = 256
	shiftNanoQuantum    = 4
	nanoRegimeQuanta    = 1 << shiftNanoQuantum // 16
	nanoSizeClasses     = nanoMaxSize / nanoRegimeQuanta
	shiftNanoSignature  = 29 // iOS 变体
	nanoSignatureBits   = 35
	shiftNanoSignatureOSX = 44 // macOS 变体
	nanoSignatureBitsOSX = 20
	nanoSignatureOSX    = 0x6
)

// ---- nanov2_zone.h ----
const (
	offsetBits = 14
	blockBits  = 12
	arenaBits  = 3
	regionBitsOSX = 15
	regionBitsIOS = 0

	blockSize        = 1 << offsetBits          // 16KB
	arenaSize        = 64 * 1024 * 1024         // 64MB
	regionSize       = 512 * 1024 * 1024        // 512MB
	blocksPerArena   = arenaSize / blockSize    // 4096
	arenasPerRegion  = regionSize / arenaSize   // 8
	maxSlotsPerBlock = blockSize / nanoRegimeQuanta
)

// ---- nanov2_malloc.c ----
const (
	blocksPerUnitShift = 6
	blocksPerUnit      = 1 << blocksPerUnitShift // 64
	totalBlockUnits    = blocksPerArena / blocksPerUnit
	maxCurrentBlocks   = 64
	maxCurrentBlocksMask = maxCurrentBlocks - 1
)

// next_slot 的特殊值
const (
	slotNull       = 0x000
	slotGuard      = 0x7fa
	slotBump       = 0x7fb
	slotFull       = 0x7fc
	slotCanMadvise = 0x7fd
	slotMadvising  = 0x7fe
	slotMadvised   = 0x7ff
)

const freeMask = (1 << 10) - 1 // free_count / gen_count 都是 10 位

// blockUnitsBySizeClass 每个尺寸类占多少个块单元,总和必须是 64。
var blockUnitsBySizeClass = [nanoSizeClasses]int{
	2, 10, 11, 10, 5, 3, 3, 4, 3, 2, 2, 2, 2, 2, 1, 2,
}

// goodSize 对应 _nano_common_good_size。
func goodSize(size int) int {
	if size <= nanoRegimeQuanta {
		return nanoRegimeQuanta
	}
	return ((size + nanoRegimeQuanta - 1) >> shiftNanoQuantum) << shiftNanoQuantum
}

// sizeClassFromSize 对应 nanov2_size_class_from_size。
func sizeClassFromSize(size int) int {
	return ((size + nanoRegimeQuanta - 1) / nanoRegimeQuanta) - 1
}

// sizeFromSizeClass 对应 nanov2_size_from_size_class。
func sizeFromSizeClass(sc int) int {
	return (sc + 1) * nanoRegimeQuanta
}

// slotsBySizeClass 块内槽位数,除不尽的余量即浪费。
func slotsBySizeClass(sc int) int {
	return blockSize / sizeFromSizeClass(sc)
}

// arenaTables 复刻 nanov2_configure_once 里两张表的构建。
func arenaTables() ([]int, []int, []int) {
	total := 0
	for _, u := range blockUnitsBySizeClass {
		total += u * blocksPerUnit
	}
	if total != blocksPerArena {
		panic("block_units 总和必须凑满一个 arena")
	}
	first := make([]int, nanoSizeClasses)
	last := make([]int, nanoSizeClasses)
	// 块 0 留给元数据块,故第 0 类从 1 开始
	next := 1
	first[0] = next
	next = blockUnitsBySizeClass[0] * blocksPerUnit
	last[0] = next - 1
	for i := 1; i < nanoSizeClasses; i++ {
		first[i] = next
		next += blockUnitsBySizeClass[i] * blocksPerUnit
		last[i] = next - 1
	}
	ptrOffset := make([]int, 0, totalBlockUnits)
	for i, u := range blockUnitsBySizeClass {
		for j := 0; j < u; j++ {
			ptrOffset = append(ptrOffset, i)
		}
	}
	return first, last, ptrOffset
}

// Layout 对应 nanov2_addr_s 的位域布局。
type Layout struct {
	IOS         bool
	Signature   uint64
	OffsetBits  int
	BlockBits   int
	ArenaBits   int
	RegionBits  int
	SignBits    int
}

func newLayout(ios bool, signature uint64) Layout {
	if ios {
		return Layout{true, signature, 14, 12, 3, regionBitsIOS, nanoSignatureBits}
	}
	return Layout{false, signature, 14, 12, 3, regionBitsOSX, nanoSignatureBitsOSX}
}

func (l Layout) blockShift() int { return l.OffsetBits }

func (l Layout) arenaShift() int { return l.OffsetBits + l.BlockBits }

func (l Layout) regionShift() int { return l.arenaShift() + l.ArenaBits }

func (l Layout) encode(offset, block, arena, region int) uint64 {
	a := uint64(offset) | uint64(block)<<l.blockShift() | uint64(arena)<<l.arenaShift()
	if !l.IOS {
		a |= uint64(region) << l.regionShift()
	}
	return a | l.Signature<<l.regionShift()
}

func (l Layout) hasValidSignature(addr uint64) bool {
	return addr>>l.regionShift() == l.Signature
}

// blockIndexToMetaIndex 高低 6 位互换,是对合运算。
func blockIndexToMetaIndex(i int) int {
	return ((i >> 6) | (i << 6)) & 0xFFF
}

// Block 一个块的元数据加槽位空闲链表。
// free_count 的口径是"空闲槽位数 - 1",满块时为 -1 即回绕成 0x3FF。
type Block struct {
	SizeClass int
	Slots     int
	NextSlot  int
	FreeCount int
	GenCount  int
	InUse     bool
	SlotNext  map[int]int
	SlotGuard map[int]bool
}

func newBlock(sizeClass int) *Block {
	n := slotsBySizeClass(sizeClass)
	return &Block{
		SizeClass: sizeClass,
		Slots:     n,
		NextSlot:  slotBump,
		FreeCount: n - 1,
		GenCount:  0,
		InUse:     true,
		SlotNext:  map[int]int{},
		SlotGuard: map[int]bool{},
	}
}

func (b *Block) isActive() bool {
	return b.NextSlot != slotNull && b.NextSlot != slotMadvising &&
		b.NextSlot != slotMadvised && b.NextSlot != slotGuard
}

func (b *Block) canAllocateFrom() bool {
	return b.InUse && b.NextSlot != slotFull
}

func (b *Block) allocatedCount() int {
	if b.NextSlot == slotFull && b.FreeCount == freeMask {
		return b.Slots
	}
	return b.Slots - b.FreeCount - 1
}

// allocate 复刻 nanov2_allocate_from_block_inline。
// 返回 (槽位, 是否从空闲链表取, 是否损坏, 是否成功)。
func (b *Block) allocate() (int, bool, bool, bool) {
	if !b.canAllocateFrom() {
		return 0, false, false, false
	}
	isFull := b.FreeCount == 0
	var slot int
	fromFreeList := false
	newNext := 0
	if b.NextSlot == slotBump || b.NextSlot == slotCanMadvise {
		if isFull {
			newNext = slotFull
		} else {
			newNext = slotBump
		}
		slot = b.Slots - b.FreeCount - 1
	} else {
		fromFreeList = true
		slot = b.NextSlot - 1 // next_slot 是 1-based
		if isFull {
			newNext = slotFull
		} else {
			newNext = b.SlotNext[slot]
		}
	}
	b.NextSlot = newNext
	b.FreeCount = (b.FreeCount - 1) & freeMask
	b.GenCount = (b.GenCount + 1) & freeMask
	if fromFreeList && !b.SlotGuard[slot] {
		return slot, true, true, true
	}
	if fromFreeList {
		b.SlotGuard[slot] = false
	}
	return slot, fromFreeList, false, true
}

// free 复刻 nanov2_free_to_block_inline,返回该块是否可 madvise。
func (b *Block) free(slot int) bool {
	wasFull := b.NextSlot == slotFull
	prevNext := b.NextSlot
	newFree := (b.FreeCount + 1) & freeMask
	b.SlotGuard[slot] = true
	freeingLastActive := !wasFull && newFree == b.Slots-1
	if freeingLastActive {
		b.SlotNext[slot] = slotNull
		if b.InUse {
			b.NextSlot = slotBump
		} else {
			b.NextSlot = slotCanMadvise
		}
	} else {
		if wasFull {
			b.SlotNext[slot] = slotBump
		} else {
			b.SlotNext[slot] = prevNext
		}
		b.NextSlot = slot + 1
	}
	b.FreeCount = newFree
	b.GenCount = (b.GenCount + 1) & freeMask
	return b.NextSlot == slotCanMadvise
}

// Arena 一个 arena:4096 个块,块 0 是元数据块。
type Arena struct {
	First      []int
	Last       []int
	PtrOffset  []int
	AslrCookie int
}

func newArena(cookie int) *Arena {
	f, l, p := arenaTables()
	return &Arena{First: f, Last: l, PtrOffset: p, AslrCookie: cookie}
}

func (a *Arena) firstBlockForSizeClass(sc int) int {
	return a.First[sc] ^ a.AslrCookie
}

func (a *Arena) sizeClassForBlock(block int) int {
	logical := block ^ a.AslrCookie
	return a.PtrOffset[logical>>blocksPerUnitShift]
}

// allocationBlockIndex 对应 nanov2_get_allocation_block_index。
func allocationBlockIndex(cpu int) int {
	return cpu & maxCurrentBlocksMask
}
