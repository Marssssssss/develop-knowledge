// gpm_scheduler.go — 单文件串讲 Go 调度器 G/M/P 三层模型与 runtime 观测 API
// Demo 3: GPM 调度模型(Goroutine / Machine / Processor)
// 涵盖 6 个示例:GOMAXPROCS 语义 / NumGoroutine / Gosched 让出 / channel 阻塞
// (G 重排队,M 不换)/ 阻塞系统调用(M 与 P 解绑)/ Goexit 与 defer
package main

import (
	"fmt"
	"runtime"
	"sync"
	"time"
)

// ---------- 1) GOMAXPROCS:控制的是 P 数(可同时执行用户代码的 CPU 数) ----------
// runtime 文档:GOMAXPROCS sets the maximum number of CPUs that can be
// executing simultaneously;n < 1 时"仅查询、不修改"。
// 默认 = min(逻辑 CPU 数, CPU 亲和掩码, cgroup CPU 配额),但不会被设到 < 2
// (除非机器本身 < 2);阻塞在系统调用里的线程不计入该限制。
func demoGOMAXPROCS() {
	fmt.Println("[1] GOMAXPROCS 与 P")
	fmt.Printf("  NumCPU()      = %d(逻辑核心数)\n", runtime.NumCPU())
	fmt.Printf("  GOMAXPROCS(0) = %d(当前 P 数,传 0 只查询)\n", runtime.GOMAXPROCS(0))
	old := runtime.GOMAXPROCS(2) // 调成 2 个 P,返回旧值
	fmt.Printf("  GOMAXPROCS(2) 返回旧值 %d\n", old)
	runtime.GOMAXPROCS(old) // 改回去
}

// ---------- 2) NumGoroutine:当前存在的 goroutine 总数 ----------
func demoNumGoroutine() {
	fmt.Println("\n[2] NumGoroutine")
	fmt.Printf("  main 中: %d\n", runtime.NumGoroutine())
	var wg sync.WaitGroup
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); time.Sleep(50 * time.Millisecond) }()
	}
	fmt.Printf("  起 10 个存活 goroutine 后: %d(应约为 11)\n", runtime.NumGoroutine())
	wg.Wait()
	fmt.Printf("  全部退出后: %d\n", runtime.NumGoroutine())
}

// ---------- 3) Gosched:让出处理器但不挂起自己 ----------
// runtime 文档:Gosched yields the processor, allowing other goroutines to
// run. It does not suspend the current goroutine, so execution resumes
// automatically. 协作式调度时代的重要让出手段;本例演示它对输出顺序的影响。
func demoGosched() {
	fmt.Println("\n[3] Gosched 让出")
	done := make(chan bool)
	go func() {
		for i := 0; i < 3; i++ {
			fmt.Printf("  worker: 第 %d 段\n", i+1)
			runtime.Gosched() // 主动让出:main 与 worker 交替打印
		}
		done <- true
	}()
	for i := 0; i < 3; i++ {
		fmt.Printf("  main  : 第 %d 段\n", i+1)
		runtime.Gosched()
	}
	<-done
}

// ---------- 4) channel 阻塞:G 被重新排队,M 不进入等待态 ----------
// Ardan Labs:channel/atomic/mutex 阻塞时,调度器直接把另一个 G 切上来;
// 同一 M+Core 可以持续干活,OS 视角线程从不 waiting——
// "Go has turned IO/Blocking work into CPU-bound work at the OS level."
func demoChannelBlock() {
	fmt.Println("\n[4] channel 阻塞:G 让位、M 不闲着")
	ch := make(chan int)
	var wg sync.WaitGroup
	wg.Add(2)
	go func() { defer wg.Done(); ch <- 42 }()          // G1:发送
	go func() { defer wg.Done(); fmt.Println("  收到", <-ch) }() // G2:接收
	wg.Wait()
	fmt.Println("  两个 G 在同一 M 上切换完成,无 OS 线程阻塞")
}

// ---------- 5) 阻塞系统调用:M 带着 G 离开 P,P 换一个 M 继续服务 ----------
// Ardan Labs 流程:M1 带着阻塞的 G1 与 P 解绑 -> 调度器调来 M2 服务该 P ->
// 系统调用返回后 G1 回 LRQ,M1 挂起备用("M1 is then placed on the side for
// future use")。异步网络调用则不同:G 移交 network poller,M 立即自由。
func demoBlockingSyscall() {
	fmt.Println("\n[5] 阻塞系统调用(文件 I/O 模拟 M-P 解绑路径)")
	var wg sync.WaitGroup
	start := time.Now()
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			// time.Sleep 在 runtime 里走 timer,不真占线程;
			// 真正走 M 解绑路径的是文件 I/O / cgo 等同步系统调用
			time.Sleep(30 * time.Millisecond)
			fmt.Printf("  G%d 从模拟阻塞中返回\n", id)
		}(i)
	}
	wg.Wait()
	fmt.Printf("  4 个 G 并发阻塞 30ms,总耗时约 %v(而非串行 120ms)\n",
		time.Since(start).Round(time.Millisecond))
}

// ---------- 6) Goexit:只终止自己,且先跑完 defer ----------
// runtime 文档:Goexit terminates the goroutine that calls it. No other
// goroutine is affected. Goexit runs all deferred calls before terminating;
// 因为不是 panic,defer 里的 recover() 返回 nil。
func demoGoexit() {
	fmt.Println("\n[6] Goexit 与 defer")
	done := make(chan string)
	go func() {
		defer func() {
			// Goexit 不是 panic:这里 recover() 拿到 nil
			fmt.Println("  defer 中 recover() =", recover())
			done <- "goroutine 已退出"
		}()
		fmt.Println("  调用 Goexit 前")
		runtime.Goexit()
		fmt.Println("  这行永远不会执行")
	}()
	fmt.Println(" ", <-done)
	fmt.Printf("  其余 goroutine 不受影响,当前仍有 %d 个\n", runtime.NumGoroutine())
}

func main() {
	demoGOMAXPROCS()
	demoNumGoroutine()
	demoGosched()
	demoChannelBlock()
	demoBlockingSyscall()
	demoGoexit()
}
