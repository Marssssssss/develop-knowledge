package main

import "fmt"


// 5 组文档级语义实验:模型在 vm_model.go,本文件只放实验与断言。

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
	fmt.Println("== demo1 brk/sbrk 语义 ==")
	a := newSpace()
	b0 := a.sbrk(0)
	check("sbrk(0) 返回当前 program break", b0 == heapStart, b0)
	check("初始 break == 堆起点", a.brkManaged() == 0)

	p := a.sbrk(64)
	check("sbrk(+64) 返回**旧的** break", p == b0, p)
	check("新 break = 旧 break + 64", a.sbrk(0) == b0+64, a.sbrk(0))
	check("break 是字节粒度,不按页取整", a.brkManaged() == 64)

	q := a.sbrk(-64)
	check("sbrk(-64) 同样返回旧 break", q == b0+64, q)
	check("break 减回起点", a.sbrk(0) == b0)

	r, err := a.glibcBrk(b0 + 4096)
	check("glibc brk() 成功返回 0", r == 0 && err == nil, r, err)
	_, err = a.glibcBrk(b0 - 1)
	if oe, ok := err.(*osErr); ok {
		check("glibc brk() 失败 errno=ENOMEM", oe.errno == eNomen, oe.errno)
	} else {
		check("glibc brk() 失败必须返回错误", false, err)
	}

	before := a.sbrk(0)
	got := a.sysBrk(b0 - 1) // 系统调用语义:失败返回当前 break
	check("系统调用 brk 失败返回当前 break", got == before, got, before)
	check("与 glibc 包装语义(-1)不同", got != mapFailed)

	a2 := newSpace()
	check("sbrk(0) 与 sysBrk 查询一致", a2.sbrk(0) == a2.sysBrk(a2.brk))
}

func demo2() {
	fmt.Println("== demo2 MAP_ANONYMOUS 匿名映射 ==")
	a := newSpace()
	addr, err := a.mmap(0, 8000, mapPrivate|mapAnon, 0, false)
	check("addr 由内核选择且页对齐", addr%page == 0 && err == nil, addr, err)
	check("长度向上取整到 2 页(8000 -> 8192)", a.find(addr).length == 8192, a.find(addr).length)
	first, _ := a.load(addr, 16)
	allZero := true
	for _, b := range first {
		if b != 0 {
			allZero = false
		}
	}
	check("MAP_ANONYMOUS 内容初始化为 0", allZero)

	a.store(addr+page, 8, 0xA5)
	seg, _ := a.load(addr+page, 8)
	check("写入第 2 页后读回一致", seg[0] == 0xA5 && seg[7] == 0xA5)
	seg1, _ := a.load(addr, 4)
	check("第 1 页仍为 0", seg1[0] == 0 && seg1[3] == 0)

	_, err = a.mmap(0, 0, mapPrivate|mapAnon, 0, false)
	check("length=0 -> EINVAL", isErr(err, eInval))
	_, err = a.mmap(0, page, protRead, 0, false)
	check("未指定共享性 -> EINVAL", isErr(err, eInval))
	_, err = a.mmap(0, page, protRead|mapPrivate|mapShared, 0, false)
	check("PRIVATE|SHARED 同时给 -> EINVAL", isErr(err, eInval))

	two, err := a.mmap(0, page, mapShared|mapAnon, 0, false)
	check("MAP_ANONYMOUS 可与 MAP_SHARED 组合", err == nil && a.find(two) != nil)
	check("第二次内核选址不与第一次重叠", two >= addr+8192 || addr >= two+page, two, addr)
}

func isErr(err error, code int) bool {
	oe, ok := err.(*osErr)
	return ok && oe.errno == code
}

func demo3() {
	fmt.Println("== demo3 offset / munmap 的对齐要求 ==")
	a := newSpace()
	ok1, err := a.mmap(0, page, protRead, mapPrivate, 4096, false)
	check("offset = 1 页 -> 成功", ok1 >= 0 && err == nil)
	_, err = a.mmap(0, page, protRead, mapPrivate, 0, false)
	check("offset = 0 -> 成功", err == nil)
	_, err = a.mmap(0, page, protRead, mapPrivate, 1, false)
	check("offset=1 -> EINVAL(必须为 sysconf(_SC_PAGE_SIZE) 的倍数)", isErr(err, eInval))

	hp, err := a.mmap(0, hugePage, protRead, mapPrivate|mapAnon, hugePage, true)
	check("巨页 offset = 巨页大小 -> 成功", hp >= 0 && err == nil)
	_, err = a.mmap(0, hugePage, protRead, mapPrivate|mapAnon, page, true)
	check("巨页 offset = 1 页 -> EINVAL", isErr(err, eInval))

	region, _ := a.mmap(0, 4*page, protRead|protWrite, mapPrivate|mapAnon, 0, false)
	a.store(region, 4*page, 0x11)
	_, err = a.munmap(region+1, page)
	check("munmap addr 非页对齐 -> EINVAL", isErr(err, eInval))

	_, err = a.munmap(region+page, 16)
	check("munmap length 可不对齐,仍成功", err == nil)
	check("被覆盖的**整页**都被卸载", a.find(region+2*page-1) == nil)
	check("范围外的页不受影响", a.find(region) != nil)
	r, _ := a.munmap(0xDEAD0000, page)
	check("手册:range 内无映射也不算错", r == 0)
}

