// Go 运行时内存分配器模型 —— 与 python/main.py 同题的第二语言实现。
//
// 数据来源：Go 1.24.0 官方源码
//   src/runtime/sizeclasses.go（size class 表）
//   src/runtime/msize.go       （roundupsize 主路径）
//   src/runtime/malloc.go      （mallocgcTiny / 常量）
//
// 说明：本机无 Go 工具链，编译期正确性依赖人工审查；表数据由脚本从官方源码注入，
// 避免手抄错位。断言助手 check() 与 Python 侧同名同语义。
package main

import (
	"fmt"
	"os"
)

// MCache 是 mcache 的最小模型：per-P、无锁；span 耗尽时向 mcentral 补货。
type MCache struct {
	tiny            int
	tinyOffset      int
	tinyAllocs      int
	bump            int
	spans           map[int]int
	mcentralFetches int
	tinySpanLeft    int
}

func newMCache() *MCache {
	return &MCache{bump: 0x1000, spans: make(map[int]int)}
}

func (m *MCache) refill(cls int) {
	m.spans[cls] = classToAllocnpages[cls] * pageSize / classToSize[cls]
	m.mcentralFetches++
}

// mallocgc 返回 (地址, 本次占用的槽位大小)。
func (m *MCache) mallocgc(size int, noscan bool) (int, int) {
	if size < tinySize && noscan {
		return m.mallocgcTiny(size)
	}
	blk := roundupsize(size)
	cls := sizeClassOf(blk)
	if cls < 0 {
		// 大对象（> maxSmallReq）不落在任何 size class 上，直接从 mheap 拿页。
		addr := m.bump
		m.bump += blk
		return addr, blk
	}
	if m.spans[cls] == 0 {
		m.refill(cls)
	}
	m.spans[cls]--
	addr := m.bump
	m.bump += blk
	return addr, blk
}

// mallocgcTiny 对齐规则逐字照抄 malloc.go：8 的倍数→8；4 的倍数→4；2 的倍数→2；奇数不额外对齐。
func (m *MCache) mallocgcTiny(size int) (int, int) {
	off := m.tinyOffset
	switch {
	case size%8 == 0:
		off = (off + 7) &^ 7
	case size%4 == 0:
		off = (off + 3) &^ 3
	case size%2 == 0:
		off = (off + 1) &^ 1
	}
	if off+size <= tinySize && m.tiny != 0 {
		m.tinyOffset = off + size
		m.tinyAllocs++
		return m.tiny + off, tinySize
	}
	if m.tinySpanLeft == 0 {
		m.refill(tinySizeClass)
		m.tinySpanLeft = m.spans[tinySizeClass]
		m.spans[tinySizeClass] = 0
	}
	m.tinySpanLeft--
	m.tiny = m.bump
	m.bump += tinySize
	m.tinyOffset = size
	m.tinyAllocs++
	return m.tiny, tinySize
}

