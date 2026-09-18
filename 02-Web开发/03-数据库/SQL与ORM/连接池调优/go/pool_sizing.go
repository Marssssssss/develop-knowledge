// 连接池容量规划的 Go 版对照实现（与 python/pool_sizing.py 同题异构）。
//
// 口径来源：HikariCP wiki "About Pool Sizing"
// （https://github.com/brettwooldridge/HikariCP/wiki/About-Pool-Sizing，实读）。本文件复现：
//   - 官方经验公式 "connections = ((core_count * 2) + effective_spindle_count)"，
//     "Core count should not include HT threads"，算例 4 核 + 1 盘 = 9（取整 10）；
//   - "Once the number of threads exceeds the number of CPU cores, you're going slower by
//     adding more threads, not faster."（无阻塞时最优池 = 核数）；
//   - "Faster, no seeks, no rotational delays means less blocking and therefore fewer threads
//     [closer to core count] will perform better"（阻塞越多，最优池越大）；
//   - "You want a small pool, saturated with threads waiting for connections."；
//   - pool-locking 下界 "pool size = Tn x (Cm - 1) + 1"：Tn=3/Cm=4 → 10，Tn=8/Cm=3 → 17。
//
// 运行：go run pool_sizing.go（本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"fmt"
	"os"
)

const switchTax = 0.2 // 每个超出核数的并发任务每 tick 扣除的核时间片比例

// formulaPoolSize 是官方经验公式；HT 线程不计入 coreCount。
func formulaPoolSize(coreCount, spindleCount int) int { return coreCount*2 + spindleCount }

// poolLockingMin 是避免死锁的最小池大小：Tn x (Cm - 1) + 1。
func poolLockingMin(threads, maxConns int) int { return threads*(maxConns-1) + 1 }

// simulate 用显式简化模型给出稳态吞吐（完成请求数 / tick）。
// 每 tick：① 池未满则补入新请求；② 已过 CPU 阶段的请求推进 I/O（不占核）；
// ③ 可用核时间片按队列顺序发给需要 CPU 的请求；④ CPU 与 I/O 都归零的请求记为完成。
func simulate(cores, pool, cpuTicks, ioTicks, ticks int) float64 {
	type task struct{ cpu, io int }
	inFlight := []task{}
	done := 0
	for t := 0; t < ticks; t++ {
		for len(inFlight) < pool {
			inFlight = append(inFlight, task{cpu: cpuTicks, io: ioTicks})
		}
		waste := 0.0
		if len(inFlight) > cores {
			waste = float64(len(inFlight)-cores) * switchTax
			if waste > float64(cores) {
				waste = float64(cores)
			}
		}
		grants := int(float64(cores) - waste + 1e-9)
		if grants < 0 {
			grants = 0
		}
		for i := range inFlight { // ② I/O 推进
			if inFlight[i].cpu == 0 && inFlight[i].io > 0 {
				inFlight[i].io--
			}
		}
		for i := range inFlight { // ③ 核时间片
			if grants > 0 && inFlight[i].cpu > 0 {
				inFlight[i].cpu--
				grants--
			}
		}
		rest := make([]task, 0, len(inFlight))
		for _, x := range inFlight { // ④ 收尾
			if x.cpu == 0 && x.io == 0 {
				done++
				continue
			}
			rest = append(rest, x)
		}
		inFlight = rest
	}
	return float64(done) / float64(ticks)
}

// sweep 返回各池大小下的吞吐曲线。
func sweep(cores int, sizes []int, cpuTicks, ioTicks int) map[int]float64 {
	out := map[int]float64{}
	for _, p := range sizes {
		out[p] = simulate(cores, p, cpuTicks, ioTicks, 400)
	}
	return out
}

// bestPool 返回吞吐最高（并列取较小池）的池大小。
func bestPool(curve map[int]float64, sizes []int) int {
	best := sizes[0]
	for _, p := range sizes {
		if curve[p] > curve[best] {
			best = p
		}
	}
	return best
}

// poolLockingTrace 模拟「每个线程一次只申请一个连接、持满 Cm 个才释放」的最坏交错。
// 返回 (完成线程数, 是否死锁)。死锁 = 某一轮既无人拿到新连接、也无人持满。
func poolLockingTrace(threads, maxConns, pool int) (int, bool) {
	held := make([]int, threads)
	finished := make([]bool, threads)
	free := pool
	completed := 0
	for round := 0; round < maxConns*threads+8; round++ {
		gained, released := 0, 0
		for i := 0; i < threads; i++ {
			if finished[i] {
				continue
			}
			if free > 0 {
				held[i]++
				free--
				gained++
			}
			if held[i] == maxConns {
				free += held[i]
				held[i] = 0
				finished[i] = true
				completed++
				released++
			}
		}
		if gained == 0 && released == 0 {
			break
		}
	}
	return completed, completed < threads
}

var checks = struct{ pass, fail int }{}

func check(label string, cond bool, detail string) {
	if cond {
		checks.pass++
		fmt.Printf("  [PASS] %s\n", label)
		return
	}
	checks.fail++
	fmt.Printf("  [FAIL] %s :: %s\n", label, detail)
}

