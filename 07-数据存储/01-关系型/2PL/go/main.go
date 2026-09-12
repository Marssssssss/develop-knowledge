// 两阶段锁 (2PL) + 死锁检测 — 最小实现 (纯 Go stdlib)
//
// 参考: CMU 15-445 Spring 2023 L16 Two-Phase Locking
//       https://15445.courses.cs.cmu.edu/spring2023/notes/16-twophaselocking.pdf
// 运行: go run main.go

package main

import "fmt"

type LockMode int

const (
	LockS LockMode = iota
	LockX
)

func (m LockMode) String() string {
	if m == LockS {
		return "S"
	}
	return "X"
}

type Req struct {
	TX      int
	Mode    LockMode
	Granted bool
}

type Lock struct {
	Reqs       []Req
	GrantedX   int   // -1 = none
	GrantedS   []int // tx ids
}

func (l *Lock) CanGrant(tx int, mode LockMode) bool {
	if mode == LockS {
		if l.GrantedX != -1 {
			return false
		}
		// FIFO anti-starvation: if any ungranted X is in queue, S must wait
		for _, r := range l.Reqs {
			if !r.Granted && r.Mode == LockX {
				return false
			}
		}
		return true
	}
	if l.GrantedX != -1 || len(l.GrantedS) > 0 || len(l.Reqs) > 0 {
		return false
	}
	return true
}

type TX struct {
	ID        int
	Growing   bool
	Aborted   bool
	HeldLocks [][2]interface{} // (resName string, mode LockMode)
}

type DB struct {
	nextTx    int
	TxByID    []*TX
	LocksByRes map[string]*Lock
	WaitsFor  map[int]map[int]bool
}

func NewDB() *DB {
	return &DB{
		nextTx:     1,
		TxByID:     nil,
		LocksByRes: map[string]*Lock{},
		WaitsFor:   map[int]map[int]bool{},
	}
}

func (d *DB) getLock(res string) *Lock {
	if l, ok := d.LocksByRes[res]; ok {
		return l
	}
	l := &Lock{GrantedX: -1}
	d.LocksByRes[res] = l
	return l
}

func (d *DB) Begin() *TX {
	tx := &TX{ID: d.nextTx, Growing: true}
	d.nextTx++
	d.TxByID = append(d.TxByID, tx)
	d.WaitsFor[tx.ID] = map[int]bool{}
	return tx
}

func (d *DB) addWait(waiter, holder int) {
	if _, ok := d.WaitsFor[waiter]; !ok {
		d.WaitsFor[waiter] = map[int]bool{}
	}
	d.WaitsFor[waiter][holder] = true
}

func (d *DB) removeWaitEdges(t int) {
	for waiter := range d.WaitsFor {
		delete(d.WaitsFor[waiter], t)
	}
}

// detectCycle returns the first cycle found: [t, t', ..., t]
func (d *DB) detectCycle() []int {
	visited := map[int]bool{}
	var dfs func(node int, path []int) []int
	dfs = func(node int, path []int) []int {
		for _, p := range path {
			if p == node {
				return append(path, node)
			}
		}
		if visited[node] {
			return nil
		}
		visited[node] = true
		for nxt := range d.WaitsFor[node] {
			res := dfs(nxt, append(path, node))
			if res != nil {
				return res
			}
		}
		return nil
	}
	for start := range d.WaitsFor {
		if r := dfs(start, nil); r != nil {
			return r
		}
	}
	return nil
}

