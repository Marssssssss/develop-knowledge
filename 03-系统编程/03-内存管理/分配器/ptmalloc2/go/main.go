// ptmalloc2 —— 5 组实验:chunk 尺寸公式 / bin 索引 / malloc 顺序 / free 顺序 / tcache 语义。
//
// 模型在 pm_model.go,本文件只放实验与断言。运行: go run .
package main

import "fmt"

var (
	fails []string
	total int
)

func check(label string, cond bool, detail ...interface{}) {
	total++
	if cond {
		fmt.Println("  [ok]", label)
		return
	}
	fails = append(fails, label)
	fmt.Println("  [FAIL]", label, detail)
}

func demo1() {
	fmt.Println("== demo1 chunk 布局与尺寸公式 ==")
	check("SIZE_SZ = 8(64 位)", sizeSz == 8)
	check("MALLOC_ALIGNMENT = 2*SIZE_SZ = 16", mallocAlign == 16)
	check("CHUNK_HDR_SZ = 2*SIZE_SZ = 16(prev_size + size)", chunkHdrSz == 16)
	check("最小 chunk = offsetof(fd_nextsize) = 4*sizeof(void*) = 32", minChunkSz == 32)
	check("MINSIZE 对齐后仍为 32", minSize == 32)
	check("三个标志位正好占低 3 位", prevInuse|isMmapped|nonMainArena == 0x07)
	check("PREV_INUSE=0x01 / IS_MMAPPED=0x02 / NON_MAIN_ARENA=0x04",
		prevInuse == 1 && isMmapped == 2 && nonMainArena == 4)
	check("prev_size 只在前一块空闲时才有效",
		(&chunk{size: 48, inuse: false}).prevSizeValid() &&
			!(&chunk{size: 48, inuse: true}).prevSizeValid())

	check("request2size(0) = MINSIZE = 32", request2size(0) == 32)
	check("request2size(1) = 32", request2size(1) == 32)
	check("request2size(24) = 32", request2size(24) == 32)
	check("request2size(25) = 48", request2size(25) == 48, request2size(25))
	check("request2size(40) = 48(tcache 注释:idx1 覆盖 25..40)", request2size(40) == 48)
	check("request2size(41) = 64", request2size(41) == 64)
	check("request2size(1000) = 1008(非 1024)", request2size(1000) == 1008, request2size(1000))
	check("request2size(112) = 128", request2size(112) == 128)
	check("request2size(64) = 80(常见误算点)", request2size(64) == 80, request2size(64))

	aligned := true
	mono := true
	for n := 0; n < 4096; n++ {
		if request2size(n)%mallocAlign != 0 {
			aligned = false
		}
		if request2size(n) > request2size(n+1) {
			mono = false
		}
	}
	check("尺寸总是 MALLOC_ALIGNMENT 的倍数", aligned)
	check("request2size 单调不减", mono)
}

