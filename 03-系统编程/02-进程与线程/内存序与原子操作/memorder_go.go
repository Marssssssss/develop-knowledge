// memorder_go.go —— Go 侧对照：Go 不暴露 memory_order，只有顺序一致的原子操作
//
// 依据官方《The Go Memory Model》（go/doc/go_mem.html，Version of June 6, 2022）：
//   - *"A data race is defined as a write to a memory location happening concurrently with
//     another read or write to that location, unless all the accesses involved are atomic
//     data accesses as provided by the sync/atomic package."*
//   - *"In the absence of data races, Go programs behave as if all the goroutines were
//     multiplexed onto a single processor. This property is sometimes referred to as
//     DRF-SC: data-race-free programs execute in a sequentially consistent manner."*
//   - *"All the atomic operations executed in a program behave as though executed in some
//     sequentially consistent order."*
//
// 也就是说：C/C++ 里要靠 release/acquire 手工搭出来的同步，在 Go 里由 sync/atomic 直接兑现，
// 代价是拿不到 relaxed 那种「只要原子性不要顺序」的更弱档位（Go 后来才引入的
// atomic.Pointer 等类型也依然是 SC 语义）。
package main

import (
	"fmt"
	"runtime"
	"sync"
	"sync/atomic"
)

// 1. 非原子的 message passing：这是数据竞争，Go 允许直接报错终止
func racyMP() {
	var data int
	var flag int32
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		data = 42
		atomic.StoreInt32(&flag, 1) // 只把 flag 变成原子，data 不是
	}()
	go func() {
		defer wg.Done()
		for atomic.LoadInt32(&flag) == 0 {
			runtime.Gosched()
		}
		fmt.Println("  racy data =", data) // data 的读写没同步：竞争
	}()
	wg.Wait()
}

// 2. 全原子版本：DRF-SC 保证读者看到 42
func atomicMP() {
	var data atomic.Int64
	var flag atomic.Int32
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		data.Store(42)
		flag.Store(1)
	}()
	go func() {
		defer wg.Done()
		for flag.Load() == 0 {
			runtime.Gosched()
		}
		fmt.Println("  atomic data =", data.Load())
	}()
	wg.Wait()
}

// 3. 原子自增：与内存序无关，RMW 本身不会丢更新
func bump() {
	var counter atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 10000; j++ {
				counter.Add(1)
			}
		}()
	}
	wg.Wait()
	fmt.Println("  counter =", counter.Load())
}

func main() {
	fmt.Println("非原子版（数据竞争，结果未定义）：")
	racyMP()
	fmt.Println("全原子版（DRF-SC → 必然 42）：")
	atomicMP()
	fmt.Println("两个 goroutine 各加 1 万：")
	bump()
}
