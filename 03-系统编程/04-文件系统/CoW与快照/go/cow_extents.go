// cow_extents.go — Btrfs 风格 CoW 的 extent 记账
//
// 运行: go run .
//
// 与 python/cow_extents.py 同题：物理块引用计数、extent 切三段、
// defrag 打断 reflink、挂载选项互斥、FIEMAP_EXTENT_SHARED。

package main

import (
	"fmt"
	"sort"
)

const blockSize = 4096

// ---- fiemap.h: fe_flags ----
const (
	FiLast        uint32 = 0x00000001
	FiUnknown     uint32 = 0x00000002
	FiDelalloc    uint32 = 0x00000004
	FiDataInline  uint32 = 0x00000200
	FiDataTail    uint32 = 0x00000400
	FiNotAligned  uint32 = 0x00000100
	FiUnwritten   uint32 = 0x00000800
	FiMerged      uint32 = 0x00001000
	FiShared      uint32 = 0x00002000
	FiFlagSync    uint32 = 0x00000001
	FiFlagXattr   uint32 = 0x00000002
	FiFlagsCompat        = FiFlagSync | FiFlagXattr
)

// Allocator 只关心每块的引用计数。
type Allocator struct {
	Next int
	Refs map[int]int
}

func NewAllocator() *Allocator {
	return &Allocator{Refs: map[int]int{}}
}

func (a *Allocator) Alloc(n int) int {
	p := a.Next
	a.Next += n
	for i := p; i < p+n; i++ {
		a.Refs[i] = 1
	}
	return p
}

func (a *Allocator) Share(p, n int) {
	for i := p; i < p+n; i++ {
		a.Refs[i]++
	}
}

func (a *Allocator) Drop(p, n int) {
	for i := p; i < p+n; i++ {
		a.Refs[i]--
		if a.Refs[i] == 0 {
			delete(a.Refs, i)
		}
	}
}

func (a *Allocator) RefCount(p int) int { return a.Refs[p] }
func (a *Allocator) LiveBlocks() int    { return len(a.Refs) }

// Extent 是块粒度的 (逻辑, 物理, 长度) 三元组。
type Extent struct {
	Logical   int
	Phys      int
	Blocks    int
	Unwritten bool
}

func (e Extent) End() int { return e.Logical + e.Blocks }

type File struct {
	Name    string
	Extents []Extent
	NoCow   bool
}

func (f *File) Blocks() int {
	n := 0
	for _, e := range f.Extents {
		n += e.Blocks
	}
	return n
}

func MakeFile(a *Allocator, name string, blocks int) *File {
	return &File{Name: name, Extents: []Extent{{0, a.Alloc(blocks), blocks, false}}}
}

// ReflinkCopy 只复制元数据，物理块引用计数 +1。
func ReflinkCopy(a *Allocator, src *File) *File {
	out := &File{Name: src.Name + ".reflink", NoCow: src.NoCow}
	for _, e := range src.Extents {
		a.Share(e.Phys, e.Blocks)
		out.Extents = append(out.Extents, e)
	}
	return out
}

// CowWrite 往中间写会把 extent 切成头/中/尾三段，只有"中"要新分配。
func CowWrite(a *Allocator, f *File, off, n int) int {
	if f.NoCow {
		return 0 // NODATACOW：原地覆盖
	}
	lo, hi := off, off+n
	allocated, out := 0, []Extent{}
	for _, e := range f.Extents {
		if e.End() <= lo || e.Logical >= hi {
			out = append(out, e)
			continue
		}
		os_, oe := max(lo, e.Logical), min(hi, e.End())
		if os_ > e.Logical { // 头
			out = append(out, Extent{e.Logical, e.Phys, os_ - e.Logical, e.Unwritten})
		}
		delta := os_ - e.Logical // 中：复制出来再改写
		a.Drop(e.Phys+delta, oe-os_)
		np := a.Alloc(oe - os_)
		allocated += oe - os_
		out = append(out, Extent{os_, np, oe - os_, false})
		if oe < e.End() { // 尾
			out = append(out, Extent{oe, e.Phys + (oe - e.Logical), e.End() - oe, e.Unwritten})
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Logical < out[j].Logical })
	f.Extents = out
	return allocated
}

