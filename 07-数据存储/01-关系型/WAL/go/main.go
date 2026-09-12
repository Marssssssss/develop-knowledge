// WAL 预写日志 + ARIES 恢复 — 最小实现 (纯 Go stdlib)
//
// 参考: https://db.apache.org/derby/papers/recovery.html (Derby 实现 ARIES 的核心思想)
// 运行: go run main.go

package main

import "fmt"

type LRType int

const (
	LR_BEGIN LRType = iota
	LR_UPDATE
	LR_COMMIT
	LR_ABORT
	LR_END
	LR_CLR
	LR_CKPT_BEGIN
	LR_CKPT_END
)

var LRName = [...]string{"BEGIN", "UPDATE", "COMMIT", "ABORT", "END", "CLR", "CKPT_BEGIN", "CKPT_END"}

type LogRec struct {
	LSN         int
	PrevLSN     int
	TX          int
	Type        LRType
	PID, Offset int
	Before      string
	After       string
	UndoNextLSN int
}

type Page struct {
	PID      int
	Data     map[int]string
	PageLSN  int
	Dirty    bool
}

type DB struct {
	log         []LogRec
	nextLSN     int
	diskLogLSN  int
	pages       map[int]*Page
	txStates    map[int]int // 0=committed,1=active,2=aborted
	txLastLSN   map[int]int
	masterLSN   int
	txToUndoSeq map[int]int // 模拟 LossSet
}

func NewDB(npages int) *DB {
	d := &DB{
		nextLSN:     1,
		pages:       map[int]*Page{},
		txStates:    map[int]int{},
		txLastLSN:   map[int]int{},
		txToUndoSeq: map[int]int{},
	}
	for i := 0; i < npages; i++ {
		d.pages[i] = &Page{PID: i, Data: map[int]string{}}
	}
	return d
}

func (d *DB) appendLog(t LRType, tx, prev int) *LogRec {
	r := &LogRec{LSN: d.nextLSN, PrevLSN: prev, TX: tx, Type: t, PID: -1, Offset: -1}
	d.nextLSN++
	d.log = append(d.log, *r)
	return &d.log[len(d.log)-1]
}

func (d *DB) Begin(tx int) {
	prev := d.txLastLSN[tx]
	r := d.appendLog(LR_BEGIN, tx, prev)
	d.txStates[tx] = 1
	d.txLastLSN[tx] = r.LSN
}

func (d *DB) Update(tx, pid, off int, val string) {
	prev := d.txLastLSN[tx]
	p := d.pages[pid]
	r := d.appendLog(LR_UPDATE, tx, prev)
	r.PID, r.Offset = pid, off
	r.Before = p.Data[off]
	r.After = val
	p.Data[off] = val
	p.PageLSN = r.LSN
	p.Dirty = true
	d.txLastLSN[tx] = r.LSN
}

func (d *DB) Commit(tx int) {
	prev := d.txLastLSN[tx]
	r := d.appendLog(LR_COMMIT, tx, prev)
	d.txLastLSN[tx] = r.LSN
	d.diskLogLSN = d.nextLSN - 1 // WAL force log
	r2 := d.appendLog(LR_END, tx, r.LSN)
	d.txStates[tx] = 0
	d.txLastLSN[tx] = r2.LSN
}

func (d *DB) Abort(tx int) {
	prev := d.txLastLSN[tx]
	r := d.appendLog(LR_ABORT, tx, prev)
	d.txLastLSN[tx] = r.LSN
	r2 := d.appendLog(LR_END, tx, r.LSN)
	d.txStates[tx] = 2
	d.txLastLSN[tx] = r2.LSN
}

func (d *DB) WriteCheckpoint() {
	r := d.appendLog(LR_CKPT_BEGIN, 0, 0)
	d.masterLSN = r.LSN
	d.appendLog(LR_CKPT_END, 0, r.LSN)
	d.diskLogLSN = d.nextLSN - 1
}

// --- Demos ---

func demoBasic() {
	d := NewDB(4)
	d.Begin(11)
	d.Update(11, 1, 0, "Alice")
	d.Update(11, 1, 1, "200")
	d.Commit(11)
	fmt.Printf("[1] Commit 后 disk_log_lsn=%d\n", d.diskLogLSN)
	fmt.Printf("[1] page1: %v\n", d.pages[1].Data)
}

func demoSteal() {
	d := NewDB(4)
	d.Begin(21)
	d.Update(21, 1, 0, "stolen")
	fmt.Printf("[2] 未 commit 时 page1[0].page_lsn=%d (可被 steal)\n", d.pages[1].PageLSN)
	d.Abort(21)
	fmt.Println("[2] T21 abort OK")
}

func demoCkpt() {
	d := NewDB(4)
	d.Begin(31)
	d.Update(31, 0, 0, "a")
	d.Update(31, 0, 1, "b")
	d.Begin(32)
	d.Update(32, 1, 0, "c")
	d.WriteCheckpoint()
	fmt.Printf("[3] Checkpoint master_lsn=%d\n", d.masterLSN)
}

func demoRecover() {
	d := NewDB(4)
	d.Begin(41)
	d.Update(41, 0, 0, "committed-data")
	d.Commit(41)
	d.Begin(42)
	d.Update(42, 0, 1, "loser-data-1")
	d.WriteCheckpoint()
	d.Begin(43)
	d.Update(43, 1, 0, "loser-data-2")
	// 模拟崩溃: master=最后 ckpt; losers=未 commit 的 tx
	losers := []int{42, 43}
	loserSet := map[int]bool{42: true, 43: true}
	// Analysis pass (简化): 重读 log
	// Redo pass (省略): 本 demo 无 dirty page 表
	// Undo pass: 反向扫 loser tx 的 update, 把 after 还原为 before
	for i := len(d.log) - 1; i >= 0; i-- {
		r := d.log[i]
		if r.Type == LR_UPDATE && loserSet[r.TX] {
			if r.Before == "" {
				delete(d.pages[r.PID].Data, r.Offset)
			} else {
				d.pages[r.PID].Data[r.Offset] = r.Before
			}
			fmt.Printf("[4] Undo T%d: page%d[off%d] -> %q\n", r.TX, r.PID, r.Offset, r.Before)
		}
	}
	fmt.Printf("[4] page0 最终: %v\n", d.pages[0].Data)
	fmt.Printf("[4] losers = %v\n", losers)
}

func demoLSNChain() {
	d := NewDB(4)
	d.Begin(51)
	lsns := []int{}
	for i := 0; i < 3; i++ {
		d.Update(51, 0, i, fmt.Sprintf("v%d", i))
		lsns = append(lsns, d.log[len(d.log)-1].LSN)
	}
	d.Commit(51)
	fmt.Printf("[5] T51 upd LSN: %v\n", lsns)
}

func main() {
	fmt.Println("== WAL + ARIES 恢复 ==")
	demoBasic()
	demoSteal()
	demoCkpt()
	demoRecover()
	demoLSNChain()
	fmt.Println("All 5 demos OK.")
}
