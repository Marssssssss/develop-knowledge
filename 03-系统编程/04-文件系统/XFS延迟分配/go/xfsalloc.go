// XFS 延迟分配与 allocsize 的 Go 侧镜像。
//
// 常量与算法同 Python 侧，全部来自 fs/xfs/ 源码：
//   xfs_format.h  —— BMBT_* 位宽与 XFS_MAX_BMBT_EXTLEN
//   xfs_mount.h   —— XFS_MIN_IO_LOG / XFS_MAX_IO_LOG
//   xfs_super.c   —— 默认 m_allocsize_log = 16、ffs(size)-1
//   xfs_mount.c   —— m_allocsize_blocks 换算、低空间阈值
//   xfs_iomap.c   —— xfs_iomap_prealloc_size / xfs_iomap_freesp
package main

import "fmt"

const (
	bmbtStartoffBitlen   = 54
	bmbtBlockcountBitlen = 21

	// XFS_MAX_BMBT_EXTLEN = (1 << 21) - 1
	xfsMaxBmbtExtlen = 1<<bmbtBlockcountBitlen - 1
	// XFS_MAX_FILEOFF = BMBT_STARTOFF_MASK + BMBT_BLOCKCOUNT_MASK
	xfsMaxFileoff = 1<<bmbtStartoffBitlen - 1 + xfsMaxBmbtExtlen

	pageShift            = 12
	xfsMinIoLog          = pageShift
	xfsMaxIoLog          = 30
	xfsDefaultAllocsLog  = 16
	xfsLowspMax          = 5
	xfsLowsp1Pcnt        = 0
	xfsLowsp5Pcnt        = 4
	nullFsBlock          = -1
	blockLog             = 12
	blockSize            = 1 << blockLog
)

// Ffs 返回最低置位的 1-based 下标，0 表示无置位。
func Ffs(x int) int {
	if x == 0 {
		return 0
	}
	return 1 + bits(x&-x)
}

func bits(x int) int {
	n := 0
	for x > 1 {
		x >>= 1
		n++
	}
	return n
}

func roundupPowOfTwo(x int) int {
	if x <= 1 {
		return 1
	}
	return 1 << (bits(x-1) + 1)
}

func rounddownPowOfTwo(x int) int {
	if x <= 0 {
		return 0
	}
	return 1 << bits(x)
}

// ParseAllocsize 对应 xfs_fs_parse_param 的 Opt_allocsize 分支。
func ParseAllocsize(valueBytes int) (int, bool) {
	log := Ffs(valueBytes) - 1
	return log, log >= xfsMinIoLog && log <= xfsMaxIoLog
}

// AllocsizeBlocks 对应 xfs_mount.c 里的换算。
func AllocsizeBlocks(allocsizeLog, blog int) int {
	return 1 << uint(allocsizeLog-blog)
}

// SetLowSpaceThresholds 先整除 100 再乘 1..5。
func SetLowSpaceThresholds(dblocks int) [xfsLowspMax]int {
	var out [xfsLowspMax]int
	d := dblocks / 100
	for i := 0; i < xfsLowspMax; i++ {
		out[i] = d * (i + 1)
	}
	return out
}

// IomapFreesp 返回节流用的 shift。
func IomapFreesp(freeBlocks int, lowSpace [xfsLowspMax]int) int {
	shift := 0
	if freeBlocks < lowSpace[xfsLowsp5Pcnt] {
		shift = 2
		if freeBlocks < lowSpace[xfsLowsp1Pcnt+3] {
			shift++
		}
		if freeBlocks < lowSpace[xfsLowsp1Pcnt+2] {
			shift++
		}
		if freeBlocks < lowSpace[xfsLowsp1Pcnt+1] {
			shift++
		}
		if freeBlocks < lowSpace[xfsLowsp1Pcnt] {
			shift++
		}
	}
	return shift
}

// Extent 是 xfs_bmbt_irec 的三要素，Startblock 为 nullFsBlock 表示 delalloc。
type Extent struct {
	Startoff   int
	Blockcount int
	Startblock int
}

func (e Extent) IsNullStartblock() bool { return e.Startblock == nullFsBlock }

// Inode 是 xfs_iomap_prealloc_size 需要的状态。
type Inode struct {
	IsizeBytes int
	Extents    []Extent
	Blocklog   uint
}

func (ip *Inode) Fsb(nbytes int) int      { return nbytes >> ip.Blocklog }
func (ip *Inode) FsbToB(nblocks int) int  { return nblocks << ip.Blocklog }

// PrevExtent 返回下标 idx 之前的一条及其下标。
func (ip *Inode) PrevExtent(idx int) (*Extent, int) {
	j := idx - 1
	if j < 0 {
		return nil, j
	}
	return &ip.Extents[j], j
}