// Defrag 打断所有 reflink：整份重写成一块。
func Defrag(a *Allocator, f *File) int {
	total := f.Blocks()
	for _, e := range f.Extents {
		a.Drop(e.Phys, e.Blocks)
	}
	f.Extents = []Extent{{0, a.Alloc(total), total, false}}
	return total
}

// ExclusiveBlocks 只数引用计数为 1 的块。
func ExclusiveBlocks(a *Allocator, f *File) int {
	n := 0
	for _, e := range f.Extents {
		for i := e.Phys; i < e.Phys+e.Blocks; i++ {
			if a.RefCount(i) == 1 {
				n++
			}
		}
	}
	return n
}

// Fiemap 生成 (logical, physical, length, flags)。
func Fiemap(a *Allocator, f *File) [][4]int64 {
	out := [][4]int64{}
	for i, e := range f.Extents {
		var flags uint32
		if a.RefCount(e.Phys) > 1 {
			flags |= FiShared
		}
		if e.Unwritten {
			flags |= FiUnwritten
		}
		if i == len(f.Extents)-1 {
			flags |= FiLast
		}
		out = append(out, [4]int64{int64(e.Logical) * blockSize,
			int64(e.Phys) * blockSize, int64(e.Blocks) * blockSize, int64(flags)})
	}
	return out
}

// ResolveMountOptions 挂载选项按顺序处理，最后出现者生效。
func ResolveMountOptions(opts []string) map[string]bool {
	st := map[string]bool{"datacow": true, "datasum": true, "compress": false}
	for _, o := range opts {
		switch o {
		case "compress":
			st["compress"], st["datacow"], st["datasum"] = true, true, true
		case "nocompress":
			st["compress"] = false
		case "nodatacow":
			st["datacow"], st["datasum"], st["compress"] = false, false, false
		case "datacow":
			st["datacow"] = true
		case "nodatasum":
			st["datasum"], st["compress"] = false, false
		case "datasum":
			st["datasum"], st["datacow"] = true, true
		}
	}
	return st
}

func flagNames(flags uint32) []string {
	pairs := []struct {
		b uint32
		n string
	}{
		{FiLast, "LAST"}, {FiUnknown, "UNKNOWN"}, {FiDelalloc, "DELALLOC"},
		{FiNotAligned, "NOT_ALIGNED"}, {FiDataInline, "DATA_INLINE"},
		{FiDataTail, "DATA_TAIL"}, {FiUnwritten, "UNWRITTEN"},
		{FiMerged, "MERGED"}, {FiShared, "SHARED"},
	}
	out := []string{}
	for _, p := range pairs {
		if flags&p.b != 0 {
			out = append(out, p.n)
		}
	}
	return out
}

func main() {
	a := NewAllocator()
	src := MakeFile(a, "db", 10)
	clone := ReflinkCopy(a, src)
	fmt.Printf("reflink 后: 逻辑 %d 块, 物理 %d 块\n",
		src.Blocks()+clone.Blocks(), a.LiveBlocks())

	n := CowWrite(a, clone, 3, 1)
	fmt.Printf("改 1 块: 新分配 %d 块, extent 切为 %d 段, 物理 %d 块\n",
		n, len(clone.Extents), a.LiveBlocks())

	for i, e := range Fiemap(a, clone) {
		fmt.Printf("  fiemap[%d] logical=%d len=%d %v\n",
			i, e[0], e[2], flagNames(uint32(e[3])))
	}

	b := ReflinkCopy(a, src)
	before := a.LiveBlocks()
	Defrag(a, b)
	fmt.Printf("defrag 打断 reflink: 物理 %d -> %d 块\n", before, a.LiveBlocks())
	fmt.Printf("defrag 后 b 独占 %d 块、src 独占 %d 块\n",
		ExclusiveBlocks(a, b), ExclusiveBlocks(a, src))

	for _, opts := range [][]string{
		{"compress", "nodatacow"}, {"nodatacow", "compress"}, {"nodatasum"},
	} {
		st := ResolveMountOptions(opts)
		fmt.Printf("  %v -> datacow=%v datasum=%v compress=%v\n",
			opts, st["datacow"], st["datasum"], st["compress"])
	}

	demoCRC()
}

func max(x, y int) int {
	if x > y {
		return x
	}
	return y
}

func min(x, y int) int {
	if x < y {
		return x
	}
	return y
}
