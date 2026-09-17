// Go 内存模型 + sync.Mutex 状态机（与 python/ 同题的第二语言实现）。
//
// 依据：https://go.dev/ref/mem（2022-06-06 版）与 go1.24.0 src/internal/sync/mutex.go。
// 本机无 Go 工具链，编译期正确性依赖人工审查 + _docs/tools/bracket_check.py。
package main

import (
	"fmt"
	"os"
)

var (
	statN    int
	statFail []string
)

func check(label string, cond bool, detail ...interface{}) {
	statN++
	if cond {
		fmt.Println("PASS  " + label)
		return
	}
	statFail = append(statFail, label)
	if len(detail) > 0 {
		fmt.Printf("FAIL  %s  | %v\n", label, detail[0])
		return
	}
	fmt.Println("FAIL  " + label)
}

// ---------------------------------------------------------------- 文档场景
func sendFirst() (*execution, *op, *op) {
	e := &execution{}
	goStmt := e.add(0, "go f()", "go", "")
	wA := e.add(1, `a = "hello, world"`, "write", "a")
	snd := e.add(1, "c <- 0", "send", "c")
	rcv := e.add(0, "<-c", "recv", "c")
	rd := e.add(0, "print(a)", "read", "a")
	e.sync(goStmt, wA).sync(snd, rcv)
	return e, wA, rd
}

func recvFirst(buffered bool) (*execution, *op, *op) {
	e := &execution{}
	goStmt := e.add(0, "go f()", "go", "")
	wA := e.add(1, `a = "hello, world"`, "write", "a")
	rcv := e.add(1, "<-c", "recv", "c")
	snd := e.add(0, "c <- 0", "send", "c")
	rd := e.add(0, "print(a)", "read", "a")
	e.sync(goStmt, wA)
	if !buffered {
		e.sync(rcv, snd) // unbuffered：receive synchronized before send 完成
	}
	return e, wA, rd
}

func goroutineDestruction() (*execution, *op, *op) {
	e := &execution{}
	goStmt := e.add(0, "go func(){...}()", "go", "")
	wA := e.add(1, `a = "hello"`, "write", "a")
	rd := e.add(0, "print(a)", "read", "a")
	e.sync(goStmt, wA)
	return e, wA, rd
}

func mutexDocExample() (*execution, *op, *op) {
	e := &execution{}
	l1 := e.add(0, "l.Lock() 第 1 次", "lock", "l")
	goStmt := e.add(0, "go f()", "go", "")
	wA := e.add(1, `a = "hello, world"`, "write", "a")
	un := e.add(1, "l.Unlock()", "unlock", "l")
	l2 := e.add(0, "l.Lock() 第 2 次", "lock", "l")
	rd := e.add(0, "print(a)", "read", "a")
	e.sync(l1, wA).sync(un, l2)
	return e, wA, rd
}

