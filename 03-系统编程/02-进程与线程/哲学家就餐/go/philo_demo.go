// 哲学家就餐问题：3 种方案串行对比（Go 版）
//
//   naive     —— "先左后右"，Mutex.TryLock + 退避，重试过多视为死锁
//   hier      —— Resource Hierarchy，按筷子编号小者优先
//   tanenbaum —— Dijkstra+Tanenbaum 监视器，sync.Cond + state[]
//
// 运行：cd go && go run philo_demo.go
//
// 注意：sync.Cond 不可复制，本 demo 用 *sync.Cond 指针。

package main

import (
	"fmt"
	"math/rand"
	"sync"
	"time"
)

const N = 5

const (
	THINKING = 0
	HUNGRY   = 1
	EATING   = 2
)

const maxMeals = 6 // 每个哲学家最多进餐次数

// ============================================================
// Tanenbaum 监视器（sync.Cond 包装）
// ============================================================
type monitor struct {
	mu    sync.Mutex
	cv    *sync.Cond // 必须指针，sync.Cond 不可复制
	state [N]int
}

func newMonitor() *monitor {
	m := &monitor{}
	m.cv = sync.NewCond(&m.mu)
	return m
}

func (m *monitor) test(i int) {
	l, r := (i-1+N)%N, (i+1)%N
	if m.state[l] != EATING && m.state[r] != EATING && m.state[i] == HUNGRY {
		m.state[i] = EATING
		m.cv.Signal() // 等价于 pthread_cond_signal
	}
}

func (m *monitor) pickup(i int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.state[i] = HUNGRY
	m.test(i)
	for m.state[i] != EATING {
		m.cv.Wait() // 原子释放 mu + 阻塞；唤醒后重新锁 mu
	}
}

func (m *monitor) putdown(i int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.state[i] = THINKING
	m.test((i - 1 + N) % N)
	m.test((i + 1) % N)
}

// ============================================================
// 筷子：sync.Mutex；Go 1.18+ 提供 TryLock 等价于 pthread_mutex_trylock
// ============================================================
type Chopstick struct {
	id  int
	mtx sync.Mutex
}

// ============================================================
// 三策略的 pickup / putdown
// ============================================================
type actions struct {
	pickup  func(i int) error
	putdown func(i int)
}

func newNaiveActions(forks [N]*Chopstick) actions {
	return actions{
		pickup: func(i int) error {
			giveups := 0
			l, r := (i-1+N)%N, (i+1)%N
			for {
				forks[l].mtx.Lock()
				if forks[r].mtx.TryLock() { // Go 1.18+ 才有 TryLock
					return nil
				}
				forks[l].mtx.Unlock() // 失败：放左、短暂退避、重试
				giveups++
				if giveups > 50 { // ~1 s
					return fmt.Errorf("deadlock: cycle detected")
				}
				time.Sleep(time.Duration(10+rand.Intn(30)) * time.Millisecond)
			}
		},
		putdown: func(i int) {
			forks[(i-1+N)%N].mtx.Unlock()
			forks[(i+1+N)%N].mtx.Unlock()
		},
	}
}

func newHierActions(forks [N]*Chopstick) actions {
	return actions{
		pickup: func(i int) error {
			l, r := (i-1+N)%N, (i+1)%N
			first, second := l, r
			if first > second {
				first, second = second, first
			}
			forks[first].mtx.Lock()
			forks[second].mtx.Lock()
			return nil
		},
		putdown: func(i int) {
			forks[(i-1+N)%N].mtx.Unlock()
			forks[(i+1+N)%N].mtx.Unlock()
		},
	}
}

func newTanenbaumActions() actions {
	m := newMonitor()
	return actions{
		pickup:  m.pickup,
		putdown: m.putdown,
	}
}

// ============================================================
// 演示驱动
// ============================================================
func runStrategy(name string, act actions) {
	var meals [N]int
	var deadlock int32 // atomic-ish via chan
	var wg sync.WaitGroup

	// 屏障：等所有 goroutine 起步再放行
	started := make(chan struct{})

	for i := 0; i < N; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			<-started // 等主线程放行
			for meals[idx] < maxMeals {
				if atomicLoad(&deadlock) == 1 {
					return
				}
				time.Sleep(time.Duration(50+rand.Intn(50)) * time.Millisecond) // think
				if err := act.pickup(idx); err != nil {
					atomicStore(&deadlock, 1)
					return
				}
				time.Sleep(time.Duration(50+rand.Intn(50)) * time.Millisecond) // eat
				meals[idx]++
				act.putdown(idx)
			}
		}(i)
	}

	time.Sleep(50 * time.Millisecond) // 让所有 goroutine 都到 started
	close(started)                    // 同时放行
	wg.Wait()

	fmt.Printf("\n=== [%s] 结果 ===\n", name)
	if atomicLoad(&deadlock) == 1 {
		fmt.Println(">> 死锁出现（Naive 高竞争下预期之中，重试过多即判循环等待）")
	} else {
		for i := 0; i < N; i++ {
			fmt.Printf("    P%d 吃了 %d 次\n", i, meals[i])
		}
	}
}

// ============================================================
// "atomic-ish" helper：32 位对齐读写在大多数平台是原子；只用 int32 字段做互斥
// 避免额外引 sync/atomic 本文件
// ============================================================
func atomicLoad(p *int32) int32     { return *p }
func atomicStore(p *int32, v int32) { *p = v }

func main() {
	rand.Seed(time.Now().UnixNano())
	fmt.Println("================ 哲学家就餐问题演示 ================")

	// naive 与 hier 需要 N 把 fork（共享）；tanenbaum 不需要
	forks := [N]*Chopstick{}
	for i := 0; i < N; i++ {
		forks[i] = &Chopstick{id: i}
	}

	runStrategy("Naive (先左后右)", newNaiveActions(forks))
	runStrategy("Resource Hierarchy (筷子编号小者优先)", newHierActions(forks))
	runStrategy("Tanenbaum 监视器 (Dijkstra+Tanenbaum)", newTanenbaumActions())
}
