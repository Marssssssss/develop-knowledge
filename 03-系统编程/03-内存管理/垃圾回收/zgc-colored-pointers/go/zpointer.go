// Package main —— ZGC 着色指针的 Go 镜像（与 python/zpointer.py 同构）。
//
// 常量照抄 OpenJDK src/hotspot/share/gc/z/zAddress.hpp（master，实读）。
// 无 Go 工具链，仅人工审查 + bracket_check / go_sanity / go_crossref。
package main

// ---- 源码里的两个 constexpr ----
func zPointerMask(shift, bits int) int { return ((1 << bits) - 1) << shift }
func zPointerBit(shift, offset int) int {
	return 1 << (shift + offset)
}

// ---- Reserved bits ----
var (
	ZPointerReservedShift = 0
	ZPointerReservedBits  = 4
	ZPointerReservedMask  = zPointerMask(ZPointerReservedShift, ZPointerReservedBits)
)

// ---- Remembered set bits ----
var (
	ZPointerRememberedShift = ZPointerReservedShift + ZPointerReservedBits
	ZPointerRememberedBits  = 2
	ZPointerRememberedMask  = zPointerMask(ZPointerRememberedShift, ZPointerRememberedBits)
	ZPointerRemembered0     = zPointerBit(ZPointerRememberedShift, 0)
	ZPointerRemembered1     = zPointerBit(ZPointerRememberedShift, 1)
)

// ---- Marked bits ----
var (
	ZPointerMarkedShift   = ZPointerRememberedShift + ZPointerRememberedBits
	ZPointerMarkedBits    = 6
	ZPointerMarkedMask    = zPointerMask(ZPointerMarkedShift, ZPointerMarkedBits)
	ZPointerFinalizable0  = zPointerBit(ZPointerMarkedShift, 0)
	ZPointerFinalizable1  = zPointerBit(ZPointerMarkedShift, 1)
	ZPointerMarkedYoung0  = zPointerBit(ZPointerMarkedShift, 2)
	ZPointerMarkedYoung1  = zPointerBit(ZPointerMarkedShift, 3)
	ZPointerMarkedOld0    = zPointerBit(ZPointerMarkedShift, 4)
	ZPointerMarkedOld1    = zPointerBit(ZPointerMarkedShift, 5)
)

// ---- Remapped bits ----
var (
	ZPointerRemappedShift = ZPointerMarkedShift + ZPointerMarkedBits
	ZPointerRemappedBits  = 4
	ZPointerRemappedMask  = zPointerMask(ZPointerRemappedShift, ZPointerRemappedBits)
	ZPointerRemapped00    = zPointerBit(ZPointerRemappedShift, 0)
	ZPointerRemapped01    = zPointerBit(ZPointerRemappedShift, 1)
	ZPointerRemapped10    = zPointerBit(ZPointerRemappedShift, 2)
	ZPointerRemapped11    = zPointerBit(ZPointerRemappedShift, 3)
)

// ZPointerLoadShiftTable 与上面的 zpointer 布局紧耦合（源码原文）。
var ZPointerLoadShiftTable = []int{
	ZPointerRemappedShift + ZPointerRemappedShift, // [0] Null
	ZPointerRemappedShift + 1,                     // [1] Remapped00
	ZPointerRemappedShift + 2,                     // [2] Remapped01
	0,
	ZPointerRemappedShift + 3, // [4] Remapped10
	0,
	0,
	0,
	ZPointerRemappedShift + 4, // [8] Remapped11
}

// ---- Barrier metadata masks ----
var (
	ZPointerLoadMetadataMask  = ZPointerRemappedMask
	ZPointerMarkMetadataMask  = ZPointerLoadMetadataMask | ZPointerMarkedMask
	ZPointerStoreMetadataMask = ZPointerMarkMetadataMask | ZPointerRememberedMask
	ZPointerAllMetadataMask   = ZPointerStoreMetadataMask
)

// ZAddressShift 元数据占满低 16 位，地址位恒从 bit16 开始。
const ZAddressShift = 16

// ZAddressAlignMask 用于校验地址对齐。
const ZAddressAlignMask = (1 << ZAddressShift) - 1

// RemapOld / RemapYoung 是两个交替的 4 位图案（源码注释）。
var (
	RemapOld   = []int{0b0011, 0b1100}
	RemapYoung = []int{0b0101, 0b1010}
)

