// Memcached slab 分配器的 Go 实现（与 slab_alloc.py 同构）。
package main

import "fmt"

const (
	pageSize       = 1024 * 1024
	chunkSizeMax   = pageSize / 2
	factor         = 1.25
	chunkSize      = 48
	chunkAlign     = 8
	maxClasses     = 63 + 1
	powerSmallest  = 1
	powerLargest   = 256
	sizeOfItem     = 48 // LP64：字段 42 字节 + data[] 对齐补齐到 48
)

func alignUp(size int) int {
	if size%chunkAlign != 0 {
		size += chunkAlign - size%chunkAlign
	}
	return size
}

// slabClasses 复现 slabs_init 的建表循环。
func slabClasses() (sizes, perslabs []int) {
	i := powerSmallest - 1
	size := float64(sizeOfItem + chunkSize)
	for {
		i++
		if i >= maxClasses-1 {
			break
		}
		if size >= float64(chunkSizeMax)/factor {
			break
		}
		size = float64(alignUp(int(size)))
		sizes = append(sizes, int(size))
		perslabs = append(perslabs, pageSize/int(size))
		size = float64(int(size)) * factor
	}
	// power_largest 单独处理
	sizes = append(sizes, chunkSizeMax)
	perslabs = append(perslabs, pageSize/chunkSizeMax)
	return
}

func classFor(ntotal int, sizes []int) int {
	for i, s := range sizes {
		if ntotal <= s {
			return i + powerSmallest
		}
	}
	return 0
}

// SlabAllocator：极简「页 → class」模型，演示内存卡在某个 class 的问题。
type SlabAllocator struct {
	freePages  int
	pagesOf    map[int]int
	usedChunks map[int]int
	perslab    map[int]int
	evictions  int
}

func newSlabAllocator(totalPages int, perslabs []int) *SlabAllocator {
	m := map[int]int{}
	for i, p := range perslabs {
		m[i+powerSmallest] = p
	}
	return &SlabAllocator{
		freePages: totalPages, pagesOf: map[int]int{},
		usedChunks: map[int]int{}, perslab: m,
	}
}

func (a *SlabAllocator) capacity(cid int) int {
	return a.pagesOf[cid] * a.perslab[cid]
}

func (a *SlabAllocator) Put(cid int) {
	if a.usedChunks[cid] >= a.capacity(cid) {
		if a.freePages > 0 {
			a.freePages--
			a.pagesOf[cid]++
		} else {
			a.evictions++ // 本 class 无空间且无空闲页 → 淘汰
		}
	}
	if a.usedChunks[cid] < a.capacity(cid) {
		a.usedChunks[cid]++
	}
}

// Reassign：只有整页 chunk 都空闲时才能把一页从 src 挪给 dst。
func (a *SlabAllocator) Reassign(src, dst int) bool {
	if a.pagesOf[src] <= 0 {
		return false
	}
	if a.usedChunks[src] > (a.pagesOf[src]-1)*a.perslab[src] {
		return false
	}
	a.pagesOf[src]--
	a.pagesOf[dst]++
	return true
}

// ------------------------------------------------------------ 自检

var fails []string

func check(label string, cond bool, detail string) {
	if cond {
		fmt.Printf("  ok   %s\n", label)
		return
	}
	fails = append(fails, label+" "+detail)
	fmt.Printf("  FAIL %s %s\n", label, detail)
}

func main() {
	sizes, perslabs := slabClasses()
	n := len(sizes)

	fmt.Println("[1] item 与默认值")
	check("sizeof(item) = 48", sizeOfItem == 48, "")
	check("初始 chunk = 96", sizeOfItem+chunkSize == 96, "")
	check("chunk_size 默认 48", chunkSize == 48, "")
	check("slab_chunk_size_max = 512KB", chunkSizeMax == 512*1024, "")
	check("MAX_CLASSES = 64", maxClasses == 64, "")

	fmt.Println("[2] slab class 表")
	check("class 数 = 39", n == 39, fmt.Sprint(n))
	check("class 1 = 96 / 10922", sizes[0] == 96 && perslabs[0] == 10922, "")
	check("class 2 = 120 / 8738", sizes[1] == 120 && perslabs[1] == 8738, "")
	check("class 3 = 152 / 6898", sizes[2] == 152 && perslabs[2] == 6898, "")
	check("最后 class = 512KB / perslab 2",
		sizes[n-1] == chunkSizeMax && perslabs[n-1] == 2, "")
	okAlign, okPerslab, okTail, okMono := true, true, true, true
	for i := range sizes {
		if sizes[i]%chunkAlign != 0 {
			okAlign = false
		}
		if perslabs[i] != pageSize/sizes[i] {
			okPerslab = false
		}
		if pageSize-perslabs[i]*sizes[i] >= sizes[i] {
			okTail = false
		}
		if i > 0 && sizes[i] < sizes[i-1] {
			okMono = false
		}
	}
	check("全部 8 字节对齐", okAlign, "")
	check("perslab = page / size", okPerslab, "")
	check("尾部碎片 < 一个 chunk", okTail, "")
	check("尺寸单调不减", okMono, "")

	fmt.Println("[3] 内部碎片")
	check("100 字节 -> class 2", classFor(100, sizes) == 2, "")
	check("500 字节 -> class 9", classFor(500, sizes) == 9, "")
	check("97 字节 -> class 2", classFor(97, sizes) == 2, "")
	worst := 0.0
	for i := 0; i < n-1; i++ {
		cid := classFor(sizes[i]+1, sizes) - powerSmallest
		f := float64(sizes[cid]-(sizes[i]+1)) / float64(sizes[cid])
		if f > worst {
			worst = f
		}
	}
	check("最坏碎片率接近 25%", worst > 0.20 && worst < 0.26, fmt.Sprint(worst))

	fmt.Println("[4] 1MB 限制与 chunked item")
	check("恰好 512KB 可入最后一 class", classFor(chunkSizeMax, sizes) == n, "")
	check("超过 512KB 放不下", classFor(chunkSizeMax+1, sizes) == 0, "")

	fmt.Println("[5] 内存卡死在某个 class")
	a := newSlabAllocator(64, perslabs)
	small, big := 2, 9
	for i := 0; i < 64*8738; i++ {
		a.Put(small)
	}
	check("小 item 吃满 64 页", a.freePages == 0 && a.pagesOf[small] == 64, "")
	check("大 item class 一页都没有", a.pagesOf[big] == 0, "")
	before := a.evictions
	for i := 0; i < 1000; i++ {
		a.Put(big)
	}
	check("写大 item 全部触发淘汰", a.evictions-before == 1000, fmt.Sprint(a.evictions-before))
	check("大 item 一个都没存下", a.usedChunks[big] == 0, "")
	a.usedChunks[small] = (a.pagesOf[small] - 1) * 8738 // 腾空最后一页
	check("腾空整页后可 reassignment", a.Reassign(small, big), "")
	check("大 item class 拿到 1 页", a.pagesOf[big] == 1, "")
	before = a.evictions
	for i := 0; i < 1000; i++ {
		a.Put(big)
	}
	check("拿到页后不再淘汰", a.evictions == before, "")
	check("大 item 已存入", a.usedChunks[big] > 0, "")
	check("最后一页仍有在用 chunk 时挪不动",
		!newSlabAllocator(2, perslabs).Reassign(small, big), "")

	fmt.Println()
	if len(fails) > 0 {
		fmt.Printf("FAILED %d\n", len(fails))
		return
	}
	fmt.Println("ALL PASS")
}