func demo2() {
	fmt.Println("== demo2 bin 索引与 tcache 索引映射 ==")
	check("NSMALLBINS=64 / NBINS=128", nSmallBins == 64 && nBins == 128)
	check("SMALLBIN_CORRECTION = (MALLOC_ALIGNMENT > CHUNK_HDR_SZ) = 0", smallCorr == 0)
	check("MIN_LARGE_SIZE = 64*16 = 1024", minLargeSz == 1024)
	check("1024 以下走 smallbin", inSmallbinRange(1008))
	check("1024 起算 large", !inSmallbinRange(1024))

	check("smallbin_index(32) = 2", smallbinIndex(32) == 2)
	check("smallbin_index(48) = 3", smallbinIndex(48) == 3)
	check("smallbin_index(1008) = 63", smallbinIndex(1008) == 63)
	check("smallbin 占 bin 2..63 共 62 个(1 号是 unsorted)", 64-2 == 62)
	check("largebin_index_64(1024) = 64", largebinIndex64(1024) == 64, largebinIndex64(1024))
	check("largebin_index_64(3072) = 96", largebinIndex64(3072) == 96, largebinIndex64(3072))
	check("largebin_index_64(3136) = 97", largebinIndex64(3136) == 97, largebinIndex64(3136))
	check("largebin_index_64(5120) = 101", largebinIndex64(5120) == 101, largebinIndex64(5120))
	check("bin_index 在 1008->1024 处连续(63 -> 64)",
		binIndex(1008) == 63 && binIndex(1024) == 64)
	ok := true
	mono := true
	for s := 32; s < (4 << 20); s += 7 {
		if binIndex(s) >= nBins {
			ok = false
		}
		if binIndex(s) > binIndex(s+13) {
			mono = false
		}
	}
	check("bin 索引不超过 NBINS-1", ok)
	check("bin_index 单调不减", mono)

	check("tcache 注释:idx0 覆盖 0..24 字节请求", usize2tidx(0) == 0 && usize2tidx(24) == 0)
	check("tcache 注释:idx1 覆盖 25..40", usize2tidx(25) == 1 && usize2tidx(40) == 1)
	check("tcache 注释:idx2 覆盖 41..56", usize2tidx(41) == 2 && usize2tidx(56) == 2)
	st := true
	for c := 32; c < 4096; c += 16 {
		if tidx2csize(csize2tidx(c)) != c {
			st = false
		}
	}
	check("tidx2csize(csize2tidx(x)) == x", st)
	check("tcache idx0 的 chunksize = MINSIZE = 32", tidx2csize(0) == 32)
	check("master:TCACHE_MAX_BINS = 64 + 12 = 76", tcacheMaxBinsMaster == 76)
	check("master:idx75 的 chunksize = 1232", tidx2csize(75) == 1232)
	check("2.35:TCACHE_MAX_BINS = 64(还没有 large bins)", tcacheMaxBins235 == 64)
	check("每 bin 上限:master 16 个,2.35 为 7 个(版本差异)",
		tcacheFillMaster == 16 && tcacheFill235 == 7)
}

func demo3() {
	fmt.Println("== demo3 malloc 的查找顺序 ==")
	a := newArena(tcacheFillMaster, 8)
	a.malloc(100)
	check("冷启动一路降到 top 分割", a.path[len(a.path)-1] == "split-top", a.path)
	check("第一步先做 mmap 阈值判定", a.path[0] == "check-mmap-threshold")
	check("小请求尺寸不匹配 fastbin", !contains(a.path, "fastbin-hit"))

	a.malloc(100 * 1024)
	check("100 KiB < 128 KiB 阈值:不走 mmap", !contains(a.path, "mmap-direct"))
	a.malloc(200 * 1024)
	check("200 KiB >= 阈值:直接 mmap", contains(a.path, "mmap-direct"))
	check("mmap 分配计入 munmaps", a.munmaps == 1, a.munmaps)

	b := newArena(tcacheFillMaster, 8)
	x := b.malloc(64)
	b.free(x)
	b.resetPath()
	y := b.malloc(64)
	check("刚释放的块命中 tcache",
		len(b.path) == 2 && b.path[0] == "check-mmap-threshold" && b.path[1] == "tcache-hit", b.path)
	check("tcache 返回同一个 chunk", y == x)

	d := newArena(0, 8)
	z := d.malloc(64)
	d.free(z)
	d.resetPath()
	d.malloc(64)
	check("tcache 满/关闭时落到 fastbin", contains(d.path, "fastbin-hit"), d.path)
	check("fastbin 在 tcache 之后才被查",
		indexOf(d.path, "fastbin-hit") > indexOf(d.path, "tcache-miss-exact-only"))

	e := newArena(0, 8)
	big := e.malloc(2048)
	e.malloc(64) // 隔离块,让 big 释放后不并入 top
	e.free(big)
	e.resetPath()
	got := e.malloc(2048)
	check("大请求先从 unsorted 取回", contains(e.path, "unsorted-scan-hit"), e.path)
	check("大请求会先把 fastbin 清进 unsorted",
		contains(e.path, "large:flush-fastbins-to-unsorted"))
	check("取回的 chunk 尺寸足够", got.size >= 2048)
}

