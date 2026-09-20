// spinlock_sem.go —— 自旋锁与信号量语义的 Go 复刻（本机人工审查，不实跑）
//
// 语义来源与 Python 版同源：IEEE Std 1003.1-2017 pthread_spin_lock / pthread_spin_trylock、
// sem_overview(7)、sem_wait(3)。
// 关键不变式：
//   1. 自旋锁不排队（TAS），ticket 锁严格先来后到；
//   2. 持有者递归上锁是 undefined，不是「返回 EDEADLK」；trylock 被持有返回 EBUSY；两者都不返回 EINTR；
//   3. 信号量值永不为负；EINTR 时值保持不变；命名信号量内核持久。
package main

import "fmt"

const (
	eagain = 11
	ebusy  = 16
	eintr  = 4
	enoent = 2
	einval = 22

	nameMax = 255
)

type result struct {
	err  int // 0 表示成功
	wait *waiter
}

func (r result) isErr() bool { return r.err != 0 }

type waiter struct {
	tid   int
	woken bool
}

// TASLock：test-and-set，谁抢到算谁的
type TASLock struct {
	heldBy int
	held   bool
	spins  int
}

func (l *TASLock) TryLock(tid int) int {
	if !l.held {
		l.held, l.heldBy = true, tid
		return 0
	}
	return ebusy
}

func (l *TASLock) Lock(tid, budget int) string {
	n := 0
	for l.TryLock(tid) == ebusy {
		l.spins++
		n++
		if budget > 0 && n > budget {
			return "deadlock" // 递归上锁：undefined，本模型观测为自旋到死
		}
	}
	return "ok"
}

func (l *TASLock) Unlock() { l.held = false }

// TicketLock：先取号再等叫号
type TicketLock struct {
	nextTicket, nowServing int
	spins                  int
}

func (l *TicketLock) Take() int {
	t := l.nextTicket
	l.nextTicket++
	return t
}

func (l *TicketLock) Acquire(ticket int) {
	for l.nowServing != ticket {
		l.spins++
	}
}

func (l *TicketLock) Release() { l.nowServing++ }

// Semaphore：值永不为负
type Semaphore struct {
	value int
	queue []*waiter
}

func (s *Semaphore) Wait(tid int, signal bool) result {
	if signal && s.value == 0 {
		return result{err: eintr} // 出错时值保持不变
	}
	if s.value > 0 {
		s.value--
		return result{}
	}
	w := &waiter{tid: tid}
	s.queue = append(s.queue, w)
	return result{wait: w}
}

func (s *Semaphore) TryWait() result {
	if s.value > 0 {
		s.value--
		return result{}
	}
	return result{err: eagain}
}

// Post 值 +1；有等待者则唤醒其一（被唤醒者随即把它拿走）
func (s *Semaphore) Post() int {
	s.value++
	if len(s.queue) > 0 {
		w := s.queue[0]
		s.queue = s.queue[1:]
		w.woken = true
		s.value--
		return w.tid
	}
	return -1
}

func validNamedSem(name string) bool {
	if len(name) == 0 || name[0] != '/' {
		return false
	}
	if len(name) > nameMax-4 {
		return false
	}
	for i := 1; i < len(name); i++ {
		if name[i] == '/' {
			return false
		}
	}
	return true
}

// NamedTable：/dev/shm 下的命名信号量，内核持久
type NamedTable struct {
	table    map[string]*Semaphore
	unlinked map[string]bool
}

func (t *NamedTable) Open(name string, value int) (*Semaphore, int) {
	if !validNamedSem(name) {
		return nil, einval
	}
	if t.unlinked[name] {
		return nil, enoent
	}
	if t.table[name] == nil {
		t.table[name] = &Semaphore{value: value}
	}
	return t.table[name], 0
}

func (t *NamedTable) Unlink(name string) {
	t.unlinked[name] = true
	delete(t.table, name)
}

func (t *NamedTable) ShmPath(name string) string { return "/dev/shm/sem." + name[1:] }

// SingleCPU：quantum=0 表示不可抢占 —— 单核自旋死锁的成因
func SingleCPU(quantum, holdTicks, maxTicks int) (bool, int, int) {
	ticks, prog, spins := 0, 0, 0
	for ticks < maxTicks {
		if quantum == 0 {
			ticks++
			spins++
			continue
		}
		for i := 0; i < quantum; i++ {
			ticks++
			spins++
			if ticks >= maxTicks {
				return false, ticks, spins
			}
		}
		for i := 0; i < quantum; i++ {
			ticks++
			prog++
			if prog >= holdTicks {
				return true, ticks, spins
			}
		}
	}
	return false, ticks, spins
}

// PriorityInversion：自旋锁不提升持有者，中优先级任务就能插队
func PriorityInversion(piAware bool, holdTicks, mediumTicks int) (int, int) {
	ticks, prog, mediumLeft := 0, 0, mediumTicks
	for {
		ticks++
		effL := 1
		if piAware {
			effL = 10
		}
		if mediumLeft > 0 && effL < 5 {
			mediumLeft--
			continue
		}
		prog++
		if prog >= holdTicks {
			return ticks, mediumTicks - mediumLeft
		}
	}
}

func main() {
	l := &TASLock{}
	fmt.Println("TAS 首次:", l.TryLock(7), "再次(递归):", l.Lock(7, 50))

	done, ticks, spins := SingleCPU(0, 3, 500)
	fmt.Println("单核不可抢占 -> 完成?", done, "空转", spins)
	done, ticks, spins = SingleCPU(5, 3, 500)
	fmt.Println("单核有轮转 -> 完成?", done, "总 tick", ticks, "空转", spins)

	w1, m1 := PriorityInversion(false, 3, 100)
	w2, m2 := PriorityInversion(true, 3, 100)
	fmt.Printf("自旋锁等 %d tick(M 跑了 %d) / PI 等 %d tick(M 跑了 %d)\n", w1, m1, w2, m2)

	s := &Semaphore{}
	blk := s.Wait(1, false)
	fmt.Println("值为 0 阻塞:", !blk.isErr(), "值仍为", s.value)
	fmt.Println("EINTR 时:", s.Wait(1, true).err == eintr, "值仍为", s.value)
	fmt.Println("post 唤醒:", s.Post(), "值仍为", s.value)

	tbl := &NamedTable{table: map[string]*Semaphore{}, unlinked: map[string]bool{}}
	sem, _ := tbl.Open("/jobs", 2)
	fmt.Println("命名信号量初值:", sem.value, "shm 路径:", tbl.ShmPath("/jobs"))
	tbl.Unlink("/jobs")
	_, err := tbl.Open("/jobs", 9)
	fmt.Println("unlink 后重开:", err == enoent)
}
