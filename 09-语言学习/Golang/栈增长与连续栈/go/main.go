package main

import "fmt"

var checks, failed int

func check(cond bool, label string) {
	checks++
	if !cond {
		failed++
		fmt.Println("FAIL:", label)
	}
}

func checkEq(got, want interface{}, label string) {
	checks++
	if fmt.Sprint(got) != fmt.Sprint(want) {
		failed++
		fmt.Printf("FAIL: %s (got=%v want=%v)\n", label, got, want)
	}
}

func main() {
	// 1) 常量
	checkEq(stackMin, int64(2048), "stackMin=2048")
	checkEq(stackNosplitBase, int64(800), "abi.StackNosplitBase=800")
	checkEq(stackSmall, int64(128), "abi.StackSmall=128")
	checkEq(stackGuardMult, 1, "默认 StackGuardMultiplier=1")
	checkEq(stackGuard("linux", "amd64"), int64(928), "linux stackGuard=928")
	checkEq(stackGuard("windows", "amd64"), int64(5024), "windows stackGuard=5024")
	checkEq(fixedStack("linux", "amd64"), int64(2048), "linux fixedStack=2048")
	checkEq(fixedStack("windows", "amd64"), int64(8192), "windows fixedStack=8192")

	// 2) 翻倍增长
	g := NewG(2048, 100, "linux")
	n, err := newStack(g, 0, false, false, 8)
	checkEq(err, nil, "首次增长不报错")
	checkEq(n, int64(4096), "2048 → 4096")
	checkEq(g.size(), int64(4096), "栈真的变大")
	checkEq(g.used(), int64(100), "已用部分不变")
	n, _ = newStack(g, 0, false, false, 8)
	checkEq(n, int64(8192), "再涨一次 → 8192")

	// 3) 帧很大时一次翻两倍
	g2 := NewG(2048, 2000, "linux")
	n, _ = newStack(g2, 3000, true, false, 8)
	checkEq(n, int64(8192), "needed=3928，4096 不够 → 8192")
	g2b := NewG(2048, 100, "linux")
	n, _ = newStack(g2b, 64, true, false, 8)
	checkEq(n, int64(4096), "小帧一次翻倍就够")

	// 4) 上限
	g3 := NewG(1<<28, 10, "linux")
	n, _ = newStack(g3, 0, false, false, 8)
	checkEq(n, int64(1)<<29, "256MB → 512MB 还没超")
	_, err = newStack(g3, 0, false, false, 8)
	check(err != nil, "512MB 再翻倍 1GB > 1e9 → 溢出")
	g4 := NewG(1<<28, 10, "linux")
	_, err = newStack(g4, 0, false, false, 4)
	check(err != nil, "32 位下 512MB > 2.5e8 → 溢出")

	// 5) 收缩的四分之一判据（是 >= 不是 >）
	checkEq(shrinkStack(NewG(8192, 1000, "linux")), int64(4096), "used=1800 < 2048 → 收缩")
	checkEq(shrinkStack(NewG(8192, 1247, "linux")), int64(4096), "used=2047 < 2048 → 收缩")
	checkEq(shrinkStack(NewG(8192, 1248, "linux")), int64(0), "used=2048 == 2048 → 不收缩")
	checkEq(shrinkStack(NewG(8192, 2000, "linux")), int64(0), "used=2800 → 不收缩")

	// 6) 收缩下限 fixedStack
	checkEq(shrinkStack(NewG(4096, 10, "linux")), int64(2048), "linux 4096 → 2048")
	checkEq(shrinkStack(NewG(2048, 10, "linux")), int64(0), "2048 不能再缩")
	checkEq(shrinkStack(NewG(8192, 10, "windows")), int64(0), "windows fixedStack=8192")
	checkEq(shrinkStack(NewG(16384, 10, "windows")), int64(8192), "windows 16384 → 8192")

	// 7) 哨兵值
	checkEq(guardSentinel(-1314, 8), uint64(0xfffffffffffffade), "stackPreempt")
	checkEq(guardSentinel(-1234, 8), uint64(0xfffffffffffffb2e), "stackFork")
	checkEq(guardSentinel(-275, 8), uint64(0xfffffffffffffeed), "stackForceMove")

	// 8) ForceMove 不翻倍
	g5 := NewG(2048, 100, "linux")
	n, _ = newStack(g5, 0, false, true, 8)
	checkEq(n, int64(2048), "stackForceMove 时 newsize = oldsize")
	checkEq(g5.Moves, 1, "但仍然搬动了一次")

	// 9) isShrinkStackSafe
	check(isShrinkStackSafe(0, false, false, false), "默认安全")
	check(!isShrinkStackSafe(0x2000, false, false, false), "系统调用中不收缩")
	check(!isShrinkStackSafe(0, true, false, false), "异步安全点不收缩")
	check(!isShrinkStackSafe(0, false, true, false), "park 到 activeStackChans 之间不收缩")
	check(!isShrinkStackSafe(0, false, false, true), "为 suspendG 等待时不收缩")

	fmt.Printf("checks=%d failed=%d\n", checks, failed)
	if failed > 0 {
		panic("selfcheck failed")
	}
}
