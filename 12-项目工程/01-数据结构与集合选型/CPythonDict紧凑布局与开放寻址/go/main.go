package main

import "fmt"

func join(is []int) string {
	s := ""
	for i, v := range is {
		if i > 0 {
			s += " -> "
		}
		s += fmt.Sprint(v)
	}
	return s
}

func main() {
	fmt.Println("== 探测序列（对应源码注释那张表） ==")
	fmt.Println("  size=8  hash=0 :", join(probeIndices(0, 8, 8)))
	fmt.Println("  size=16 hash=0 :", join(probeIndices(0, 16, 16)))

	fmt.Println("\n== 无删除时的扩容轨迹 ==")
	d := newCompactDict(pyDictLogMinSize)
	prev := d.dkSize()
	fmt.Printf("  start      size=%d capacity=%d\n", prev, d.capacity())
	for n := 1; n <= 43; n++ {
		d.insert(fmt.Sprintf("k%d", n), uint64(n*104729))
		if d.dkSize() != prev {
			fmt.Printf("  第 %2d 个插入 size %d -> %d (GROWTH_RATE(%d)=%d)\n",
				n, prev, d.dkSize(), n-1, growthRate(n-1))
			prev = d.dkSize()
		}
	}

	fmt.Println("\n== 删除不回增 dk_usable ==")
	d2 := newCompactDict(pyDictLogMinSize)
	for n := 0; n < 5; n++ {
		d2.insert(fmt.Sprintf("a%d", n), uint64(n))
	}
	fmt.Printf("  插满 5 个: size=%d usable=%d nentries=%d used=%d\n",
		d2.dkSize(), d2.dkUsable, d2.dkNentry, d2.maUsed)
	d2.delete("a0", 0)
	d2.delete("a1", 1)
	fmt.Printf("  删掉 2 个: size=%d usable=%d nentries=%d used=%d dummies=%d\n",
		d2.dkSize(), d2.dkUsable, d2.dkNentry, d2.maUsed, d2.dummyCount())
	d2.insert("a5", 5)
	fmt.Printf("  再插 1 个: size=%d usable=%d nentries=%d used=%d dummies=%d resizes=%d\n",
		d2.dkSize(), d2.dkUsable, d2.dkNentry, d2.maUsed, d2.dummyCount(), d2.resizes)

	fmt.Println("\n== 扩容反而把表变小（压实） ==")
	d3 := newCompactDict(6)
	for n := 0; n < 42; n++ {
		d3.insert(fmt.Sprintf("b%d", n), uint64(n*31))
	}
	fmt.Printf("  64 槽装满 42: size=%d nentries=%d used=%d\n", d3.dkSize(), d3.dkNentry, d3.maUsed)
	for n := 0; n < 36; n++ {
		d3.delete(fmt.Sprintf("b%d", n), uint64(n*31))
	}
	fmt.Printf("  删掉 36 个  : size=%d nentries=%d used=%d dummies=%d\n",
		d3.dkSize(), d3.dkNentry, d3.maUsed, d3.dummyCount())
	d3.insert("b42", 42*31)
	fmt.Printf("  再插 1 个    : size=%d nentries=%d used=%d dummies=%d\n",
		d3.dkSize(), d3.dkNentry, d3.maUsed, d3.dummyCount())

	fmt.Println("\n== 索引槽宽度 ==")
	for _, k := range []int{3, 8, 16, 32} {
		size := 1 << k
		fmt.Printf("  log2_size=%2d size=%-12d 索引 %d 字节/槽 capacity=%d\n",
			k, size, indexBytesPerSlot(k), usableFraction(size))
	}

	fmt.Println("\n== 预分配 estimate_log2_keysize ==")
	for _, n := range []int{1, 5, 6, 10, 21, 100, 1000} {
		k := estimateLog2Keysize(n)
		fmt.Printf("  n=%-5d -> size=%-6d capacity=%d\n", n, 1<<k, usableFraction(1<<k))
	}
}
