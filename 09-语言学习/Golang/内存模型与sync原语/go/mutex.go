// sync.Mutex 的状态机模型（对齐 Go 1.24 internal/sync/mutex.go）。
//
// state 位布局：bit0 mutexLocked=1、bit1 mutexWoken=2、bit2 mutexStarving=4、
// bit3 起为等待者计数（mutexWaiterShift=3）。正常模式允许新到者插队（barge），
// 饥饿模式改为直接移交所有权；等待超过 1ms 的等待者负责把 Mutex 切到饥饿模式。
// 与 python/sync_mutex.py 同构。
package main

import "fmt"

const (
	mutexLocked    = 1 << 0
	mutexWoken     = 1 << 1
	mutexStarving  = 1 << 2
	mutexWaiterSh  = 3
	starvationNS   = 1000000 // 1ms
)

type fatalError struct{ msg string }

func (e *fatalError) Error() string { return e.msg }

type waiter struct {
	gid        int
	waitStart  int
	starving   bool
	awoke      bool
	iter       int
	queueLifo  bool
	counted    bool
}

type syncMutex struct {
	state      int
	queue      []int
	wmap       map[int]*waiter
	maxSpin    int
	spinEvents int
	handoffs   int
	barges     int
	lifoRequeue int
	log        []string
}

func newSyncMutex(maxSpin int) *syncMutex {
	return &syncMutex{wmap: map[int]*waiter{}, maxSpin: maxSpin}
}

func (m *syncMutex) waiters() int      { return m.state >> mutexWaiterSh }
func (m *syncMutex) starving() bool    { return m.state&mutexStarving != 0 }
func (m *syncMutex) woken() bool       { return m.state&mutexWoken != 0 }

func (m *syncMutex) tryLock() bool {
	if m.state&(mutexLocked|mutexStarving) != 0 {
		return false
	}
	m.state |= mutexLocked
	return true
}

func canSpin(iter, maxSpin int) bool { return iter < maxSpin }

// lock 返回 (是否拿到锁, 是否需要阻塞)。
func (m *syncMutex) lock(gid, now int, w *waiter) (bool, bool, error) {
	if w != nil {
		m.wmap[gid] = w
	}
	if m.state&(mutexLocked|mutexStarving) != 0 {
		return m.lockSlow(gid, now, w)
	}
	m.state |= mutexLocked
	if m.waiters() > 0 {
		m.barges++
		m.log = append(m.log, fmt.Sprintf("t=%d G%d 快路径 CAS 抢到锁（越过等待者）", now, gid))
	}
	return true, false, nil
}

func (m *syncMutex) lockSlow(gid, now int, w *waiter) (bool, bool, error) {
	if w == nil {
		w = &waiter{gid: gid}
	}
	wasAwoke := w.awoke // 源码里 awoke 的清除发生在下一轮循环，单趟模型用入口快照
	if (m.state&(mutexLocked|mutexStarving)) == mutexLocked && canSpin(w.iter, m.maxSpin) {
		if !w.awoke && !m.woken() && m.waiters() != 0 {
			m.state |= mutexWoken
			w.awoke = true
		}
		m.spinEvents++
		w.iter++
		m.log = append(m.log, fmt.Sprintf("t=%d G%d 自旋（iter=%d）", now, gid, w.iter))
	}
	newState := m.state
	if m.state&mutexStarving == 0 {
		newState |= mutexLocked
	}
	if !w.counted {
		newState += 1 << mutexWaiterSh
		w.counted = true
	}
	if w.starving && m.state&mutexLocked != 0 {
		newState |= mutexStarving
	}
	if wasAwoke {
		if newState&mutexWoken == 0 {
			return false, false, &fatalError{"sync: inconsistent mutex state"}
		}
		newState &= ^mutexWoken
		w.awoke = false
	}
	m.state = newState
	w.queueLifo = w.waitStart != 0
	if w.waitStart == 0 {
		w.waitStart = now
	}
	if !contains(m.queue, gid) {
		if w.queueLifo {
			m.queue = append([]int{gid}, m.queue...)
			m.lifoRequeue++
		} else {
			m.queue = append(m.queue, gid)
		}
	}
	if now-w.waitStart > starvationNS {
		w.starving = true
	}
	m.log = append(m.log, fmt.Sprintf("t=%d G%d 排队（等待者=%d，饥饿=%v）",
		now, gid, m.waiters(), w.starving))
	return false, true, nil
}

func contains(s []int, v int) bool {
	for _, x := range s {
		if x == v {
			return true
		}
	}
	return false
}

// unlock 返回被唤醒（或将被移交）的 gid。
func (m *syncMutex) unlock(now int) (int, error) {
	newState := m.state - mutexLocked
	if newState&mutexLocked == mutexLocked {
		return 0, &fatalError{"sync: unlock of unlocked mutex"}
	}
	m.state = newState
	if newState != 0 {
		return m.unlockSlow(now)
	}
	return 0, nil
}

func (m *syncMutex) unlockSlow(now int) (int, error) {
	if m.state&mutexStarving == 0 {
		if m.waiters() == 0 || m.state&(mutexLocked|mutexWoken|mutexStarving) != 0 {
			return 0, nil
		}
		m.state = (m.state - (1 << mutexWaiterSh)) | mutexWoken
		who := 0
		if len(m.queue) > 0 {
			who = m.queue[0]
			m.queue = m.queue[1:]
		}
		if w, ok := m.wmap[who]; ok {
			w.counted = false
			w.awoke = true
		}
		m.log = append(m.log, fmt.Sprintf("t=%d 正常模式唤醒 G%d（须与新到者竞争）", now, who))
		return who, nil
	}
	// 饥饿模式：直接移交所有权，新到者不能插队
	m.handoffs++
	who := 0
	if len(m.queue) > 0 {
		who = m.queue[0]
	}
	m.log = append(m.log, fmt.Sprintf("t=%d 饥饿模式**移交**所有权给 G%d", now, who))
	return who, nil
}

// acquireHandoff 饥饿模式下被移交者拿锁：返回是否退出饥饿模式。
func (m *syncMutex) acquireHandoff(gid, now int, w *waiter) (bool, error) {
	if m.state&(mutexLocked|mutexWoken) != 0 || m.waiters() == 0 {
		return false, &fatalError{"sync: inconsistent mutex state"}
	}
	delta := mutexLocked - (1 << mutexWaiterSh)
	w.counted = false
	leaving := !w.starving || m.waiters() == 1
	if leaving {
		delta -= mutexStarving
	}
	m.state += delta
	if len(m.queue) > 0 && m.queue[0] == gid {
		m.queue = m.queue[1:]
	}
	return leaving, nil
}
