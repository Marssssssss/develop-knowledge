// MVCC 多版本并发控制 — InnoDB 风格最小实现 (纯 Go stdlib)
//
// 核心模型: Row{trx_id, roll_ptr, value, deleted}; undo log 单链;
// ReadView 可见性规则 (RC: 每 SELECT 新建; RR: 首 SELECT 创建并复用)。
//
// 参考: MySQL 9.7 Reference Manual 17.3 InnoDB Multi-Versioning
// https://dev.mysql.com/doc/refman/9.7/en/innodb-multi-versioning.html
//
// 运行: go run main.go

package main

import "fmt"

type UndoEntry struct {
	RollPtrNext int // 链头方向, 指向 undo log 中下一个更老 entry 的 idx; -1 = 链底
	TrxID       int
	Value       int
	Deleted     bool
}

type Row struct {
	Value    int
	TrxID    int // DB_TRX_ID: 最后修改者, 0 = 未初始
	RollPtr  int // DB_ROLL_PTR: undo log 中上一个版本 idx; -1 = 基线
	Deleted  bool
}

type ReadView struct {
	LowWater   int   // 创建 ReadView 时仍活跃的最小 trx_id
	HighWater  int   // 创建 ReadView 时下一个将分配的事务 id
	ActiveIDs  []int // 活跃事务 id 列表
	CreatorID  int
}

type Transaction struct {
	ID          int
	IsRR        bool // REPEATABLE READ vs READ COMMITTED
	Snapshot    *ReadView
	Committed   bool
	Aborted     bool
}

type MVCCDB struct {
	Rows         map[string]*Row
	UndoLog      []UndoEntry
	NextTxID     int
	ActiveTxList []*Transaction // used when acquiring snapshot
}

func NewMVCCDB() *MVCCDB {
	return &MVCCDB{
		Rows:         map[string]*Row{},
		NextTxID:     1,
		ActiveTxList: nil,
	}
}

func (db *MVCCDB) Begin(isRR bool) *Transaction {
	tx := &Transaction{ID: db.NextTxID, IsRR: isRR}
	db.NextTxID++
	db.ActiveTxList = append(db.ActiveTxList, tx)
	return tx
}

func (db *MVCCDB) Commit(tx *Transaction) { tx.Committed = true }
func (db *MVCCDB) Rollback(tx *Transaction) { tx.Aborted = true }

func (tx *Transaction) AcquireSnapshot(db *MVCCDB) {
	if tx.IsRR && tx.Snapshot != nil {
		return
	}
	rv := &ReadView{CreatorID: tx.ID, HighWater: db.NextTxID}
	active := []int{}
	var minActive int = 0
	for _, t := range db.ActiveTxList {
		if t.Committed || t.Aborted {
			continue
		}
		active = append(active, t.ID)
		if minActive == 0 || t.ID < minActive {
			minActive = t.ID
		}
	}
	rv.ActiveIDs = active
	if len(active) > 0 {
		rv.LowWater = minActive
	} else {
		rv.LowWater = db.NextTxID
	}
	tx.Snapshot = rv
}

func (tx *Transaction) visibleRule(trxID int) bool {
	rv := tx.Snapshot
	if trxID == rv.CreatorID {
		return true
	}
	if trxID < rv.LowWater {
		return true
	}
	if trxID >= rv.HighWater {
		return false
	}
	for _, id := range rv.ActiveIDs {
		if id == trxID {
			return false
		}
	}
	return true
}

// Read returns the visible value or (nil, false) if deleted/not visible
func (tx *Transaction) Read(db *MVCCDB, name string) (*int, bool) {
	tx.AcquireSnapshot(db)
	r, ok := db.Rows[name]
	if !ok {
		return nil, false
	}
	curTrx, curRp := r.TrxID, r.RollPtr
	for {
		if tx.visibleRule(curTrx) {
			if r.Deleted {
				return nil, false
			}
			return &r.Value, true
		}
		if curRp == -1 {
			return nil, false
		}
		e := db.UndoLog[curRp]
		if tx.visibleRule(e.TrxID) {
			if e.Deleted {
				return nil, false
			}
			return &e.Value, true
		}
		curTrx, curRp = e.TrxID, e.RollPtrNext
	}
}