// IomapPreallocSize 是 xfs_iomap_prealloc_size 的逐行转写。
func IomapPreallocSize(ip *Inode, offsetBytes, icur, allocsizeBlocks,
	dalignBlocks, freeBlocks int, lowSpace [xfsLowspMax]int,
	qblocks int, quotaShift int) int {
	offsetFsb := ip.Fsb(offsetBytes)

	if ip.IsizeBytes < ip.FsbToB(allocsizeBlocks) {
		return 0
	}

	prev, cur := ip.PrevExtent(icur)
	if prev == nil || ip.IsizeBytes < ip.FsbToB(dalignBlocks) ||
		prev.Startoff+prev.Blockcount < offsetFsb {
		return allocsizeBlocks
	}

	plen := prev.Blockcount
	for {
		got, nxt := ip.PrevExtent(cur)
		cur = nxt
		if got == nil {
			break
		}
		if plen > xfsMaxBmbtExtlen/2 || got.IsNullStartblock() ||
			got.Startoff+got.Blockcount != prev.Startoff ||
			got.Startblock+got.Blockcount != prev.Startblock {
			break
		}
		plen += got.Blockcount
		prev = got
	}

	allocBlocks := plen * 2
	if allocBlocks > xfsMaxBmbtExtlen {
		allocBlocks = ip.Fsb(offsetBytes)
	}
	allocBlocks = min(roundupPowOfTwo(xfsMaxBmbtExtlen), allocBlocks)

	shift := IomapFreesp(freeBlocks, lowSpace)
	if qblocks > 0 {
		allocBlocks = min(allocBlocks, qblocks)
	}
	if quotaShift > shift {
		shift = quotaShift
	}
	if shift != 0 {
		allocBlocks >>= uint(shift)
	}
	if allocBlocks != 0 {
		allocBlocks = rounddownPowOfTwo(allocBlocks)
	}
	if allocBlocks > xfsMaxBmbtExtlen {
		allocBlocks = xfsMaxBmbtExtlen
	}
	for allocBlocks != 0 && allocBlocks >= freeBlocks {
		allocBlocks >>= 4
	}
	if allocBlocks < allocsizeBlocks {
		allocBlocks = allocsizeBlocks
	}
	return allocBlocks
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func main() {
	fmt.Println("== 常量 ==")
	fmt.Printf("  XFS_MAX_BMBT_EXTLEN = %d（2 的幂？%v）\n",
		xfsMaxBmbtExtlen, rounddownPowOfTwo(xfsMaxBmbtExtlen) == xfsMaxBmbtExtlen)
	fmt.Printf("  XFS_MAX_FILEOFF     = %d\n", xfsMaxFileoff)
	fmt.Printf("  MIN_IO_LOG=%d MAX_IO_LOG=%d 默认 allocsize_log=%d\n",
		xfsMinIoLog, xfsMaxIoLog, xfsDefaultAllocsLog)

	fmt.Println("== allocsize 解析 ==")
	for _, v := range []int{4, 64, 96, 128} {
		log, ok := ParseAllocsize(v * 1024)
		got := 0
		if log >= 0 {
			got = 1 << uint(log)
		}
		fmt.Printf("  %-6dk → log=%-3d 合法=%-5v 实际=%d KiB\n",
			v, log, ok, got/1024)
	}

	low := SetLowSpaceThresholds(1_000_000)
	fmt.Printf("== 节流阈值 %v ==\n", low)
	for _, f := range []int{60_000, 49_999, 39_999, 29_999, 19_999, 9_999} {
		fmt.Printf("  free=%-7d → shift=%d\n", f, IomapFreesp(f, low))
	}

	fmt.Println("== 动态投机预分配 ==")
	ab := 16
	big := SetLowSpaceThresholds(20_000_000)
	shapes := []struct {
		name string
		ip   Inode
		off  int
	}{
		{"小文件 isize=16KiB", Inode{16 * 1024, []Extent{{0, 4, 0x1000}}, blockLog}, 4 * blockSize},
		{"单条前驱 4 块", Inode{64 * blockSize, []Extent{{0, 4, 0x1000}}, blockLog}, 4 * blockSize},
		{"单条前驱 64 块", Inode{1 << 22, []Extent{{0, 64, 0x1000}}, blockLog}, 64 * blockSize},
		{"两条物理连续", Inode{1 << 22, []Extent{{0, 8, 0x1000}, {8, 8, 0x1008}}, blockLog}, 16 * blockSize},
		{"物理不连续", Inode{1 << 22, []Extent{{0, 8, 0x1000}, {8, 8, 0x9000}}, blockLog}, 16 * blockSize},
	}
	for _, c := range shapes {
		ip := c.ip
		icur := len(ip.Extents)
		for i, e := range ip.Extents {
			if e.Startoff >= ip.Fsb(c.off) {
				icur = i
				break
			}
		}
		r := IomapPreallocSize(&ip, c.off, icur, ab, 0, 10_000_000, big, 0, 0)
		fmt.Printf("  %-16s → %d 块（%d KiB）\n", c.name, r, r*4)
	}
}
