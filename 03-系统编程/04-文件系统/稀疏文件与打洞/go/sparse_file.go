// sparse_file.go — SEEK_HOLE/SEEK_DATA 与 fallocate 五种模式
//
// 运行: go run .
//
// 与 python/sparse_file.py 同题：洞的查找边界、打洞与预分配、
// COLLAPSE/INSERT 的粒度与 EOF 约束。

package main

import (
	"fmt"
	"sort"
)

const block = 4096

const (
	seekData = 3
	seekHole = 4
)

// fallocate(2) 的 mode 位
const (
	fallocKeepSize      = 0x01
	fallocPunchHole     = 0x02
	fallocCollapseRange = 0x08
	fallocZeroRange     = 0x10
	fallocInsertRange   = 0x20
	fallocUnshareRange  = 0x40
)

const (
	fiemapLast      = 0x0001
	fiemapUnwritten = 0x0800
)

// SparseFile 是块粒度的稀疏文件。
type SparseFile struct {
	Size      int
	Data      map[int]bool // 有真实数据的块
	Unwritten map[int]bool // 已预分配但没写过的块
}

func NewSparseFile(size int) *SparseFile {
	return &SparseFile{Size: size, Data: map[int]bool{}, Unwritten: map[int]bool{}}
}

func (f *SparseFile) NBlocks() int { return (f.Size + block - 1) / block }

func (f *SparseFile) IsHole(b int) bool {
	return !f.Data[b] && !f.Unwritten[b]
}

func (f *SparseFile) AllocatedBlocks() int {
	seen := map[int]bool{}
	for b := range f.Data {
		seen[b] = true
	}
	for b := range f.Unwritten {
		seen[b] = true
	}
	return len(seen)
}

func (f *SparseFile) Write(offset, length int) {
	for b := offset / block; b <= (offset+length-1)/block; b++ {
		f.Data[b] = true
		delete(f.Unwritten, b)
	}
	if offset+length > f.Size {
		f.Size = offset + length
	}
}

// Lseek 只实现 SEEK_DATA / SEEK_HOLE。
func (f *SparseFile) Lseek(offset, whence int) (int, error) {
	if offset > f.Size {
		return 0, fmt.Errorf("ENXIO: offset 越过文件末尾")
	}
	if whence != seekData && whence != seekHole {
		return 0, fmt.Errorf("EINVAL: 只支持 SEEK_DATA / SEEK_HOLE")
	}
	start := offset / block
	wantHole := whence == seekHole
	for b := start; b < f.NBlocks(); b++ {
		if f.IsHole(b) == wantHole {
			if b == start {
				return offset, nil // 已经落在目标区间里
			}
			return b * block, nil
		}
	}
	if wantHole {
		return f.Size, nil // 末尾之后是隐式洞
	}
	return 0, fmt.Errorf("ENXIO: offset 落在文件末尾的洞里")
}

// Fallocate 支持五种模式。
func (f *SparseFile) Fallocate(mode, offset, length int) (int, error) {
	switch {
	case mode&fallocCollapseRange != 0:
		return f.collapse(offset, length, mode)
	case mode&fallocInsertRange != 0:
		return f.insert(offset, length, mode)
	case mode&fallocPunchHole != 0:
		if mode&fallocKeepSize == 0 {
			return 0, fmt.Errorf("EINVAL: PUNCH_HOLE 必须与 KEEP_SIZE 一起给")
		}
		return f.punch(offset, length), nil
	case mode&fallocZeroRange != 0:
		return f.zeroRange(offset, length, mode&fallocKeepSize != 0), nil
	default:
		return f.alloc(offset, length, mode&fallocKeepSize != 0), nil
	}
}

func (f *SparseFile) blkRange(offset, length int) (int, int) {
	return offset / block, (offset + length - 1) / block
}

func (f *SparseFile) alloc(offset, length int, keepSize bool) int {
	first, last := f.blkRange(offset, length)
	for b := first; b <= last; b++ {
		if !f.Data[b] {
			f.Unwritten[b] = true
		}
	}
	if !keepSize && offset+length > f.Size {
		f.Size = offset + length
	}
	return last - first + 1
}

func (f *SparseFile) punch(offset, length int) int {
	first, last := f.blkRange(offset, length)
	freed := 0
	for b := first; b <= last; b++ {
		if f.Data[b] || f.Unwritten[b] {
			freed++
		}
		delete(f.Data, b)
		delete(f.Unwritten, b)
	}
	return freed
}

func (f *SparseFile) zeroRange(offset, length int, keepSize bool) int {
	first, last := f.blkRange(offset, length)
	for b := first; b <= last; b++ {
		delete(f.Data, b)
		f.Unwritten[b] = true
	}
	if !keepSize && offset+length > f.Size {
		f.Size = offset + length
	}
	return last - first + 1
}