func demo4() {
	fmt.Println("== demo4 free 的顺序与'不还给 OS' ==")
	a := newArena(tcacheFillMaster, 8)
	c := a.malloc(64)
	a.free(c)
	check("free 第一站是 tcache", a.path[0] == "tcache-store", a.path)

	b := newArena(0, 8)
	m := b.malloc(64)
	b.free(m)
	check("tcache 满则进 fastbin", len(b.path) == 1 && b.path[0] == "fastbin", b.path)
	check("fastbin 以 chunksize 为键", len(b.fastbins[request2size(64)]) == 1)

	d := newArena(0, 8)
	p := d.malloc(4096)
	d.free(p)
	check("大块 free 先做合并", contains(d.path, "coalesce"), d.path)
	check("合并后紧邻堆顶 -> 被并入 top", contains(d.path, "absorb-into-top"), d.path)
	check("堆顶释放不进任何 bin", !contains(d.path, "unsorted"))
	check("并入 top 后 top 恢复原大小", d.topSize == topChunkSize, d.topSize)

	e := newArena(0, 8)
	e.malloc(4096)
	mid := e.malloc(4096)
	e.malloc(64) // 隔离块
	e.free(mid)
	check("非堆顶释放 -> 进 unsorted", contains(e.path, "unsorted"), e.path)
	check("unsorted 长度 +1", len(e.unsorted) == 1, len(e.unsorted))

	f := newArena(0, 8)
	blocks := []*chunk{}
	for i := 0; i < 3; i++ {
		blocks = append(blocks, f.malloc(4096))
		blocks = append(blocks, f.malloc(64)) // 隔离块,阻止相邻合并
	}
	for i := 0; i < len(blocks); i += 2 {
		f.free(blocks[i])
	}
	check("多次释放后 unsorted 累积到 3", len(f.unsorted) == 3, len(f.unsorted))
	check("free 不等于归还 OS:arena 总量不变", f.inArenaBytes() == topChunkSize, f.inArenaBytes())
	check("free 之后 in-use 字节数只剩隔离块",
		f.heapBytes() == 3*request2size(64), f.heapBytes())
	check("未达 trim 阈值不触发修剪", f.trims == 0 && f.trimThreshold == defaultTrimThresh)

	g := newArena(0, 8)
	h := g.malloc(256 * 1024)
	g.free(h)
	check("mmap 来的块 free 时直接 munmap", len(g.path) == 1 && g.path[0] == "munmap", g.path)
}

func demo5() {
	fmt.Println("== demo5 tcache 语义与上层策略 ==")
	a := newArena(tcacheFillMaster, 8)
	check("master:每 bin 上限 16", a.tcacheCount == 16)

	c100 := a.malloc(100)
	check("request 100 -> chunk 112", c100.size == 112, c100.size)
	check("chunk 112 的 tcache idx = 5", csize2tidx(112) == 5)
	c112 := a.malloc(112)
	check("request 112 -> chunk 128", c112.size == 128, c112.size)
	check("chunk 128 的 tcache idx = 6", csize2tidx(128) == 6)

	a.free(c112)
	a.resetPath()
	a.malloc(100)
	check("tcache 只做**精确尺寸**匹配:112 的请求拿不到 128 的块",
		!contains(a.path, "tcache-hit"), a.path)
	check("此时回落到普通路径而非'用更大的 chunk'",
		a.path[len(a.path)-1] == "split-top", a.path)

	b := newArena(tcacheFillMaster, 8)
	xs := make([]*chunk, 20)
	for i := range xs {
		xs[i] = b.malloc(64)
	}
	for _, x := range xs {
		b.free(x)
	}
	overflow := 0
	for _, v := range b.fastbins {
		overflow += len(v)
	}
	check("tcache 装到上限即止",
		len(b.tcache[csize2tidx(request2size(64))]) == b.tcacheCount)
	check("溢出部分落到 fastbin", overflow == 20-b.tcacheCount, overflow)
	check("tcache 索引与 chunksize 互为逆映射",
		tidx2csize(csize2tidx(request2size(64))) == request2size(64))

	check("arena 上限 = 8 * CPU 核数(手册原文)", newArena(0, 4).arenaMax == 32)
	check("arena 上限随核数线性增长", newArena(0, 16).arenaMax == 128)
	check("M_MMAP_MAX 默认 65536(设 0 即禁用 mmap 路径)", newArena(0, 8).mmapMax == 65536)
}

func contains(s []string, want string) bool { return indexOf(s, want) >= 0 }

func indexOf(s []string, want string) int {
	for i, x := range s {
		if x == want {
			return i
		}
	}
	return -1
}

func main() {
	demo1()
	fmt.Println()
	demo2()
	fmt.Println()
	demo3()
	fmt.Println()
	demo4()
	fmt.Println()
	demo5()
	fmt.Println()
	fmt.Printf("断言总数 %d,失败 %d\n", total, len(fails))
	if len(fails) > 0 {
		for _, f := range fails {
			fmt.Println("  FAILED:", f)
		}
		return
	}
	fmt.Println("全部通过")
}
