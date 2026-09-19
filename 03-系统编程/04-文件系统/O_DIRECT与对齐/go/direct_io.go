// direct_io.go — O_DIRECT 的对齐规则与硬约束
//
// 运行: go run .
//
// 与 python/direct_io.py 同题：对齐的三个代际、未对齐的两种处理、
// O_DIRECT ≠ O_SYNC、禁止与 fork() 并发、混用的代价。

package main

import (
	"fmt"
	"os"
)

const (
	fsBlock      = 4096 // 2.4 时代：文件系统块大小
	logicalBlock = 512  // 2.6.0 起：块设备逻辑块大小
)

// 数值取自 glibc sysdeps/unix/sysv/linux/bits/fcntl-linux.h
const (
	ODirect       = 0o40000
	OSync         = 0o4010000
	ODSync        = 0o10000
	OSyncMetadata = OSync & ^ODSync // 去掉 O_DSYNC 位后剩下的那一位
)

const (
	strict   = "EINVAL"   // 未对齐直接报错
	fallback = "buffered" // 未对齐静默退回 buffered
)

// Fs 是一个文件系统的 O_DIRECT 画像。
type Fs struct {
	Name           string
	SupportsDirect bool
	MemAlign       int
	OffAlign       int
	Misaligned     string
	Kernel         [3]int
	StatxDioalign  bool
	NFS            bool
}

func statxDioalign(f Fs) (int, int) {
	if f.Kernel[0] < 6 || (f.Kernel[0] == 6 && f.Kernel[1] < 1) || !f.StatxDioalign {
		return 0, 0
	}
	return f.MemAlign, f.OffAlign
}

// AlignmentFor 拿不到 statx 时按内核代际猜。
func AlignmentFor(f Fs) (int, int) {
	if mem, off := statxDioalign(f); mem != 0 {
		return mem, off
	}
	if f.Kernel[0] > 2 || (f.Kernel[0] == 2 && f.Kernel[1] >= 6) {
		return logicalBlock, logicalBlock
	}
	return fsBlock, fsBlock
}

// CheckIO 一次 O_DIRECT 读写能否真的走直接 IO。
func CheckIO(f Fs, bufAddr, offset, length int) (string, error) {
	if !f.SupportsDirect {
		return "", fmt.Errorf("EINVAL: 该文件系统未实现 O_DIRECT")
	}
	mem, off := AlignmentFor(f)
	if mem == 0 {
		return "", fmt.Errorf("EINVAL: 该文件系统不支持 O_DIRECT")
	}
	if bufAddr%mem != 0 || offset%off != 0 || length%off != 0 {
		if f.Misaligned == strict {
			return "", fmt.Errorf("EINVAL: 未对齐（要求 mem=%d off=%d）", mem, off)
		}
		return fallback, nil // 手册：也可能静默退回 buffered I/O
	}
	return "direct", nil
}

// ForkRace O_DIRECT 期间 fork() 的合法性。
func ForkRace(bufferKind string) (string, error) {
	switch bufferKind {
	case "private":
		return "", fmt.Errorf("数据损坏风险：私有映射缓冲区上的 O_DIRECT 与 fork() 并发")
	case "shm", "map_shared", "dontfork":
		return "ok", nil
	}
	return "", fmt.Errorf("未知缓冲区类型: %s", bufferKind)
}

// SyncGuarantee O_DIRECT 自己不等于同步 IO。
func SyncGuarantee(flags int) (bool, bool) {
	data := flags&(OSync|ODSync) != 0
	metadata := flags&OSyncMetadata != 0 // 不能写 flags&OSync
	return data, metadata
}

// IOCost 混用 buffered 与 direct 的等效 IO 量。
func IOCost(mode string, size, switches, overlap int) int {
	cost := size
	if mode == "mixed" {
		cost += switches * 2 * overlap // 写回脏页 + 作废页缓存再重读
	}
	return cost
}

func main() {
	ext4 := Fs{Name: "ext4", SupportsDirect: true, MemAlign: 512, OffAlign: 512,
		Misaligned: strict, Kernel: [3]int{6, 6, 0}, StatxDioalign: true}
	xfs := Fs{Name: "xfs", SupportsDirect: true, MemAlign: 512, OffAlign: 512,
		Misaligned: fallback, Kernel: [3]int{6, 1, 0}, StatxDioalign: true}
	old24 := Fs{Name: "ext3-2.4", SupportsDirect: true,
		Kernel: [3]int{2, 4, 30}}
	tmpfs := Fs{Name: "tmpfs"}

	fmt.Println("== 对齐要求的三个代际 ==")
	for _, f := range []Fs{ext4, old24} {
		mem, off := AlignmentFor(f)
		fmt.Printf("  %-10s -> mem=%d off=%d\n", f.Name, mem, off)
	}

	fmt.Println("== 未对齐的两种命运 ==")
	for _, f := range []Fs{ext4, xfs} {
		mode, err := CheckIO(f, 4096, 1000, 4096)
		fmt.Printf("  %-5s 偏移 1000 -> mode=%q err=%v\n", f.Name, mode, err)
	}

	fmt.Println("== 不支持 O_DIRECT 的文件系统 ==")
	if _, err := CheckIO(tmpfs, 4096, 4096, 4096); err != nil {
		fmt.Printf("  tmpfs -> %v\n", err)
	}

	fmt.Println("== O_DIRECT 不等于 O_SYNC ==")
	for _, fl := range []int{ODirect, ODirect | OSync, ODirect | ODSync} {
		d, m := SyncGuarantee(fl)
		fmt.Printf("  flags=%#09o -> 数据同步=%v 元数据同步=%v\n", fl, d, m)
	}
	fmt.Printf("  glibc: O_SYNC=%#o 含 O_DSYNC=%#o -> %v\n",
		OSync, ODSync, OSync&ODSync == ODSync)

	fmt.Println("== 混用的代价 ==")
	size, overlap, switches := 64*4096, 4096, 10
	fmt.Printf("  纯 direct=%d, 混用=%d, 差=%d\n",
		IOCost("direct", size, 0, 0), IOCost("mixed", size, switches, overlap),
		IOCost("mixed", size, switches, overlap)-IOCost("direct", size, 0, 0))

	if _, err := ForkRace("private"); err != nil {
		fmt.Fprintf(os.Stdout, "== fork() 并发 ==\n  private -> %v\n", err)
	}
}