func main() {
	fmt.Println("=== A. happens-before 关系的基本性质 ===")
	e, wA, rd := sendFirst()
	hb, _ := e.happensBefore()
	n := len(e.ops)
	selfLoop, trans := false, false
	for i := 0; i < n; i++ {
		if hb[i][i] {
			selfLoop = true
		}
	}
	trans = true
	for i := 0; i < n; i++ {
		for k := 0; k < n; k++ {
			for j := 0; j < n; j++ {
				if hb[i][k] && hb[k][j] && !hb[i][j] {
					trans = false
				}
			}
		}
	}
	check("A1 happens-before 是严格偏序（不自反）", !selfLoop)
	check("A2 happens-before 传递闭包成立", trans)
	check("A3 go 语句 happens before 新 goroutine 的首操作", e.ordered(e.ops[0], e.ops[1]))
	check("A4 send happens before 对应 receive 且无竞争", e.ordered(wA, rd) && len(e.races()) == 0)
	check("A5 Requirement 3：可见写唯一", len(e.visibleWrites(rd)) == 1)

	fmt.Println("\n=== B. 文档四个例子：哪些是保证的 ===")
	e, wA, rd = sendFirst()
	check("B1 缓冲 channel + 先发后收 → **保证**打印", e.ordered(wA, rd) && len(e.races()) == 0)
	e, wA, rd = recvFirst(false)
	check("B2 无缓冲 channel + 先收后发 → **仍然保证**打印", e.ordered(wA, rd) && len(e.races()) == 0)
	e, wA, rd = recvFirst(true)
	check("B3 缓冲 channel + 先收后发 → 构成**数据竞争**（文档明说不保证）",
		len(e.races()) > 0 && !e.ordered(wA, rd), e.races())
	check("B4 竞争对正是 (write a, print(a))",
		len(e.races()) == 1 && e.races()[0] == [2]string{wA.label, rd.label}, e.races())
	e, wA, rd = goroutineDestruction()
	check("B5 goroutine 退出**不保证**同步于任何事件 → 该例是竞争",
		len(e.races()) > 0 && !e.ordered(wA, rd))
	e, wA, rd = mutexDocExample()
	check("B6 Mutex 例：第 n 次 Unlock happens before 第 m 次 Lock 返回 → **保证**打印",
		e.ordered(wA, rd) && len(e.races()) == 0)

	fmt.Println("\n=== C. 其余同步机制 ===")
	e = &execution{}
	rcv := e.add(0, "第 1 次 receive", "recv", "c")
	sends := []*op{}
	for i := 1; i <= 4; i++ {
		sends = append(sends, e.add(1, fmt.Sprintf("第 %d 次 send", i), "send", "c"))
	}
	e.sync(rcv, sends[2]) // k=1, C=2 → k+C = 3
	check("C1 容量 2：第 1 次 receive happens before 第 3 次 send 完成", e.ordered(rcv, sends[2]))
	check("C2 但第 1 次 receive **不**同步于第 2 次 send（需到 k+C）", !e.ordered(rcv, sends[1]))
	e = &execution{}
	qInit := e.add(0, "q.init()", "unlock", "init_q")
	pInit := e.add(0, "p.init()", "lock", "init_q")
	mainFn := e.add(0, "main.main()", "lock", "init_p")
	e.sync(qInit, pInit).sync(pInit, mainFn)
	check("C3 init 链 q→p→main 全序（无竞争）", len(e.races()) == 0 && e.ordered(qInit, mainFn))
	e = &execution{}
	st := e.add(0, "atomic.Store(x, 1)", "atomic_write", "x")
	ld := e.add(1, "atomic.Load(x)", "atomic_read", "x")
	e.sync(st, ld)
	check("C4 原子操作由 SC 全序连接 → 不构成竞争", len(e.races()) == 0)
	check("C5 原子读的可见写唯一", len(e.visibleWrites(ld)) == 1)
	e = &execution{}
	e.add(1, "临界区: a = 1", "write", "a")
	e.add(0, "l.TryLock() 失败（无同步效果）", "read", "trylock")
	e.add(0, "读 a", "read", "a")
	check("C6 **失败**的 TryLock 不建立同步关系 → 仍是竞争", len(e.races()) > 0)

	fmt.Println("\n=== D. Mutex 状态位与快路径 ===")
	check("D1 状态位布局 locked=1 / woken=2 / starving=4 / waiterShift=3",
		mutexLocked == 1 && mutexWoken == 2 && mutexStarving == 4 && mutexWaiterSh == 3)
	check("D2 饥饿阈值 = 1e6 ns（1ms）", starvationNS == 1000000)
	m := newSyncMutex(0)
	check("D3 零值 Mutex 可直接 TryLock 成功", m.tryLock())
	check("D4 已加锁时 TryLock 失败", !m.tryLock())
	m = newSyncMutex(0)
	m.state = mutexLocked | mutexStarving
	check("D5 饥饿模式下 TryLock 失败（locked|starving 任一即失败）", !m.tryLock())
	m = newSyncMutex(0)
	if _, err := m.unlock(0); err != nil {
		check("D6 解锁未加锁的 Mutex → fatal", err.Error() == "sync: unlock of unlocked mutex",
			err.Error())
	} else {
		check("D6 解锁未加锁的 Mutex → fatal", false, "未报错")
	}
	m = newSyncMutex(0)
	m.state = mutexLocked
	if _, err := m.acquireHandoff(1, 0, &waiter{gid: 1}); err != nil {
		check("D7 状态不一致时 acquireHandoff 抛 fatal",
			err.Error() == "sync: inconsistent mutex state", err.Error())
	} else {
		check("D7 状态不一致时 acquireHandoff 抛 fatal", false, "未报错")
	}

	fmt.Println("\n=== E. 正常模式：插队（barge）与队首重排 ===")
	m = newSyncMutex(0)
	ok1, blocked1, _ := m.lock(1, 0, nil)
	check("E1 快路径拿到锁且不阻塞", ok1 && !blocked1)
	w2, w3 := &waiter{gid: 2}, &waiter{gid: 3}
	ok2, b2, _ := m.lock(2, 10, w2)
	ok3, b3, _ := m.lock(3, 20, w3)
	check("E2 G2/G3 排队阻塞", !ok2 && b2 && !ok3 && b3)
	check("E3 队列 FIFO：[2, 3]", len(m.queue) == 2 && m.queue[0] == 2 && m.queue[1] == 3, m.queue)
	check("E4 等待者计数为 2", m.waiters() == 2, m.waiters())
	woke, _ := m.unlock(100)
	check("E5 正常模式 unlock 唤醒队首 G2", woke == 2, woke)
	check("E6 唤醒后 state 置了 WOKEN 位", m.woken())
	ok4, b4, _ := m.lock(4, 101, nil)
	check("E7 新到的 G4 抢在 G2 之前拿到锁（barge，正常模式允许）",
		ok4 && !b4 && m.barges == 1, m.barges)
	ok2b, b2b, _ := m.lock(2, 102, w2)
	check("E8 输掉竞争的 G2 被插回**队首**（LIFO 重排）",
		!ok2b && b2b && m.queue[0] == 2 && m.lifoRequeue == 1, m.queue)
	check("E9 重排后 WOKEN 已被清掉（awoke 分支生效）", !m.woken())
	check("E10 全程未进入饥饿模式（等待均 < 1ms）", !m.starving())
	ms := newSyncMutex(2)
	ms.lock(1, 0, nil)
	ms.lock(2, 10, &waiter{gid: 2})
	check("E11 自旋发生但此时没有别的等待者 → **不**置 WOKEN",
		ms.spinEvents == 1 && !ms.woken(), ms.spinEvents)
	ms.lock(3, 20, &waiter{gid: 3})
	check("E12 已有等待者时自旋才置 WOKEN（源码条件 waiterShift != 0）",
		ms.spinEvents == 2 && ms.woken(), ms.spinEvents)

	fmt.Println("\n=== F. 饥饿模式：移交与退出条件 ===")
	m = newSyncMutex(0)
	m.lock(1, 0, nil)
	c := &waiter{gid: 2}
	m.lock(2, 10, c)
	check("F1 G2 刚入队时未置饥饿", !m.starving())
	c.starving = (2000000 - c.waitStart) > starvationNS
	check("F2 等待超过 1ms 后等待者自身置 starving（本地标志）", c.starving)
	m.lock(2, 2000000, c)
	check("F3 只有当前确实被锁住才把 Mutex 切到饥饿模式", m.starving())
	before := m.spinEvents
	m.lock(2, 2000100, c)
	check("F4 饥饿模式下**不再自旋**（所有权靠移交，抢也抢不到）", m.spinEvents == before)
	wokeHandle, _ := m.unlock(2000200)
	check("F5 饥饿模式 unlock **直接移交**所有权给队首",
		wokeHandle == 2 && m.handoffs == 1, wokeHandle)
	leaving, _ := m.acquireHandoff(2, 2000300, c)
	check("F6 通过移交拿到锁：LOCKED 已置、等待者计数已减、饥饿位清掉",
		m.state&mutexLocked != 0 && m.waiters() == 0 && !m.starving())
	check("F7 队里再无等待者 → 退出饥饿模式", leaving)
	m2 := newSyncMutex(0)
	m2.lock(1, 0, nil)
	x, y := &waiter{gid: 2}, &waiter{gid: 3}
	m2.lock(2, 10, x)
	m2.lock(3, 20, y)
	m2.state |= mutexStarving
	x.starving = true
	m2.unlock(100)
	leaving2, _ := m2.acquireHandoff(2, 110, x)
	check("F8 G2 仍在饥饿状态且队里还有 G3 → **不**退出饥饿模式",
		!leaving2 && m2.starving() && m2.waiters() == 1)

	fmt.Println("\n=== G. 尾延迟：两种模式的对照 ===")
	mn := newSyncMutex(0)
	mn.lock(1, 0, nil)
	wn := map[int]*waiter{}
	for _, g := range []int{2, 3, 4} {
		wn[g] = &waiter{gid: g}
		mn.lock(g, g, wn[g])
	}
	check("G1 三个等待者按 FIFO 排队 [2,3,4]",
		len(mn.queue) == 3 && mn.queue[0] == 2 && mn.queue[2] == 4, mn.queue)
	w1, _ := mn.unlock(1000)
	check("G2 正常模式 unlock 唤醒队首 G2", w1 == 2, w1)
	ok99, _, _ := mn.lock(99, 1001, nil)
	check("G3 新到者 G99 插队成功（barges=1）→ 长尾的来源", ok99 && mn.barges == 1, mn.barges)
	ok2g, _, _ := mn.lock(2, 1005, wn[2])
	check("G4 G2 被插队后重排回队首（lifo_requeue=1）",
		!ok2g && mn.queue[0] == 2 && mn.lifoRequeue == 1)
	mh := newSyncMutex(0)
	mh.lock(1, 0, nil)
	wh := map[int]*waiter{}
	for _, g := range []int{2, 3, 4} {
		wh[g] = &waiter{gid: g}
		mh.lock(g, g, wh[g])
	}
	mh.state |= mutexStarving
	w2h, _ := mh.unlock(1000)
	check("G5 饥饿模式 unlock 直接移交队首 G2", w2h == 2 && mh.handoffs == 1)
	ok99h, b99h, _ := mh.lock(99, 1001, nil)
	check("G6 饥饿模式新到者**不能**插队，只能排到队尾（[2,3,4,99]）",
		!ok99h && b99h && len(mh.queue) == 4 && mh.queue[3] == 99, mh.queue)
	leavingG, _ := mh.acquireHandoff(2, 1010, wh[2])
	check("G7 移交完成后队列顺序不变（[3,4,99]）→ 饥饿模式严格 FIFO、无长尾",
		leavingG && len(mh.queue) == 3 && mh.queue[0] == 3 && !mh.starving(), mh.queue)

	fmt.Println("\n=== 汇总 ===")
	fmt.Printf("断言总数 %d；失败 %d %v\n", statN, len(statFail), statFail)
	if len(statFail) > 0 {
		os.Exit(1)
	}
}