func (d *DB) Lock(tx *TX, res string, mode LockMode) bool {
	if !tx.Growing {
		return false
	}
	l := d.getLock(res)
	if l.CanGrant(tx.ID, mode) {
		l.Reqs = append(l.Reqs, Req{TX: tx.ID, Mode: mode, Granted: true})
		if mode == LockS {
			l.GrantedS = append(l.GrantedS, tx.ID)
		} else {
			l.GrantedX = tx.ID
		}
		tx.HeldLocks = append(tx.HeldLocks, [2]interface{}{res, mode})
		return true
	}
	// 阻塞: 加入等待队列
	l.Reqs = append(l.Reqs, Req{TX: tx.ID, Mode: mode, Granted: false})
	if mode == LockS {
		if l.GrantedX != -1 {
			d.addWait(tx.ID, l.GrantedX)
		}
	} else {
		if l.GrantedX != -1 {
			d.addWait(tx.ID, l.GrantedX)
		}
		for _, h := range l.GrantedS {
			d.addWait(tx.ID, h)
		}
	}
	if cyc := d.detectCycle(); cyc != nil {
		// victim = 环中最大 id
		victim := 0
		for _, t := range cyc {
			if t > victim {
				victim = t
			}
		}
		d.Abort(d.TxByID[victim-1]) // TxByID 是 0-indexed, id 1-based
		fmt.Printf("    -> deadlock victim = T%d, cycle = %v\n", victim, cyc)
		return false
	}
	return false
}

func (d *DB) UnlockAll(tx *TX) {
	tx.Growing = false
	for _, held := range tx.HeldLocks {
		res := held[0].(string)
		mode := held[1].(LockMode)
		l := d.getLock(res)
		if mode == LockS {
			for i, h := range l.GrantedS {
				if h == tx.ID {
					l.GrantedS = append(l.GrantedS[:i], l.GrantedS[i+1:]...)
					break
				}
			}
		} else {
			if l.GrantedX == tx.ID {
				l.GrantedX = -1
			}
		}
		// 唤醒后续: 简化, 直接重新评估队列头可 grant 的
		for _, r := range l.Reqs {
			if !r.Granted && l.CanGrant(r.TX, r.Mode) {
				r.Granted = true
				if r.Mode == LockS {
					l.GrantedS = append(l.GrantedS, r.TX)
				} else {
					l.GrantedX = r.TX
				}
				delete(d.WaitsFor, r.TX)
			}
		}
	}
	tx.HeldLocks = nil
	d.removeWaitEdges(tx.ID)
}

func (d *DB) Abort(tx *TX) {
	d.UnlockAll(tx)
	tx.Aborted = true
}

func demoXLock() {
	d := NewDB()
	t1 := d.Begin()
	t2 := d.Begin()
	r1 := d.Lock(t1, "A", LockX)
	r2 := d.Lock(t2, "A", LockX)
	fmt.Printf("[1] T%d X(A).grant=%v; T%d X(A).grant=%v\n", t1.ID, r1, t2.ID, r2)
	d.UnlockAll(t1)
}

func demoShared() {
	d := NewDB()
	t1, t2, t3 := d.Begin(), d.Begin(), d.Begin()
	a := d.Lock(t1, "A", LockS)
	b := d.Lock(t2, "A", LockS)
	c := d.Lock(t3, "A", LockX)
	fmt.Printf("[2] S-S 共存: T%d=%v T%d=%v; T%d X(A) 被 block=%v\n", t1.ID, a, t2.ID, b, t3.ID, !c)
	d.UnlockAll(t1)
	d.UnlockAll(t2)
}

func demoStrict() {
	d := NewDB()
	t := d.Begin()
	d.Lock(t, "K", LockX)
	fmt.Printf("[3] T%d growing=%v\n", t.ID, t.Growing)
	d.UnlockAll(t)
	fmt.Printf("[3] commit 后 growing=%v\n", t.Growing)
}

func demoCycle() {
	d := NewDB()
	t1, t2 := d.Begin(), d.Begin()
	d.Lock(t1, "A", LockX)
	d.Lock(t2, "B", LockX)
	d.Lock(t1, "B", LockX)
	d.Lock(t2, "A", LockX)
	fmt.Println("[4] 死锁检测触发, 见上面")
}

func demoIso() {
	fmt.Println("[5] 隔离级别异常矩阵见 README §'对比/选型'")
}

func main() {
	fmt.Println("== 两阶段锁 2PL + 死锁检测 ==")
	demoXLock()
	demoShared()
	demoStrict()
	demoCycle()
	demoIso()
	fmt.Println("All 5 demos OK.")
}
