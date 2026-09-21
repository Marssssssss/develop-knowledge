// Package upsert —— 批量写入与 Upsert（PG ON CONFLICT / SQLite UPSERT 转写）。
//
// 与 Python 版的**显式语言差异**：
//   * Python 的谓词是闭包，这里改成 func(map[string]any) bool 显式传入；
//   * Python 抛异常，这里用 (value, error) 返回；
//   * 行标识用主键值的字符串拼接（Go 的 map 键不能是切片），
//     id(obj) 的语义用该字符串代替。
package main

import (
	"errors"
	"fmt"
	"sort"
	"strings"
)

type Row = map[string]any

var (
	ErrNoUniqueIndex      = errors.New("there is no unique or exclusion constraint matching the ON CONFLICT specification")
	ErrCardinality        = errors.New("ON CONFLICT DO UPDATE command cannot affect row a second time")
	ErrNoTarget           = errors.New("ON CONFLICT DO UPDATE requires a conflict_target")
	ErrPrivilege          = errors.New("insufficient privilege")
	ErrBadBatch           = errors.New("batch_size must be positive")
)

// Pred 是部分索引的 WHERE 谓词。
type Pred func(Row) bool

// Index 是表上的索引。
type Index struct {
	Name      string
	Cols      []string
	Unique    bool
	Predicate Pred
}

func (i *Index) matches(r Row) bool { return i.Predicate == nil || i.Predicate(r) }

func (i *Index) value(r Row) string {
	parts := make([]string, 0, len(i.Cols))
	for _, c := range i.Cols {
		parts = append(parts, fmt.Sprint(r[c]))
	}
	return strings.Join(parts, "\x00")
}

// Table 是一张表。
type Table struct {
	Name  string
	PK    []string
	Rows  map[string]Row
	Idxs  []*Index
}

func NewTable(name string, pk []string) *Table {
	return &Table{Name: name, PK: pk, Rows: map[string]Row{}}
}

func (t *Table) AddIndex(i *Index) { t.Idxs = append(t.Idxs, i) }

func (t *Table) pkOf(r Row) string {
	parts := make([]string, 0, len(t.PK))
	for _, c := range t.PK {
		parts = append(parts, fmt.Sprint(r[c]))
	}
	return strings.Join(parts, "\x00")
}

// InferArbiters 官方：不考虑顺序，列集合完全相同的唯一索引都被推断为 arbiter。
func (t *Table) InferArbiters(target []string) ([]*Index, error) {
	want := map[string]bool{}
	for _, c := range target {
		want[c] = true
	}
	var out []*Index
	for _, idx := range t.Idxs {
		if !idx.Unique {
			continue
		}
		if len(idx.Cols) != len(want) {
			continue
		}
		same := true
		for _, c := range idx.Cols {
			if !want[c] {
				same = false
				break
			}
		}
		if same {
			out = append(out, idx)
		}
	}
	if len(out) == 0 {
		return nil, ErrNoUniqueIndex
	}
	return out, nil
}

func (t *Table) findConflict(r Row, arbiters []*Index) (Row, string, bool) {
	for _, idx := range arbiters {
		if !idx.matches(r) {
			continue
		}
		v := idx.value(r)
		for key, existing := range t.Rows {
			if idx.matches(existing) && idx.value(existing) == v {
				return existing, key, true
			}
		}
	}
	return nil, "", false
}

// WhereFn 是 DO UPDATE 末尾的 WHERE：参数是 (已存在行, 拟插入行 excluded)。
type WhereFn func(existing, proposed Row) bool

// Insert 是一条 INSERT 语句。
type Insert struct {
	Table          *Table
	Values         []Row
	OnConflict     bool
	ConflictTarget []string
	ConstraintName string
	Action         string // "nothing" | "update"
	SetClause      map[string]string
	Where          WhereFn
	Returning      bool
	Priv           map[string]bool
}

// Result 是一次执行的结果。
type Result struct {
	Inserted, Updated, Skipped, LockedNotUpdated, Returning int
}

func evalExpr(expr string, existing, proposed Row) any {
	switch {
	case strings.HasPrefix(expr, "excluded."):
		return proposed[strings.TrimPrefix(expr, "excluded.")]
	case strings.HasPrefix(expr, "self."):
		return existing[strings.TrimPrefix(expr, "self.")]
	}
	return expr
}

// Run 执行这条 INSERT。
func (ins *Insert) Run() (Result, error) {
	var res Result
	t := ins.Table
	priv := ins.Priv
	if priv == nil {
		priv = map[string]bool{"INSERT": true, "UPDATE": true, "SELECT": true}
	}
	if !priv["INSERT"] {
		return res, ErrPrivilege
	}
	if ins.Action == "update" && !priv["UPDATE"] {
		return res, ErrPrivilege
	}
	if ins.OnConflict && !priv["SELECT"] {
		return res, ErrPrivilege
	}

	var arbiters []*Index
	if ins.OnConflict {
		switch {
		case ins.ConstraintName != "":
			for _, i := range t.Idxs {
				if i.Name == ins.ConstraintName && i.Unique {
					arbiters = append(arbiters, i)
				}
			}
			if len(arbiters) == 0 {
				return res, ErrNoUniqueIndex
			}
		case len(ins.ConflictTarget) > 0:
			var err error
			arbiters, err = t.InferArbiters(ins.ConflictTarget)
			if err != nil {
				return res, err
			}
		case ins.Action == "update":
			return res, ErrNoTarget
		default:
			for _, i := range t.Idxs {
				if i.Unique {
					arbiters = append(arbiters, i)
				}
			}
		}
	}

	affected := map[string]bool{}
	for _, proposed := range ins.Values {
		proposed = copyRow(proposed)
		var conflicting Row
		var ckey string
		if ins.OnConflict {
			var found bool
			conflicting, ckey, found = t.findConflict(proposed, arbiters)
			if !found {
				t.Rows[t.pkOf(proposed)] = proposed
				res.Inserted++
				if ins.Returning {
					res.Returning++
				}
				continue
			}
			if ins.Action == "nothing" {
				res.Skipped++
				continue
			}
			if affected[ckey] {
				return res, ErrCardinality
			}
			affected[ckey] = true
			if ins.Where != nil && !ins.Where(conflicting, proposed) {
				res.LockedNotUpdated++ // 官方：不更新，但仍被锁
				continue
			}
			for col, expr := range ins.SetClause {
				conflicting[col] = evalExpr(expr, conflicting, proposed)
			}
			res.Updated++
			if ins.Returning {
				res.Returning++
			}
			continue
		}
		t.Rows[t.pkOf(proposed)] = proposed
		res.Inserted++
		if ins.Returning {
			res.Returning++
		}
	}
	return res, nil
}

func copyRow(r Row) Row {
	out := Row{}
	for k, v := range r {
		out[k] = v
	}
	return out
}