// Color 给无着色地址上色（地址必须 64 KiB 对齐）。
func Color(addr, c int) int {
	if addr&ZAddressAlignMask != 0 {
		panic("address must be 64KiB aligned")
	}
	return addr | c
}

// Uncolor 去掉全部元数据位。
func Uncolor(ptr int) int { return ptr & ^ZPointerAllMetadataMask }

// LoadShiftLookupIndex 取查表下标。
func LoadShiftLookupIndex(value int) int {
	return (value & ZPointerLoadMetadataMask) >> ZPointerRemappedShift
}

// LoadShiftLookup 取推测移位量。
func LoadShiftLookup(value int) int {
	return ZPointerLoadShiftTable[LoadShiftLookupIndex(value)]
}

// OverlappingZeros 返回地址位与元数据零位的重叠数：00/01/10/11 -> 3/2/1/0。
func OverlappingZeros(colorField int) int {
	n := 0
	for colorField > 1 {
		colorField >>= 1
		n++
	}
	return 3 - n
}

// ZGlobalsPointers 当前的期望位与 flip 逻辑。
type ZGlobalsPointers struct {
	OldI         int
	YoungI       int
	MarkedYoung  int
	MarkedOld    int
	Remapped     int
	OldMask      int
	YoungMask    int
	LoadGoodMask int
	LoadBadMask  int
}

// NewZGlobalsPointers 构造初态（Old0 & Young0 = Remapped00）。
func NewZGlobalsPointers() *ZGlobalsPointers {
	g := &ZGlobalsPointers{MarkedYoung: ZPointerMarkedYoung0, MarkedOld: ZPointerMarkedOld0}
	g.refresh()
	return g
}

func (g *ZGlobalsPointers) refresh() {
	g.OldMask = RemapOld[g.OldI]
	g.YoungMask = RemapYoung[g.YoungI]
	g.Remapped = (g.OldMask & g.YoungMask) << ZPointerRemappedShift
	g.LoadGoodMask = g.Remapped
	g.LoadBadMask = ZPointerLoadMetadataMask & ^g.Remapped
}

// FlipYoungRelocateStart 翻转 young 侧的 remap 掩码。
func (g *ZGlobalsPointers) FlipYoungRelocateStart() { g.YoungI ^= 1; g.refresh() }

// FlipOldRelocateStart 翻转 old 侧的 remap 掩码。
func (g *ZGlobalsPointers) FlipOldRelocateStart() { g.OldI ^= 1; g.refresh() }

// IsLoadGood 判断颜色的 remap 位是否为当前期望值。
func (g *ZGlobalsPointers) IsLoadGood(ptr int) bool {
	return ptr&ZPointerLoadMetadataMask == g.Remapped
}

// ZHeap 带转发表的堆：并发重定位 + 自愈 load barrier。
type ZHeap struct {
	G          *ZGlobalsPointers
	Forwarding map[int]int
	Fields     map[string]int
	SlowPaths  int
}

// NewZHeap 构造一个空堆。
func NewZHeap(g *ZGlobalsPointers) *ZHeap {
	return &ZHeap{G: g, Forwarding: map[int]int{}, Fields: map[string]int{}}
}

// LoadBarrier 颜色过期时走慢路径，并把修正后的指针写回字段（self-healing）。
func (h *ZHeap) LoadBarrier(field string) int {
	p := h.Fields[field]
	if p == 0 || h.G.IsLoadGood(p) {
		return Uncolor(p)
	}
	h.SlowPaths++
	addr := Uncolor(p)
	if to, ok := h.Forwarding[addr]; ok {
		addr = to
	}
	h.Fields[field] = Color(addr, h.G.Remapped)
	return addr
}

// ShenandoahHeap Brooks pointer 放在对象头里：每次 load 都要多读一次内存。
type ShenandoahHeap struct {
	Brooks     map[int]int
	Fields     map[string]int
	Loads      int
	ExtraReads int
}

// LoadReferenceBarrier 对应 Shenandoah 的 LRB。
func (s *ShenandoahHeap) LoadReferenceBarrier(field string) int {
	s.Loads++
	obj := s.Fields[field]
	s.ExtraReads++ // 必须读对象头里的转发词
	if to, ok := s.Brooks[obj]; ok {
		return to
	}
	return obj
}
