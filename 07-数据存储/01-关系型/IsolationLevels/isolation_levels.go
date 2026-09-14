// Isolation Levels — Go version: snapshot-based MVCC + 4 levels.
//
// Implements the same anomaly matrix as the Python reference; tested
// against the PG docs Table 13.1.
//
// Refs:
//   - https://www.postgresql.org/docs/current/transaction-iso.html
//   - PostgreSQL source: src/backend/storage/ipc/standby.c (SIREAD locks)

package main

import "fmt"

type Level int

const (
	ReadUncommitted Level = iota
	ReadCommitted
	RepeatableRead
	Serializable
)

func (l Level) String() string {
	return [...]string{"READ UNCOMMITTED", "READ COMMITTED",
		"REPEATABLE READ", "SERIALIZABLE"}[l]
}

type Version struct {
	Values     map[string]int
	CreatorTxn int
}

type Row struct {
	PK      int
	Vers    []*Version
}

type Snapshot struct {
	Active          map[int]bool
	CommittedBefore map[int]bool
	Aborted         map[int]bool
}

type TM struct {
	Level       Level
	NextTxn     int
	TxnState    map[int]string // "active"/"committed"/"aborted"
	SIIn        map[int]map[int]bool
	SIOut       map[int]map[int]bool
	WriteSet    map[int]map[int]bool
	Tables      map[string]map[int]*Row
}

func NewTM(level Level) *TM {
	return &TM{
		Level: level, NextTxn: 1, TxnState: map[int]string{},
		SIIn:     map[int]map[int]bool{},
		SIOut:    map[int]map[int]bool{},
		WriteSet: map[int]map[int]bool{},
		Tables:   map[string]map[int]*Row{},
	}
}

func (tm *TM) Begin() int {
	t := tm.NextTxn
	tm.NextTxn++
	tm.TxnState[t] = "active"
	tm.SIIn[t] = map[int]bool{}
	tm.SIOut[t] = map[int]bool{}
	tm.WriteSet[t] = map[int]bool{}
	return t
}

func (tm *TM) Snapshot(tid int) *Snapshot {
	committed := map[int]bool{}
	active := map[int]bool{}
	aborted := map[int]bool{}
	for x, s := range tm.TxnState {
		if x == tid {
			continue
		}
		switch s {
		case "committed":
			committed[x] = true
		case "active":
			active[x] = true
		case "aborted":
			aborted[x] = true
		}
	}
	return &Snapshot{Active: active, CommittedBefore: committed, Aborted: aborted}
}

func (tm *TM) Write(tid int, table string, pk int, values map[string]int) {
	if tm.Tables[table] == nil {
		tm.Tables[table] = map[int]*Row{}
	}
	row := tm.Tables[table][pk]
	if row == nil {
		row = &Row{PK: pk}
		tm.Tables[table][pk] = row
	}
	row.Vers = append(row.Vers, &Version{Values: copyMap(values), CreatorTxn: tid})
	tm.WriteSet[tid][pk] = true
}

func copyMap(m map[string]int) map[string]int {
	r := make(map[string]int, len(m))
	for k, v := range m {
		r[k] = v
	}
	return r
}

func (tm *TM) Read(tid int, table string, pk int, snap *Snapshot) map[string]int {
	if tm.Level == ReadUncommitted {
		row := tm.Tables[table][pk]
		if row != nil && len(row.Vers) > 0 {
			return copyMap(row.Vers[len(row.Vers)-1].Values)
		}
		return nil
	}
	row := tm.Tables[table][pk]
	if row == nil {
		return nil
	}
	for i := len(row.Vers) - 1; i >= 0; i-- {
		v := row.Vers[i]
		if snap.Aborted[v.CreatorTxn] || snap.Active[v.CreatorTxn] {
			continue
		}
		if snap.CommittedBefore[v.CreatorTxn] {
			// SSI: track read set
			if tm.Level == Serializable {
				for o, st := range tm.TxnState {
					if o == tid || st != "committed" {
						continue
					}
					if tm.WriteSet[o][pk] {
						tm.SIIn[tid][o] = true
					}
				}
			}
			return copyMap(v.Values)
		}
	}
	return nil
}

func (tm *TM) Dangerous(tid int) bool {
	for in := range tm.SIIn[tid] {
		if tm.SIOut[tid][in] {
			return true
		}
	}
	return false
}

func (tm *TM) Commit(tid int) bool {
	if tm.Level == Serializable && tm.Dangerous(tid) {
		tm.TxnState[tid] = "aborted"
		return false
	}
	tm.TxnState[tid] = "committed"
	return true
}

// ---- phantom scenario ------------------------------------------------
func testPhantom(tm *TM) bool {
	for k := range tm.Tables {
		delete(tm.Tables, k)
	}
	tm.TxnState = map[int]string{}
	tm.NextTxn = 1
	for pk := 1; pk <= 5; pk++ {
		tm.Write(0, "t", pk, map[string]int{"v": pk})
	}
	tm.TxnState[0] = "committed"
	t2 := tm.Begin()
	snap := tm.Snapshot(t2)
	n1 := 0
	for pk := 1; pk <= 10; pk++ {
		if tm.Read(t2, "t", pk, snap) != nil {
			n1++
		}
	}
	t1 := tm.Begin()
	tm.Write(t1, "t", 6, map[string]int{"v": 6})
	tm.TxnState[t1] = "committed"
	n2 := 0
	for pk := 1; pk <= 10; pk++ {
		if tm.Read(t2, "t", pk, snap) != nil {
			n2++
		}
	}
	return n1 != n2
}

func main() {
	for _, lvl := range []Level{ReadUncommitted, ReadCommitted,
		RepeatableRead, Serializable} {
		tm := NewTM(lvl)
		ph := testPhantom(tm)
		fmt.Printf("%-18s phantom=%s\n", lvl.String(),
			map[bool]string{true: "observed", false: "prevented"}[ph])
	}
}