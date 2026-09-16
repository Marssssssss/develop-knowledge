// 最小有栈协程:Go 视角 —— goroutine 是"有栈 + 可增长"的现代形态。
//
// ucontext 的栈是固定的(本系列 C 版用 64KiB),深递归直接段错误;
// goroutine 的栈从很小的初始值开始、由运行时按需增长,任意调用
// 深度都可以挂起/恢复。本程序观测三件事:
//   1. 单个 goroutine 里 10 万层深递归不溢出(栈按需增长)
//   2. 同款递归在"固定 64KiB 栈"模型下必然溢出(用计数器模拟)
//   3. 从 3 层嵌套调用深处让出、由主流程唤醒 —— 有栈协程的
//      标志能力(挂起点不需要在任何词法 yield 位置)
package main

import (
	"fmt"
	"os"
)

const depth = 100000 // 递归深度:64KiB 固定栈撑不住的量级

// deepRec 在 goroutine 里跑:运行时按需增长栈,10 万层安然无恙。
func deepRec(n int, acc int) int {
	var pad [16]byte // 每层驻留 16B,放大栈消耗
	pad[0] = byte(n)
	if n == 0 {
		return acc
	}
	return deepRec(n-1, acc+n) + int(pad[0])*0
}

// fixedStackRec 模拟 ucontext 固定栈:每层约 64B 栈帧,
// 超过栈容量即"溢出"(真实 ucontext 下是段错误,此处报错返回)。
func fixedStackRec(n int, capacity int, used *int) error {
	*used += 64
	if *used > capacity {
		return fmt.Errorf("stack overflow at depth %d: need %d > %d bytes",
			depth-n, *used, capacity)
	}
	if n == 0 {
		return nil
	}
	return fixedStackRec(n-1, capacity, used)
}

// nestedYield 在 depthLeft 层嵌套的最深处"让出"(发给 parked),
// 等主流程 close(release) 唤醒后继续 —— 挂起点不在任何词法位置。
func nestedYield(depthLeft int, parked chan int, release chan struct{},
	done chan string) {
	if depthLeft == 0 {
		parked <- depthLeft     // 最深处让出(depthLeft == 0)
		<-release               // 被主流程唤醒
		done <- "resumed-from-depth-3"
		return
	}
	nestedYield(depthLeft-1, parked, release, done)
}

func main() {
	// 场景 1:goroutine 栈可增长,深递归无恙。
	// (go 语句 synchronized-before goroutine 启动 —— go.dev/ref/mem)
	resCh := make(chan int, 1)
	go func() {
		resCh <- deepRec(depth, 0)
	}()
	total := <-resCh
	if want := depth * (depth + 1) / 2; total != want {
		fmt.Println("FAIL: unexpected recursion result", total)
		os.Exit(1)
	}
	fmt.Printf("PASS: goroutine handled depth-%d recursion (sum=%d): "
		+"stack grows on demand\n", depth, total)

	// 场景 2:固定 64KiB"ucontext 栈"跑同款递归必然爆。
	used := 0
	err := fixedStackRec(depth, 64*1024, &used)
	if err == nil {
		fmt.Println("FAIL: fixed stack unexpectedly survived")
		os.Exit(1)
	}
	fmt.Printf("PASS: fixed 64KiB stack overflows: %v\n", err)

	// 场景 3:从任意嵌套深度让出并恢复(有栈的自由度)。
	parked := make(chan int)
	release := make(chan struct{})
	done := make(chan string, 1)
	go nestedYield(3, parked, release, done)
	at := <-parked
	if at != 0 {
		fmt.Println("FAIL: expected park at recursion floor, got", at)
		os.Exit(1)
	}
	close(release)
	msg := <-done
	if msg != "resumed-from-depth-3" {
		fmt.Println("FAIL: unexpected message", msg)
		os.Exit(1)
	}
	fmt.Println("PASS: suspended from nested call depth and resumed "
		+"(impossible for lexically-limited generators)")
}
