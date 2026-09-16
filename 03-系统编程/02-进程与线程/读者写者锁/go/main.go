// 读者-写者锁:Go 实现(sync.RWMutex)。
//
// 依据 go.dev/pkg/sync 文档:有 goroutine 调用 Lock 且读者持锁时,
// 后续 RLock 阻塞直到写者获得并释放 → 写者偏好、禁止递归读锁。
// 本程序验证:读者重叠、写者独占、TryRLock/TryLock 的非阻塞探测。
package main

import (
	"fmt"
	"os"
	"sync"
	"sync/atomic"
	"time"
)

const (
	nReaders = 4
	nWriters = 3
	iter     = 200
)

var (
	curReaders int64 // 持读锁期间用原子计数观测并发度
	maxReaders int64
	violations int64
)

func main() {
	var rw sync.RWMutex
	var wg sync.WaitGroup

	for i := 0; i < nReaders; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < iter; j++ {
				rw.RLock()
				cur := atomic.AddInt64(&curReaders, 1)
				for {
					m := atomic.LoadInt64(&maxReaders)
					if cur <= m || atomic.CompareAndSwapInt64(&maxReaders, m, cur) {
						break
					}
				}
				time.Sleep(50 * time.Microsecond) // 驻留:制造读者重叠窗口
				atomic.AddInt64(&curReaders, -1)
				rw.RUnlock()
			}
		}()
	}

	for i := 0; i < nWriters; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < iter; j++ {
				rw.Lock()
				// 写者独占:此刻不能有任何读者
				if atomic.LoadInt64(&curReaders) != 0 {
					atomic.AddInt64(&violations, 1)
				}
				rw.Unlock()
			}
		}()
	}
	wg.Wait()

	fail := false
	if atomic.LoadInt64(&violations) != 0 {
		fmt.Println("FAIL: readers found inside writer section")
		fail = true
	}
	if atomic.LoadInt64(&maxReaders) < 2 {
		fmt.Println("FAIL: readers never overlapped")
		fail = true
	}
	if fail {
		os.Exit(1)
	}
	fmt.Printf("PASS: RWMutex invariants (max concurrent readers = %d, writer exclusive)\n",
		atomic.LoadInt64(&maxReaders))

	// TryRLock:写者持锁时必然失败且不阻塞
	rw.Lock()
	ok := rw.TryRLock()
	rw.Unlock()
	fmt.Printf("TryRLock while write-held = %v (want false)\n", ok)
	if ok {
		os.Exit(1)
	}

	// TryLock:读者持锁时必然失败且不阻塞
	rw.RLock()
	ok = rw.TryLock()
	rw.RUnlock()
	fmt.Printf("TryLock while read-held  = %v (want false)\n", ok)
	if ok {
		os.Exit(1)
	}
	fmt.Println("PASS: TryRLock/TryLock never block (EBUSY semantics)")
}
