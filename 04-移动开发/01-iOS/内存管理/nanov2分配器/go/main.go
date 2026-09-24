package main

import "fmt"

func main() {
	fmt.Println("== 尺寸类 ==")
	for _, size := range []int{1, 16, 17, 100, 200, 256, 257} {
		sc := sizeClassFromSize(size)
		good := goodSize(size)
		where := "nanov2"
		if good > nanoMaxSize {
			where = "交给 helper zone"
		}
		fmt.Printf("  malloc(%4d) -> good_size=%3d class=%2d(%3d 字节) %s\n",
			size, good, sc, sizeFromSizeClass(sc), where)
	}

	fmt.Println("\n== 各尺寸类的块内槽位与浪费 ==")
	for sc := 0; sc < nanoSizeClasses; sc++ {
		n := slotsBySizeClass(sc)
		fmt.Printf("  class %2d: %3d 字节 x %4d 槽, 浪费 %3d 字节\n",
			sc, sizeFromSizeClass(sc), n, blockSize-n*sizeFromSizeClass(sc))
	}

	fmt.Println("\n== 一个 16 字节类的块 ==")
	blk := newBlock(0)
	var slots []int
	for i := 0; i < 5; i++ {
		s, _, _, ok := blk.allocate()
		if ok {
			slots = append(slots, s)
		}
	}
	fmt.Printf("  连分 5 次 -> 槽位 %v next_slot=SLOT_BUMP free_count=%d\n",
		slots, blk.FreeCount)
	blk.free(slots[1])
	fmt.Printf("  释放槽 %d   -> next_slot=%d(1-based) free_count=%d\n",
		slots[1], blk.NextSlot, blk.FreeCount)
	s, fromList, corrupt, _ := blk.allocate()
	fmt.Printf("  再分配     -> 槽位 %d(来自空闲链表=%v 哨兵损坏=%v)\n",
		s, fromList, corrupt)

	for i := 0; i < blk.Slots-5; i++ {
		blk.allocate()
	}
	fmt.Printf("  填满后     -> next_slot=SLOT_FULL(%d) free_count=%d(回绕),已分 %d 槽\n",
		slotFull, blk.FreeCount, blk.allocatedCount())
	_, _, _, ok := blk.allocate()
	fmt.Println("  再分配     -> 成功?", ok)

	fmt.Println("\n== 地址拆解(iOS 变体) ==")
	lay := newLayout(true, 0x6)
	arena := newArena(0x2a)
	block := arena.firstBlockForSizeClass(0)
	addr := lay.encode(3*16, block, 1, 0)
	fmt.Printf("  class 0 首块 = %d(逻辑 %d 与 cookie 0x2a 异或后)\n",
		block, block^arena.AslrCookie)
	fmt.Printf("  槽 3 的地址 = 0x%x,签名有效=%v,由块号反查尺寸类=%d\n",
		addr, lay.hasValidSignature(addr), arena.sizeClassForBlock(block))

	fmt.Println("\n== CPU -> 当前块下标 ==")
	fmt.Println("  ", allocationBlockIndex(0), allocationBlockIndex(1),
		allocationBlockIndex(63), allocationBlockIndex(64), allocationBlockIndex(65))

	fmt.Println("\n== 放空一个已停用的块 ==")
	b := newBlock(7)
	b.allocate()
	b.InUse = false
	fmt.Printf("  free -> %v next_slot=SLOT_CAN_MADVISE(%d)\n",
		b.free(0), slotCanMadvise)
	fmt.Printf("  块号与元数据下标互换: 1 -> %d, 64 -> %d\n",
		blockIndexToMetaIndex(1), blockIndexToMetaIndex(64))
}
