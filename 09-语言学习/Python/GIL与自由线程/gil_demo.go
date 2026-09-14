// GIL 跨语言对照:Go 的 goroutine 由 runtime 调度到多个 OS 线程上,CPU 密集任务可以真并行。
//
// 这是 09-语言学习/Python/GIL与自由线程 的对照实现(不是独立 demo),
// 用来和 Python 版的"线程加速比 ≈ 1x"做直接对比。
//
// 运行:
//
//	go run gil_demo.go                  # 默认 GOMAXPROCS=NumCPU,观察真并行
//	GOMAXPROCS=1 go run gil_demo.go     # 人为串行化,加速比退化到 ~1x
//
// 注:本机无 Go 工具链,此文件经人工代码审查,未实际编译运行。
package main

import (
	"fmt"
	"runtime"
	"sync"
	"time"
)

const perTask = 40_000_000 // 单份纯计算工作的迭代数

// busy 是一段纯 CPU 计算,没有 I/O,不主动让出。
func busy(n int) int {
	sum := 0
	for i := 0; i < n; i++ {
		sum += (i * i) % 7
	}
	return sum
}

func main() {
	cores := runtime.NumCPU()
	fmt.Printf("NumCPU=%d GOMAXPROCS=%d Go=%s\n", cores, runtime.GOMAXPROCS(0), runtime.Version())
	fmt.Println("对照点:Python 的多个线程共享一把 GIL,同一时刻只有一个线程在跑字节码;")
	fmt.Println("        Go 的多个 goroutine 由调度器放到不同 OS 线程,可同时跑在多核上。")

	// 串行:同一个 goroutine 连做 cores 份工作
	total := 0
	start := time.Now()
	for i := 0; i < cores; i++ {
		total += busy(perTask)
	}
	serial := time.Since(start)

	// 并行:cores 个 goroutine 各做一份,由 runtime 自动分配到多核
	var wg sync.WaitGroup
	start = time.Now()
	for i := 0; i < cores; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			busy(perTask)
		}()
	}
	wg.Wait()
	parallel := time.Since(start)

	fmt.Printf("serial   (%d tasks, 1 goroutine)  = %v\n", cores, serial)
	fmt.Printf("parallel (%d goroutines)          = %v\n", cores, parallel)
	fmt.Printf("speedup = %.2fx (理论上限 %d x)\n", float64(serial)/float64(parallel), cores)
	fmt.Println("GOMAXPROCS=1 时 speedup 会退化到 ~1x —— 那才是【没有多核调度】的样子。")
	_ = total // 保留结果,避免被优化掉
}
