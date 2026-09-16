// 生产者-消费者:Go 实现(带缓冲 channel + close 广播)。
//
// 依据 The Go Memory Model(go.dev/ref/mem):
//   - 第 k 次发送 happens-before 对应的第 k 次接收完成
//   - 容量 C 的 channel 第 k 次接收 happens-before 第 k+C 次发送完成
//   - close(ch) happens-before 收到零值的接收
// 数据经 channel 传递,接收方无需任何锁即可看到发送方此前的全部写入。
package main

import (
	"fmt"
	"os"
	"sync"
)

const (
	bufCap      = 4
	nProd       = 2
	nCons       = 2
	itemsPerProd = 20
	totalItems  = nProd * itemsPerProd
)

func main() {
	ch := make(chan int, bufCap) // 容量即有界缓冲;也是背压边界

	var prodWg sync.WaitGroup
	for p := 0; p < nProd; p++ {
		prodWg.Add(1)
		go func(p int) {
			defer prodWg.Done()
			for j := 0; j < itemsPerProd; j++ {
				ch <- p*itemsPerProd + j // 满(容量 C)时在此阻塞
			}
		}(p)
	}

	// 唯一发送方管理者:所有发送完成后 close —— close 是广播,
	// 每个消费者的 for-range 都会在收到关闭后退出。
	go func() {
		prodWg.Wait()
		close(ch)
	}()

	seen := make([]int, totalItems) // 受 mu 保护(消费者互相之间的计数)
	var mu sync.Mutex
	var consWg sync.WaitGroup
	for c := 0; c < nCons; c++ {
		consWg.Add(1)
		go func() {
			defer consWg.Done()
			for v := range ch { // 收到关闭(零值广播)后循环自然结束
				mu.Lock()
				seen[v]++
				mu.Unlock()
			}
		}()
	}
	consWg.Wait()

	fail := false
	if cap(ch) != bufCap {
		fmt.Println("FAIL: channel capacity")
		fail = true
	}
	exactlyOnce := true
	for i, n := range seen {
		if n != 1 {
			fmt.Printf("  item %d seen %d times\n", i, n)
			exactlyOnce = false
		}
	}
	if !exactlyOnce {
		fmt.Println("FAIL: every item must be consumed exactly once")
		fail = true
	}
	if fail {
		os.Exit(1)
	}
	fmt.Println("PASS: buffered channel producer-consumer, "
		"close-as-broadcast, every item consumed exactly once")
}