func main() {
	fmt.Println("=== A. size class 表完整性（官方 Go 1.24 src/runtime/sizeclasses.go）===")
	check("A1 共 68 个 size class（0 号未使用）", len(classToSize) == 68, len(classToSize))
	check("A2 class 0 为 0 字节且 class 1 为 8 字节",
		classToSize[0] == 0 && classToSize[1] == 8)
	inc := true
	for i := 1; i < 67; i++ {
		if classToSize[i] >= classToSize[i+1] {
			inc = false
		}
	}
	check("A3 尺寸严格递增", inc)
	check("A4 最大小对象 32768 == gc.MaxSmallSize", classToSize[67] == maxSmallSize)
	check("A5 tinySizeClass=2 对应 16 B（源码自断言）",
		classToSize[tinySizeClass] == tinySize, classToSize[tinySizeClass])

	fmt.Println("\n=== B. span 布局：objects / tail waste / max waste 全表复核 ===")
	badObj, badTail, badSum, badWaste := []int{}, []int{}, []int{}, []int{}
	for c := 1; c < 68; c++ {
		span := classToAllocnpages[c] * pageSize
		size := classToSize[c]
		objs := span / size
		if objs != officialObjects[c-1] {
			badObj = append(badObj, c)
		}
		waste := span - objs*size
		if waste != officialTailWaste[c-1] {
			badTail = append(badTail, c)
		}
		if span != objs*size+waste {
			badSum = append(badSum, c)
		}
		bp := int((1 - float64((classToSize[c-1]+1)*objs)/float64(span))*10000 + 0.5)
		if bp != officialMaxWasteBP[c-1] {
			badWaste = append(badWaste, c)
		}
	}
	check("B1 67 个 class 的 objects 与官方注释表逐行一致", len(badObj) == 0, badObj)
	check("B2 67 个 class 的 tail waste 与官方注释表逐行一致", len(badTail) == 0, badTail)
	check("B3 span = objects × size + tail waste 对 67 行全部成立", len(badSum) == 0, badSum)
	check("B4 max waste 可由 1-(前一类尺寸+1)×objects/span 复算到万分之一",
		len(badWaste) == 0, badWaste)
	check("B5 class 1 的 max waste 上限 87.5%（1 B 请求塞进 8 B 槽）",
		officialMaxWasteBP[0] == 8750)
	check("B6 240→256 是零浪费分界", classToSize[17] == 240 && classToSize[18] == 256 &&
		8192%240 != 0 && 8192%256 == 0)

	fmt.Println("\n=== C. 两级快速映射表 ===")
	check("C1 size_to_class8 长度 = 1024/8+1 = 129", len(sizeToClass8) == 129, len(sizeToClass8))
	check("C2 size_to_class128 长度 = 31744/128+1 = 249",
		len(sizeToClass128) == 249, len(sizeToClass128))
	same, diff := true, -1
	for i, v := range sizeToClass128 {
		if v != officialSizeToClass128[i] {
			same, diff = false, i
			break
		}
	}
	check("C3 size_to_class128 与源码数组 249 项逐项一致", same, diff)
	check("C4 class8 上界落到 class 32（正好 1024 B）", sizeToClass8[128] == 32)
	check("C5 8 B 请求→class 1；9 B 请求→class 2（8 B 粒度）",
		sizeToClass8[1] == 1 && sizeToClass8[divRoundUp(9, 8)] == 2)
	check("C6 1025 B 走 128 粒度 → class 33（1152）",
		sizeToClass128[divRoundUp(1025-smallSizeMax, largeSizeDiv)] == 33)

	fmt.Println("\n=== D. roundupsize：请求字节 → 实际 block ===")
	cases := [][2]int{{1, 8}, {8, 8}, {9, 16}, {17, 24}, {24, 24}, {25, 32}, {33, 48},
		{48, 48}, {49, 64}, {65, 80}, {1024, 1024}, {1025, 1152}, {1281, 1408},
		{1409, 1536}, {8193, 9472}, {32768, 32768}}
	for _, c := range cases {
		got := roundupsize(c[0])
		check(fmt.Sprintf("D req=%d → %d", c[0], c[1]), got == c[1], got)
	}
	notSmall, mono, prev := true, true, 0
	for req := 1; req <= maxSmallSize; req++ {
		got := roundupsize(req)
		if got < req {
			notSmall = false
		}
		if got < prev {
			mono = false
		}
		prev = got
	}
	check("D17 1..32768 全量扫描：结果恒 >= 请求", notSmall)
	check("D18 1..32768 全量扫描：结果单调不减", mono)

	fmt.Println("\n=== E. 大对象路径：按页对齐 ===")
	check("E1 32769 → 40960（5 页）", roundupsize(32769) == 40960, roundupsize(32769))
	check("E2 65536 恰好整页不额外放大", roundupsize(65536) == 65536)
	check("E3 65537 → 73728", roundupsize(65537) == 73728, roundupsize(65537))
	check("E4 大对象结果不落在任何 size class 上", sizeClassOf(roundupsize(40000)) == -1)

	fmt.Println("\n=== F. tiny allocator（< 16 B 且不含指针）===")
	mc := newMCache()
	a1, e1 := mc.mallocgc(5, true)
	a2, e2 := mc.mallocgc(3, true)
	a3, e3 := mc.mallocgc(6, true)
	check("F1 三次 tiny 分配落在同一个 16 B block 内（偏移 0/5/8）",
		a1 == mc.tiny && a2-a1 == 5 && a3-a1 == 8 && a2 < a1+tinySize && a3 < a1+tinySize)
	check("F2 子对象偏移按 size 累加（5 → 8）", a2-a1 == 5 && a3-a1 == 8)
	check("F3 tinyAllocs 计数为 3", mc.tinyAllocs == 3)
	check("F4 elemsize 恒为 16（整个 block 归 class 2 span 管）",
		e1 == tinySize && e2 == tinySize && e3 == tinySize)
	mc.mallocgc(6, true)
	check("F5 第 4 次（14+6 > 16）另开销 block，计数 4", mc.tinyAllocs == 4)

	mc2 := newMCache()
	mc2.mallocgc(5, true)
	b9, _ := mc2.mallocgc(9, false)
	check("F6 含指针对象不得进 tiny（9 B 走 size class 路径）", b9 != mc2.tiny)
	check("F7 含指针 9 B 的实际槽位是 16 B（class 2）", roundupsize(9) == 16)

	mc3 := newMCache()
	c0, _ := mc3.mallocgc(8, true)
	c1, _ := mc3.mallocgc(1, true)
	check("F8 size%8==0 时在 block 内按 8 字节对齐", c0%8 == 0)
	check("F9 奇数尺寸不引入对齐填充（1 紧跟在 8 之后）", c1-c0 == 8, c1-c0)

	mc4 := newMCache()
	d16, _ := mc4.mallocgc(16, true)
	check("F10 size == 16 不进 tiny（必须能被显式释放）", d16 != mc4.tiny)
	check("F11 16 B 请求 → class 2 槽位 16 B", roundupsize(16) == 16)

	fmt.Println("\n=== G. 三级缓存：mcache → mcentral → mheap ===")
	check("G1 spanClass = size class × 2（scan / noscan 各一套），共 136",
		spanClassCount == 136)
	enc := true
	for c := 0; c < 68; c++ {
		if (c<<1)>>1 != c || ((c<<1)|1)>>1 != c {
			enc = false
		}
	}
	check("G2 noscan spanClass = class<<1，scan = class<<1|1（可逆编码）", enc)
	mc5 := newMCache()
	for i := 0; i < 1000; i++ {
		mc5.mallocgc(48, false)
	}
	check("G3 1000 个 48 B 对象只需 6 次向 mcentral 补货（每 span 170 个）",
		mc5.mcentralFetches == 6, mc5.mcentralFetches)
	check("G4 补货次数 = ceil(1000 / (8192/48)) = 6", divRoundUp(1000, 8192/48) == 6)
	mc6 := newMCache()
	for i := 0; i < 512; i++ {
		mc6.mallocgc(5, true)
	}
	check("G5 512 个 tiny block 恰好吃光 1 个 class 2 span（512 个槽）",
		mc6.mcentralFetches == 1, mc6.mcentralFetches)

	fmt.Println("\n=== H. 统计口径：同样请求次数，size class 决定实际占用 ===")
	footprint := func(n, req int) int {
		m := newMCache()
		total := 0
		for i := 0; i < n; i++ {
			_, blk := m.mallocgc(req, false)
			total += blk
		}
		return total
	}
	check("H1 10 万个 1 B 对象实际占 800 KB（8 倍放大）", footprint(100000, 1) == 800000)
	check("H2 10 万个 100 B 对象占 11.2 MB（112 B 槽）", footprint(100000, 100) == 11200000)
	check("H3 100 B 请求的槽位浪费率 = 12/112 = 10.71%",
		int((1-100.0/112)*10000+0.5) == 1071)
	check("H4 换成 96 B 请求浪费率 0%（正好命中 class 8）", roundupsize(96) == 96)

	fmt.Println("\n" + "=== 汇总 ===")
	fmt.Printf("断言总数 %d；失败 %d %v\n", statN, len(statFail), statFail)
	if len(statFail) > 0 {
		os.Exit(1)
	}
}