func demo4() {
	fmt.Println("== demo4 MAP_FIXED 与 MAP_FIXED_NOREPLACE ==")
	a := newSpace()
	base := 0x7F0010000000
	a.mmap(base, 4*page, mapPrivate|mapAnon|mapFixed, 0, false)
	a.store(base, 4*page, 0x22)
	check("MAP_FIXED 精确落在请求地址", a.find(base).start == base)

	_, err := a.mmap(base, page, protRead, mapPrivate|mapAnon|mapNoRepl, 0, false)
	check("MAP_FIXED_NOREPLACE 重叠 -> EEXIST", isErr(err, eExist))
	seg, _ := a.load(base, 1)
	check("失败后已有映射完好", a.find(base) != nil && seg[0] == 0x22)

	a.mmap(base+page, 4*page, mapPrivate|mapAnon|mapFixed, 0, false)
	check("MAP_FIXED 覆盖后旧映射被丢弃", len(a.unmappedSpans) > 0, a.unmappedSpans)
	nz, _ := a.load(base+2*page, 4)
	check("MAP_FIXED 覆盖后新映射就位", nz[0] == 0 && nz[3] == 0)
	old, _ := a.load(base, 1)
	check("未重叠的前一页保留旧内容", old[0] == 0x22)

	hint := 0x7F0020000000
	got, err := a.mmap(hint, page, protRead, mapPrivate|mapAnon, 0, false)
	check("非 FIXED 时 addr 仅作提示", err == nil && got%page == 0 && a.find(got) != nil)
}

func demo5() {
	fmt.Println("== demo5 mmap 阈值:128 KiB 默认与动态调整 ==")
	p := newPolicy()
	check("DEFAULT_MMAP_THRESHOLD = 128*1024", p.mmapThreshold == 131072)
	check("DEFAULT_MMAP_THRESHOLD_MAX(64 位)= 4M*sizeof(long)", mmapThreshM == 33554432)
	check("DEFAULT_MMAP_MAX = 65536", p.mmapMax == 65536)
	check("64 KiB 分配走堆", p.classify(64*1024) == "heap")
	check("恰好 128 KiB 走 mmap(>= 阈值)", p.classify(128*1024) == "mmap")
	check("128 KiB - 1 仍走堆", p.classify(128*1024-1) == "heap")

	check("释放 200 KiB 的块 -> 阈值上调", p.onFree(200*1024) == "raise")
	check("阈值 = 被释放块大小", p.mmapThreshold == 204800, p.mmapThreshold)
	check("trim 阈值动态调整为阈值的 2 倍", p.trimThreshold == 409600, p.trimThreshold)
	check("此时 150 KiB 走堆(阈值已上调)", p.classify(150*1024) == "heap")
	check("再释放 1 MiB 的块 -> 阈值继续上调", p.onFree(1024*1024) == "raise")
	check("阈值 = 1 MiB", p.mmapThreshold == 1048576)
	check("超过上限后不变", p.onFree(mmapThreshM+1) == "keep")
	check("超上限后阈值不变", p.mmapThreshold == 1048576)
	check("上限值本身可上调", p.onFree(mmapThreshM) == "raise")
	check("阈值等于上限", p.mmapThreshold == mmapThreshM)

	p2 := newPolicy()
	p2.mallopt("M_MMAP_THRESHOLD")
	check("显式设置 mallopt 后动态调整被禁用", !p2.dynamic)
	check("被禁用后释放大块不再改阈值", p2.onFree(8*1024*1024) == "frozen")
	check("阈值保持 128 KiB", p2.mmapThreshold == 131072)
	check("M_TOP_PAD 填充量按页取整", alignUp(1) == 4096 && alignUp(4096) == 4096)
	check("M_TRIM_THRESHOLD 默认 128 KiB", trimThresh == 131072)
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
