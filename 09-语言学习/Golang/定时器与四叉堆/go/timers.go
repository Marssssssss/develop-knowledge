package main

import "fmt"

// Stop 对应 (*timer).stop：只打标记，不从堆里摘除。
func (t *Timer) Stop() bool {
	ts := t.ts
	if t.state&timerHeaped != 0 {
		t.state |= timerModified
		if t.state&timerZombie == 0 {
			t.state |= timerZombie
			ts.Zombies++
		}
	}
	pending := t.When > 0
	t.When = 0
	t.unlock()
	return pending
}

// Modify 对应 (*timer).modify：Reset 走这里，堆快照延后同步。
func (t *Timer) Modify(when, period int64) (pending, wake bool) {
	ts := t.ts
	if when <= 0 {
		panic("timer when must be positive")
	}
	if period < 0 {
		panic("timer period must be non-negative")
	}
	t.Period = period
	pending = t.When > 0
	t.When = when
	if t.state&timerHeaped != 0 {
		t.state |= timerModified
		if t.state&timerZombie != 0 {
			ts.Zombies--
			t.state &^= timerZombie
		}
		if ts.MinWhenModified == 0 || when < ts.MinWhenModified {
			wake = true
			t.astate = t.state // 官方：先刷 astate 再改 minWhenModified
			ts.UpdateMinWhenModified(when)
		}
	}
	t.unlock()
	return pending, wake
}

// UpdateHeap 对应 (*timer).updateHeap：t 必须是 heap[0]。
func (ts *Timers) UpdateHeap(t *Timer) bool {
	if t.ts != ts || ts.Heap[0].Timer != t {
		badTimer()
	}
	if t.state&timerZombie != 0 {
		t.state &^= timerHeaped | timerZombie | timerModified
		ts.Zombies--
		ts.DeleteMin()
		return true
	}
	if t.state&timerModified != 0 {
		t.state &^= timerModified
		ts.Heap[0].When = t.When
		ts.SiftDown(0)
		ts.updateMinWhenHeap()
		return true
	}
	return false
}

// CleanHead 对应 (*timers).cleanHead：先零成本摘堆尾 zombie，再处理堆顶。
func (ts *Timers) CleanHead() {
	for {
		if len(ts.Heap) == 0 {
			return
		}
		n := len(ts.Heap)
		tail := ts.Heap[n-1].Timer
		if tail != nil && tail.astate&timerZombie != 0 {
			if tail.state&timerZombie != 0 {
				tail.state &^= timerHeaped | timerZombie | timerModified
				tail.ts = nil
				ts.Zombies--
				ts.Heap[n-1] = TimerWhen{}
				ts.Heap = ts.Heap[:n-1]
			}
			tail.unlock()
			continue
		}
		t := ts.Heap[0].Timer
		if t.astate&(timerModified|timerZombie) == 0 {
			return // 快路径
		}
		if !ts.UpdateHeap(t) {
			return
		}
		t.unlock()
	}
}

// Adjust 对应 (*timers).adjust：扫全堆清 zombie、同步 modified 的 when。
func (ts *Timers) Adjust(now int64, force bool) {
	if !force {
		first := ts.MinWhenModified
		if first == 0 || first > now {
			return
		}
	}
	ts.MinWhenHeap = ts.WakeTime()
	ts.MinWhenModified = 0
	changed := false
	for i := 0; i < len(ts.Heap); i++ {
		t := ts.Heap[i].Timer
		if t.astate&(timerModified|timerZombie) == 0 {
			continue
		}
		if t.state&timerHeaped == 0 {
			badTimer()
		}
		if t.state&timerZombie != 0 {
			ts.Zombies--
			t.state &^= timerHeaped | timerZombie | timerModified
			n := len(ts.Heap)
			ts.Heap[i] = ts.Heap[n-1]
			ts.Heap[n-1] = TimerWhen{}
			ts.Heap = ts.Heap[:n-1]
			t.ts = nil
			t.unlock()
			i--
			changed = true
			continue
		}
		if t.state&timerModified != 0 {
			ts.Heap[i].When = t.When
			t.state &^= timerModified
			t.unlock()
			changed = true
		}
	}
	if changed {
		ts.InitHeap()
	}
	ts.updateMinWhenHeap()
}

// WakeTime 对应 (*timers).wakeTime：modified 优先于 heap。
func (ts *Timers) WakeTime() int64 {
	if ts.MinWhenModified != 0 {
		return ts.MinWhenModified
	}
	return ts.MinWhenHeap
}

// unlockAndRun 对应 (*timer).unlockAndRun（去掉 channel 与 race 相关分支）。
func (ts *Timers) unlockAndRun(now int64, t *Timer) int {
	delay := now - t.When
	var next int64
	if t.Period > 0 {
		// 官方：next = when + period*(1 + delay/period)，整数除法
		next = t.When + t.Period*(1+delay/t.Period)
		if next < 0 {
			next = maxWhen
		}
	}
	t.When = next
	if t.state&timerHeaped != 0 {
		t.state |= timerModified
		if next == 0 {
			t.state |= timerZombie
			ts.Zombies++
			if ts.Zombies > ts.ZombiePeak {
				ts.ZombiePeak = ts.Zombies
			}
		}
		ts.UpdateHeap(t)
	}
	t.unlock()
	ts.Fired = append(ts.Fired, fmt.Sprintf("%s@%d", t.Name, now))
	return 0
}

// Run 对应 (*timers).run：返回 -1（空堆）/ 下次时刻 / 0（已触发）。
func (ts *Timers) Run(now int64) int64 {
	for {
		if len(ts.Heap) == 0 {
			return -1
		}
		tw := ts.Heap[0]
		t := tw.Timer
		if t.ts != ts {
			panic("bad ts")
		}
		if t.astate&(timerModified|timerZombie) == 0 && tw.When > now {
			return tw.When
		}
		if ts.UpdateHeap(t) {
			t.unlock()
			continue
		}
		if t.When > now {
			t.unlock()
			return t.When
		}
		return int64(ts.unlockAndRun(now, t))
	}
}