func main() {
	sizes := []int{1, 2, 4, 6, 8, 10, 16, 32, 100, 1000}

	fmt.Println("1) 官方经验公式 connections = ((core_count * 2) + effective_spindle_count)")
	check("4 核 + 1 盘 = 9（官方算例）", formulaPoolSize(4, 1) == 9, fmt.Sprint(formulaPoolSize(4, 1)))
	check("SSD / 缓存全命中（spindle=0）= 8，比机械盘更小", formulaPoolSize(4, 0) == 8, fmt.Sprint(formulaPoolSize(4, 0)))
	check("HT 线程不计入核数（8 核 16 线程按 8 算）", formulaPoolSize(8, 1) == 17, fmt.Sprint(formulaPoolSize(8, 1)))

	fmt.Println("\n2) 有 I/O 阻塞时：峰值出现在「略大于核数」的小池")
	ioCurve := sweep(4, sizes, 5, 20)
	ioBest := bestPool(ioCurve, sizes)
	fmt.Printf("     K=4, cpu=5, io=20 -> 吞吐 %v\n", ioCurve)
	check("峰值池大小落在 (4, 16] 区间", ioBest > 4 && ioBest <= 16, fmt.Sprint(ioBest))
	check("P=4（等于核数）不是最优：I/O 空档无人补位", ioCurve[4] < ioCurve[ioBest],
		fmt.Sprintf("%v vs %v", ioCurve[4], ioCurve[ioBest]))
	check("P=100 明显差于峰值池（'Even 100 connections, overkill'）", ioCurve[100] < ioCurve[ioBest],
		fmt.Sprintf("%v vs %v", ioCurve[100], ioCurve[ioBest]))
	check("P=1000 吞吐塌到峰值的 10% 以下（'1000 still horrible'）", ioCurve[1000] < 0.1*ioCurve[ioBest],
		fmt.Sprintf("%v vs %v", ioCurve[1000], ioCurve[ioBest]))

	fmt.Println("\n3) 无阻塞（纯 CPU）时：最优池 = 核数，且超过核数即变慢")
	cpuCurve := sweep(4, sizes, 5, 0)
	cpuBest := bestPool(cpuCurve, sizes)
	fmt.Printf("     K=4, cpu=5, io=0 -> 吞吐 %v\n", cpuCurve)
	check("最优池 = 核数(4)", cpuBest == 4, fmt.Sprint(cpuBest))
	check("P=8 / P=16 依次递减", cpuCurve[8] < cpuCurve[4] && cpuCurve[16] < cpuCurve[8],
		fmt.Sprintf("%v/%v/%v", cpuCurve[4], cpuCurve[8], cpuCurve[16]))

	fmt.Println("\n4) 阻塞越多，最优池越大（SSD 推论的形状复现）")
	heavyCurve := sweep(4, sizes, 5, 60)
	heavyBest := bestPool(heavyCurve, sizes)
	check("I/O 占比提高后最优池大于纯 CPU 场景", heavyBest > cpuBest, fmt.Sprintf("%d vs %d", heavyBest, cpuBest))

	fmt.Println("\n5) pool-locking 下界 pool size = Tn x (Cm - 1) + 1")
	check("Tn=3 / Cm=4 -> 10（官方算例）", poolLockingMin(3, 4) == 10, fmt.Sprint(poolLockingMin(3, 4)))
	check("Tn=8 / Cm=3 -> 17（官方算例）", poolLockingMin(8, 3) == 17, fmt.Sprint(poolLockingMin(8, 3)))
	done, dead := poolLockingTrace(3, 4, 9)
	check("池=9（下界-1）→ 三个线程各持 3 个连接后集体卡死", done == 0 && dead,
		fmt.Sprintf("done=%d dead=%v", done, dead))
	done, dead = poolLockingTrace(3, 4, 10)
	check("池=10（下界）→ 全部完成", done == 3 && !dead, fmt.Sprintf("done=%d dead=%v", done, dead))
	done, dead = poolLockingTrace(8, 3, 16)
	check("Tn=8 / Cm=3 时池=16 仍死锁（下界 17 不可省）", done == 0 && dead,
		fmt.Sprintf("done=%d dead=%v", done, dead))
	done, dead = poolLockingTrace(8, 3, 17)
	check("Tn=8 / Cm=3 时池=17 恰好全部完成", done == 8 && !dead, fmt.Sprintf("done=%d dead=%v", done, dead))

	fmt.Println("\n6) 目标形态：小池 + 大量线程阻塞在池上")
	peakConcurrency := func(pool, clients int) int {
		if pool < clients {
			return pool
		}
		return clients
	}
	check("池=10 / 3000 用户 → 同时在飞只有 10", peakConcurrency(10, 3000) == 10, fmt.Sprint(peakConcurrency(10, 3000)))
	check("下界（防死锁）与最优（吞吐）是两个不同的量", poolLockingMin(3, 4) != ioBest,
		fmt.Sprintf("%d vs %d", poolLockingMin(3, 4), ioBest))

	fmt.Printf("\n断言结果：pass=%d fail=%d\n", checks.pass, checks.fail)
	if checks.fail > 0 {
		os.Exit(1)
	}
}
