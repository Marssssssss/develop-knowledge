// 计算着色器执行模型:Go 复刻线程标识、硬件限制与树形归约。
// 依据 Microsoft Learn "Compute Shader Overview"/"numthreads"/"Dispatch":
// SV_GroupID/SV_GroupThreadID/SV_DispatchThreadID/SV_GroupIndex 四值自洽;
// cs_5_0 每组 ≤1024 线程、Z≤64、Dispatch 每维 ≤65535。
package main

import (
	"fmt"
	"math/rand"
)

type profile struct {
	maxThreadsPerGroup int
	maxZ               int
	maxDispatchDim     int
	atomics            bool
}

var cs5 = profile{1024, 64, 65535, true}
var cs4x = profile{768, 1, 65535, false}

// threadID:一个线程的四个系统值标识。
type threadID struct {
	groupID          [3]int // SV_GroupID
	groupThreadID    [3]int // SV_GroupThreadID
	dispatchThreadID [3]int // SV_DispatchThreadID
	groupIndex       int    // SV_GroupIndex
}

func newThreadID(numthreads, groupID, groupThreadID [3]int) threadID {
	dt := [3]int{}
	for i := 0; i < 3; i++ {
		dt[i] = groupID[i]*numthreads[i] + groupThreadID[i]
	}
	x, y := numthreads[0], numthreads[1]
	gi := groupThreadID[2]*x*y + groupThreadID[1]*x + groupThreadID[0]
	return threadID{groupID, groupThreadID, dt, gi}
}

func validateNumthreads(nt [3]int, p profile) bool {
	return nt[0]*nt[1]*nt[2] <= p.maxThreadsPerGroup && nt[2] <= p.maxZ
}

func validateDispatch(d [3]int, p profile) bool {
	for _, v := range d {
		if v > p.maxDispatchDim {
			return false
		}
	}
	return true
}

// groupReduce:groupshared + 屏障的树形归约(分阶段模拟,阶段内顺序无关)。
func groupReduce(numthreads [3]int, values []int) int {
	n := numthreads[0] * numthreads[1] * numthreads[2]
	if len(values) != n {
		panic("values length mismatch")
	}
	shared := make([]int, n)
	copy(shared, values) // 阶段 0:每线程写自己的值
	// GroupMemoryBarrierWithGroupSync(阶段边界)
	for stride := n / 2; stride > 0; stride /= 2 {
		for i := 0; i < stride; i++ {
			shared[i] += shared[i+stride]
		}
		// 屏障:下一级归约必须看到本级的全部写入
	}
	return shared[0]
}

func check(label string, cond bool, detail string) {
	if !cond {
		panic("FAIL " + label + ": " + detail)
	}
	fmt.Println("ok -", label)
}

func main() {
	// 1) MS Learn 文档标准例子:Dispatch(5,3,2)+numthreads(10,8,3)
	nt := [3]int{10, 8, 3}
	tid := newThreadID(nt, [3]int{2, 1, 0}, [3]int{7, 5, 0})
	check("DispatchThreadID", tid.dispatchThreadID == [3]int{27, 13, 0},
		fmt.Sprintf("%v", tid.dispatchThreadID))
	check("GroupIndex 57", tid.groupIndex == 57, fmt.Sprintf("%d", tid.groupIndex))

	// 全量枚举:DispatchThreadID 唯一,GroupIndex 与组内坐标一一对应
	total := 0
	seen := map[[3]int]bool{}
	for gz := 0; gz < 2; gz++ {
		for gy := 0; gy < 3; gy++ {
			for gx := 0; gx < 5; gx++ {
				for tz := 0; tz < nt[2]; tz++ {
					for ty := 0; ty < nt[1]; ty++ {
						for tx := 0; tx < nt[0]; tx++ {
							t := newThreadID(nt, [3]int{gx, gy, gz}, [3]int{tx, ty, tz})
							seen[t.dispatchThreadID] = true
							want := tz*nt[0]*nt[1] + ty*nt[0] + tx
							if t.groupIndex != want {
								panic(fmt.Sprintf("group index mismatch: %d vs %d", t.groupIndex, want))
							}
							total++
						}
					}
				}
			}
		}
	}
	check("total threads", total == 5*3*2*10*8*3, fmt.Sprintf("%d", total))
	check("dispatch IDs unique", len(seen) == total, fmt.Sprintf("%d", len(seen)))

	// 2) 硬件限制(cs_5_0 / cs_4_x)
	check("cs5 1024 ok", validateNumthreads([3]int{32, 32, 1}, cs5), "")
	check("cs5 1089 fail", !validateNumthreads([3]int{33, 33, 1}, cs5), "")
	check("cs5 z65 fail", !validateNumthreads([3]int{64, 1, 65}, cs5), "")
	check("cs4x 768 ok", validateNumthreads([3]int{768, 1, 1}, cs4x), "")
	check("cs4x z2 fail", !validateNumthreads([3]int{256, 1, 2}, cs4x), "")
	check("dispatch 65535 ok", validateDispatch([3]int{65535, 1, 1}, cs5), "")
	check("dispatch 65536 fail", !validateDispatch([3]int{65536, 1, 1}, cs5), "")

	// 3) 树形归约正确性(随机数据)
	rng := rand.New(rand.NewSource(2026))
	for _, ntI := range [][3]int{{16, 16, 1}, {256, 1, 1}, {8, 4, 2}} {
		n := ntI[0] * ntI[1] * ntI[2]
		vals := make([]int, n)
		want := 0
		for i := range vals {
			vals[i] = rng.Intn(201) - 100
			want += vals[i]
		}
		got := groupReduce(ntI, vals)
		check(fmt.Sprintf("reduce %v", ntI), got == want,
			fmt.Sprintf("got=%d want=%d", got, want))
	}

	// 4) 原子性:非原子丢更新 vs InterlockedAdd
	vals := []int{10, 20, 30, 40}
	// 原子:逐个读改写,不丢
	atomicSum := 0
	for _, v := range vals {
		atomicSum += v
	}
	// 非原子:全部线程先读旧值 0,再各自写回 → 只剩最后写的
	nonAtomic := 0
	for _, v := range vals {
		nonAtomic = 0 + v // 读到的 counter 恒为 0
	}
	check("interlocked add", atomicSum == 100, fmt.Sprintf("%d", atomicSum))
	check("lost update", nonAtomic == 40, fmt.Sprintf("%d", nonAtomic))
	check("cs4x no atomics", !cs4x.atomics && cs5.atomics, "")

	fmt.Println("ALL TESTS PASSED")
}
