// page_cache.go — Linux page cache + writeback 演示(Linux-only)
//
// 运行: go run page_cache.go
//
// 演示:
//   1. write() 后 page cache dirty 状态(读 /proc/meminfo 的 Cached/Dirty)
//   2. posix_fadvise(POSIX_FADV_DONTNEED) 释放 cache 页
//   3. sync_file_range 触发精细 writeback
//   4. POSIX_FADV_RANDOM vs SEQUENTIAL 调整预读窗口

//go:build linux

package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"syscall"
	"unsafe"
)

const (
	fileMB    = 16
	fileSize  = fileMB * 1024 * 1024
	halfSize  = fileSize / 2
	pathF     = "cache_demo.bin"
)

const (
	posixFadvNormal     = 0
	posixFadvRandom     = 1
	posixFadvSequential = 2
	posixFadvWillneed   = 3
	posixFadvDontneed   = 4
)

const (
	syncFileRangeWaitBefore = 1
	syncFileRangeWrite       = 2
	syncFileRangeWaitAfter   = 4
)

func must(err error) {
	if err != nil {
		panic(err)
	}
}

// Linux x86_64 syscall numbers
const (
	sysFadvise64      = 272
	sysSyncFileRange  = 84
)

func fadvise(fd, off, length uintptr, advice int) error {
	_, _, e := syscall.Syscall6(sysFadvise64,
		fd, off, length, uintptr(advice), 0, 0)
	if e != 0 {
		return e
	}
	return nil
}

func syncFileRange(fd, off, length uintptr, flags uint) error {
	_, _, e := syscall.Syscall6(sysSyncFileRange,
		fd, off, length, uintptr(flags), 0, 0)
	if e != 0 {
		return e
	}
	return nil
}

func readMeminfoKB(key string) int64 {
	data, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return -1
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, key) {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				v, _ := strconv.ParseInt(fields[1], 10, 64)
				return v
			}
		}
	}
	return -1
}

func printCacheStatus(label string) {
	cached := readMeminfoKB("Cached:")
	dirty := readMeminfoKB("Dirty:")
	fmt.Printf("[%s] Cached=%d KB, Dirty=%d KB\n", label, cached, dirty)
}

func demo1WriteDirty() {
	fmt.Println("\n=== demo 1: write() fills page cache with dirty pages ===")
	printCacheStatus("before write")

	f, err := os.OpenFile(pathF, os.O_RDWR|os.O_CREATE|os.O_TRUNC, 0644)
	must(err)
	must(f.Truncate(int64(fileSize)))

	buf := make([]byte, fileSize)
	for i := range buf {
		buf[i] = 0x42
	}
	_, err = f.Write(buf)
	must(err)
	fmt.Printf("wrote %d MB to fd\n", fileMB)

	printCacheStatus("after write (no sync)")

	must(f.Sync())
	printCacheStatus("after fsync")
	f.Close()
}

func demo2FadviseDontneed() {
	fmt.Println("\n=== demo 2: posix_fadvise(POSIX_FADV_DONTNEED) releases pages ===")
	fd, err := syscall.Open(pathF, syscall.O_RDONLY, 0)
	must(err)
	defer syscall.Close(fd)

	must(fadvise(uintptr(fd), 0, 0, posixFadvWillneed))
	printCacheStatus("after WILLNEED")

	must(fadvise(uintptr(fd), 0, uintptr(halfSize), posixFadvDontneed))
	printCacheStatus("after DONTNEED 0..8MB")
}

func demo3SyncFileRange() {
	fmt.Println("\n=== demo 3: sync_file_range() fine-grained writeback ===")
	fd, err := syscall.Open(pathF, syscall.O_RDWR, 0)
	must(err)
	defer syscall.Close(fd)

	// 制造一批 dirty 页
	_, err = syscall.Pwrite(fd, []byte("XXXX"), int64(fileSize-4096))
	must(err)
	printCacheStatus("after pwrite (dirty)")

	must(syncFileRange(uintptr(fd), 0, uintptr(fileSize),
		syncFileRangeWaitBefore|syncFileRangeWrite|syncFileRangeWaitAfter))
	printCacheStatus("after sync_file_range(WRITE|WAIT)")

	must(syscall.Fsync(fd))
	printCacheStatus("after fsync")
}

func demo4FadviseReadahead() {
	fmt.Println("\n=== demo 4: POSIX_FADV_RANDOM vs SEQUENTIAL readahead ===")
	fd, err := syscall.Open(pathF, syscall.O_RDONLY, 0)
	must(err)
	defer syscall.Close(fd)

	must(fadvise(uintptr(fd), 0, 0, posixFadvRandom))
	fmt.Println("set POSIX_FADV_RANDOM  (readahead OFF)")

	// 读 backing device 默认 readahead
	if data, err := os.ReadFile("/sys/block/sda/queue/read_ahead_kb"); err == nil {
		if ra, err := strconv.Atoi(strings.TrimSpace(string(data))); err == nil {
			fmt.Printf("backing device default readahead: %d KB (RANDOM → 0, SEQUENTIAL → %d KB)\n",
				ra, ra*2)
		}
	}

	must(fadvise(uintptr(fd), 0, 0, posixFadvSequential))
	fmt.Println("set POSIX_FADV_SEQUENTIAL  (readahead ×2)")
	must(fadvise(uintptr(fd), 0, 0, posixFadvNormal))
	fmt.Println("set POSIX_FADV_NORMAL  (default readahead)")
	_ = unsafe.Sizeof(0)
}

func main() {
	if _, err := os.Stat("/proc/meminfo"); err != nil {
		fmt.Println("This demo requires Linux (/proc/meminfo missing).")
		os.Exit(1)
	}
	demo1WriteDirty()
	demo2FadviseDontneed()
	demo3SyncFileRange()
	demo4FadviseReadahead()
	os.Remove(pathF)
	fmt.Println("\nall 4 demos done")
}