func (f *SparseFile) collapse(offset, length, mode int) (int, error) {
	if mode&^fallocCollapseRange != 0 {
		return 0, fmt.Errorf("EINVAL: COLLAPSE_RANGE 不能与其他标志并用")
	}
	if offset%block != 0 || length%block != 0 {
		return 0, fmt.Errorf("EINVAL: 粒度必须是文件系统逻辑块大小的倍数")
	}
	if offset+length >= f.Size {
		return 0, fmt.Errorf("EINVAL: 区间触及或越过 EOF，请改用 ftruncate")
	}
	f.shift(offset, -length)
	f.Size -= length
	return length, nil
}

func (f *SparseFile) insert(offset, length, mode int) (int, error) {
	if mode&^fallocInsertRange != 0 {
		return 0, fmt.Errorf("EINVAL: INSERT_RANGE 不能与其他标志并用")
	}
	if offset%block != 0 || length%block != 0 {
		return 0, fmt.Errorf("EINVAL: 粒度必须是文件系统逻辑块大小的倍数")
	}
	if offset >= f.Size {
		return 0, fmt.Errorf("EINVAL: offset 达到或越过 EOF，请改用 ftruncate")
	}
	f.shift(offset, length)
	f.Size += length
	return length, nil
}

// shift 把 [offset, Size) 的内容整体平移 delta 字节。
func (f *SparseFile) shift(offset, delta int) {
	d, start := delta/block, offset/block
	nd, nu := map[int]bool{}, map[int]bool{}
	all := map[int]bool{}
	for b := range f.Data {
		all[b] = true
	}
	for b := range f.Unwritten {
		all[b] = true
	}
	for b := range all {
		tgt := b
		if b >= start {
			tgt = b + d
		}
		if f.Data[b] {
			nd[tgt] = true
		} else {
			nu[tgt] = true
		}
	}
	f.Data, f.Unwritten = nd, nu
}

// Fiemap 合并相邻同类块。
func (f *SparseFile) Fiemap() [][3]int {
	type ext struct {
		off   int
		len   int
		flags int
	}
	out := []ext{}
	all := []int{}
	for b := range f.Data {
		all = append(all, b)
	}
	for b := range f.Unwritten {
		if !f.Data[b] {
			all = append(all, b)
		}
	}
	sort.Ints(all)
	for _, b := range all {
		flags := 0
		if f.Unwritten[b] {
			flags = fiemapUnwritten
		}
		if len(out) > 0 && out[len(out)-1].off+out[len(out)-1].len == b*block &&
			out[len(out)-1].flags == flags {
			out[len(out)-1].len += block
		} else {
			out = append(out, ext{b * block, block, flags})
		}
	}
	res := make([][3]int, len(out))
	for i, e := range out {
		f := e.flags
		if i == len(out)-1 {
			f |= fiemapLast
		}
		res[i] = [3]int{e.off, e.len, f}
	}
	return res
}

func main() {
	f := NewSparseFile(0)
	f.Write(0, block)
	f.Write(2*block, block)
	fmt.Printf("size=%d 实际占用 %d 块\n", f.Size, f.AllocatedBlocks())

	for _, c := range []struct {
		off int
		w   int
		n   string
	}{{0, seekData, "SEEK_DATA"}, {0, seekHole, "SEEK_HOLE"},
		{block, seekData, "SEEK_DATA"}, {4500, seekHole, "SEEK_HOLE"},
		{2 * block, seekHole, "SEEK_HOLE"}, {f.Size, seekData, "SEEK_DATA"}} {
		got, err := f.Lseek(c.off, c.w)
		if err != nil {
			fmt.Printf("  lseek(%d, %s) -> %v\n", c.off, c.n, err)
		} else {
			fmt.Printf("  lseek(%d, %s) -> %d\n", c.off, c.n, got)
		}
	}

	freed, _ := f.Fallocate(fallocPunchHole|fallocKeepSize, 0, block)
	fmt.Printf("打洞: 释放 %d 块, size=%d, 占用 %d 块\n",
		freed, f.Size, f.AllocatedBlocks())

	c := NewSparseFile(5 * block)
	for b := 0; b < 5; b++ {
		c.Data[b] = true
	}
	if _, err := c.Fallocate(fallocCollapseRange, block, 100); err != nil {
		fmt.Printf("collapse 粒度不整 -> %v\n", err)
	}
	if _, err := c.Fallocate(fallocCollapseRange, 4*block, block); err != nil {
		fmt.Printf("collapse 触及 EOF -> %v\n", err)
	}
	c.Fallocate(fallocCollapseRange, block, block)
	keys := []int{}
	for b := range c.Data {
		keys = append(keys, b)
	}
	fmt.Printf("collapse 后 size=%d 块集合=%v\n", c.Size, keys)
}
