package main

import (
	"sort"
	"strings"
)

// SQLiteTable 是 SQLite UPSERT 的简化模型：conflict target 可选。
type SQLiteTable struct {
	Name    string
	Uniques [][]string
	Rows    []Row
}

func (s *SQLiteTable) conflict(r Row, target []string) (Row, bool) {
	cands := s.Uniques
	if len(target) > 0 {
		cands = [][]string{target}
	}
	for _, u := range cands {
		for _, row := range s.Rows {
			hit := true
			for _, c := range u {
				if row[c] != r[c] {
					hit = false
					break
				}
			}
			if hit {
				return row, true
			}
		}
	}
	return nil, false
}

// Upsert 返回 (插入行数, 更新行数)。
func (s *SQLiteTable) Upsert(rows []Row, target []string, action string,
	setClause map[string]string, where WhereFn) (int, int) {
	ins, upd := 0, 0
	for _, r := range rows {
		r = copyRow(r)
		c, found := s.conflict(r, target)
		if !found {
			s.Rows = append(s.Rows, r)
			ins++
			continue
		}
		if action == "nothing" {
			continue
		}
		if where != nil && !where(c, r) {
			continue
		}
		for col, expr := range setClause {
			c[col] = evalExpr(expr, c, r)
		}
		upd++
	}
	return ins, upd
}

// ReplaceInto 是「先删后插」，会触发 DELETE 触发器与外键的 ON DELETE 动作。
func (s *SQLiteTable) ReplaceInto(r Row, target []string) string {
	var events []string
	if c, found := s.conflict(r, target); found {
		for i, row := range s.Rows {
			if &row == &c || sameRow(row, c) {
				s.Rows = append(s.Rows[:i], s.Rows[i+1:]...)
				break
			}
		}
		events = append(events, "DELETE")
	}
	s.Rows = append(s.Rows, copyRow(r))
	events = append(events, "INSERT")
	return strings.Join(events, "+")
}

func sameRow(a, b Row) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

// NeedsWhereTrue 判断 INSERT ... SELECT 后紧跟 ON CONFLICT 是否有解析歧义。
func NeedsWhereTrue(sql string) bool {
	u := strings.ToUpper(strings.Join(strings.Fields(sql), " "))
	if !strings.Contains(u, " ON CONFLICT") || !strings.Contains(u, " SELECT ") {
		return false
	}
	_, tail, _ := strings.Cut(u, " SELECT ")
	head, _, _ := strings.Cut(tail, " ON CONFLICT")
	return !strings.Contains(head, " WHERE ")
}

// BatchStatementCount 把 n 行按 batch_size 合成多值 INSERT 后的语句数。
func BatchStatementCount(n, batchSize int) (int, error) {
	if batchSize <= 0 {
		return 0, ErrBadBatch
	}
	return (n + batchSize - 1) / batchSize, nil
}

func sortedKeys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
