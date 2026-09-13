// goroutine_channel.go — 单文件串讲 goroutine / channel / select 三大并发原语
// Demo 1: goroutine + channel + select (CSP 通信模型)
// 涵盖 9 个示例:goroutine 创建 / unbuffered 同步 / buffered 异步 / close 广播 /
// comma-ok 接收 / range 迭代 / select 多路复用 / nil-channel 屏蔽 / fan-out 超时取消
package main

import (
	"context"
	"fmt"
	"sync"
	"time"
)

func main() {
	// ---------- 1) goroutine 创建与基础同步 ----------
	fmt.Println("[1] goroutine + WaitGroup 等待")
	var wg sync.WaitGroup
	for i := 0; i < 3; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			fmt.Printf("  worker %d done\n", id)
		}(i)
	}
	wg.Wait()
	fmt.Println("  all workers finished")

	// ---------- 2) unbuffered channel 同步通信 ----------
	fmt.Println("\n[2] unbuffered channel: 发送即握手")
	sync_ch := make(chan string) // capacity=0, 同步
	go func() {
		sync_ch <- "ping" // 阻塞,直到主 goroutine 接收
	}()
	msg := <-sync_ch
	fmt.Printf("  got: %q (sender blocked until this line)\n", msg)

	// ---------- 3) buffered channel 异步队列 ----------
	fmt.Println("\n[3] buffered channel: 容量 3, 不阻塞直到满")
	buf_ch := make(chan int, 3)
	for i := 1; i <= 3; i++ {
		buf_ch <- i // 不阻塞
	}
	fmt.Printf("  len(buf_ch)=%d (cap=%d), 队列已满, 第四次发送会阻塞\n", len(buf_ch), cap(buf_ch))

	// ---------- 4) close 充当广播信号 ----------
	fmt.Println("\n[4] close(ch) 广播: 唤醒所有接收者")
	done := make(chan struct{})
	for i := 0; i < 3; i++ {
		go func(id int) {
			<-done // 所有 worker 同时被唤醒
			fmt.Printf("  worker %d received shutdown signal\n", id)
		}(i)
	}
	time.Sleep(50 * time.Millisecond) // 让 worker 全部进入 <-done 阻塞
	close(done)
	time.Sleep(50 * time.Millisecond)

	// ---------- 5) comma-ok 接收, 检测通道关闭 ----------
	fmt.Println("\n[5] comma-ok 接收: v, ok := <-ch")
	data := make(chan int, 3)
	data <- 10
	data <- 20
	close(data)
	for i := 0; i < 4; i++ {
		v, ok := <-data
		fmt.Printf("  recv: v=%d ok=%v\n", v, ok) // 第 3 次 ok=false, v=0
	}

	// ---------- 6) range channel, 直到 close ----------
	fmt.Println("\n[6] range channel 配合 close 自动退出")
	stream := make(chan int, 5)
	go func() {
		for i := 1; i <= 5; i++ {
			stream <- i
		}
		close(stream) // 关键:忘记 close 会死锁
	}()
	sum := 0
	for v := range stream {
		sum += v
	}
	fmt.Printf("  range sum: %d (1+2+3+4+5)\n", sum)

	// ---------- 7) select 多路复用 + 超时 + 取消 ----------
	fmt.Println("\n[7] select: 多路复用 + timeout + context 取消")
	slow := make(chan string, 1)
	go func() {
		time.Sleep(200 * time.Millisecond)
		slow <- "result"
	}()

	select {
	case v := <-slow:
		fmt.Printf("  收到结果: %s\n", v)
	case <-time.After(50 * time.Millisecond):
		fmt.Println("  超时 50ms (但任务仍在后台跑)")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	select {
	case v := <-slow:
		fmt.Printf("  收到结果: %s\n", v)
	case <-ctx.Done():
		fmt.Printf("  ctx 取消: %v\n", ctx.Err())
	}

	// ---------- 8) nil channel 在 select 中"屏蔽"该 case ----------
	fmt.Println("\n[8] nil channel = 永远不就绪, 用于动态屏蔽")
	a, b := make(chan int), make(chan int)
	_ = b // 故意不用 b
	go func() { a <- 100 }()
	b = nil // 屏蔽掉 case <-b
	select {
	case v := <-a:
		fmt.Printf("  只关心 a, got %d (case <-b 被 nil 屏蔽)\n", v)
	case <-b: // 永远不会触发
	}
	_ = a

	// ---------- 9) fan-out: N 个 worker 处理同一 channel ----------
	fmt.Println("\n[9] fan-out: 4 worker 共享 jobs channel")
	jobs := make(chan int, 8)
	for i := 1; i <= 8; i++ {
		jobs <- i
	}
	close(jobs) // 关键:让 worker 的 range 退出

	var results = make(chan int, 8)
	var wg2 sync.WaitGroup
	for w := 1; w <= 4; w++ {
		wg2.Add(1)
		go func(workerID int) {
			defer wg2.Done()
			for job := range jobs {
				// 模拟工作负载
				time.Sleep(10 * time.Millisecond)
				results <- job * job
			}
		}(w)
	}
	wg2.Add(1)
	go func() {
		wg2.Wait() // 等所有 worker 退出
		close(results)
		wg2.Done()
	}()
	wg2.Wait() // 这里等待的是 close(results) 那条 goroutine

	total := 0
	for r := range results {
		total += r
	}
	fmt.Printf("  fan-out sum of squares (1..8): %d\n", total)
}