func (tx *Transaction) Update(db *MVCCDB, name string, value int) {
	r, ok := db.Rows[name]
	if !ok {
		r = &Row{TrxID: 0, RollPtr: -1}
		db.Rows[name] = r
	}
	oldIdx := len(db.UndoLog)
	db.UndoLog = append(db.UndoLog, UndoEntry{
		RollPtrNext: r.RollPtr,
		TrxID:       r.TrxID,
		Value:       r.Value,
		Deleted:     r.Deleted,
	})
	r.RollPtr = oldIdx
	r.TrxID = tx.ID
	r.Value = value
	r.Deleted = false
}

// ---- Demos ----

func demoBasic() {
	db := NewMVCCDB()
	db.Rows["x"] = &Row{Value: 100}
	t1 := db.Begin(false)
	t1.Update(db, "x", 200)
	t2 := db.Begin(false)
	v, _ := t2.Read(db, "x")
	fmt.Printf("[1] T2 sees x=%d (before T1 commit)\n", *v)
	if *v != 100 {
		panic("expected 100")
	}
	db.Commit(t1)
	t3 := db.Begin(false)
	v, _ = t3.Read(db, "x")
	fmt.Printf("[1] T3 sees x=%d (after T1 commit)\n", *v)
	if *v != 200 {
		panic("expected 200")
	}
}

func demoRRReuse() {
	db := NewMVCCDB()
	db.Rows["balance"] = &Row{Value: 1000}
	t1 := db.Begin(true) // RR
	v, _ := t1.Read(db, "balance")
	if *v != 1000 {
		panic("expected 1000")
	}
	t2 := db.Begin(false)
	t2.Update(db, "balance", 1500)
	db.Commit(t2)
	v, _ = t1.Read(db, "balance")
	fmt.Printf("[2] RR 2nd read: balance=%d (snapshot reused)\n", *v)
	if *v != 1000 {
		panic("RR should be repeatable")
	}
}

func demoRCPerStmt() {
	db := NewMVCCDB()
	db.Rows["x"] = &Row{Value: 100}
	t1 := db.Begin(false) // RC
	_, _ = t1.Read(db, "x")
	t2 := db.Begin(false)
	t2.Update(db, "x", 999)
	db.Commit(t2)
	v, _ := t1.Read(db, "x")
	fmt.Printf("[3] RC 2nd read sees t2 commit: x=%d\n", *v)
	if *v != 999 {
		panic("RC should see latest committed")
	}
}

func demoChain() {
	db := NewMVCCDB()
	db.Rows["k"] = &Row{Value: 0}
	t1 := db.Begin(false)
	for _, v := range []int{10, 20, 30, 40} {
		t1.Update(db, "k", v)
	}
	db.Commit(t1)
	chain := 0
	cur := db.Rows["k"].RollPtr
	for cur != -1 {
		chain++
		cur = db.UndoLog[cur].RollPtrNext
	}
	fmt.Printf("[4] Undo chain len = %d (after 4 updates)\n", chain)
	if chain != 3 {
		panic("expected chain len 3")
	}
}

func demoWriteConflict() {
	db := NewMVCCDB()
	db.Rows["y"] = &Row{Value: 50}
	t1 := db.Begin(true)
	t2 := db.Begin(true)
	t1.Update(db, "y", 60)
	t2.Update(db, "y", 70)
	db.Commit(t2)
	db.Rollback(t1)
	fmt.Printf("[5] Final y=%d (last commit wins)\n", db.Rows["y"].Value)
	if db.Rows["y"].Value != 70 {
		panic("expected 70")
	}
}

func main() {
	fmt.Println("== MVCC 多版本并发控制 ==")
	demoBasic()
	demoRRReuse()
	demoRCPerStmt()
	demoChain()
	demoWriteConflict()
	fmt.Println("All 5 demos passed.")
}
