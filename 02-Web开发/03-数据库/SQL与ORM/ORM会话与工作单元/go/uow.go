// Package uow —— ORM 会话与工作单元（SQLAlchemy 2.0 官方源码转写）。
//
// 与 Python 版的**显式语言差异**（Go 没有弱引用，也没有弱键字典）：
//   * IdentityMap 用 map[Key]*State 强持有，对象不会被 GC，因此
//     「对象被回收后键视为不存在」这条性质在 Go 版里用 Alive 字段手工模拟；
//   * Python 的 vars(obj) 反射脏检查，这里改成显式 Attrs() 快照；
//   * Python 的异常（InvalidRequestError）改成 (bool, error) 返回。
package main

import (
	"fmt"
	"sort"
	"strings"
)

// Key 是身份键：实体名 + 主键元组拼成的字符串。
type Key string

// Obj 是被映射的实体。
type Obj struct {
	Kind   string
	ID     int
	Fields map[string]string
	Alive  bool // 模拟 Python 版的弱引用存活性
}
// Table 带外键父表，用于拓扑排序（官方 mapper._sorted_tables）。
type Table struct {
	Name   string
	Parent string
}

// Session 是工作单元。
type Session struct {
	Tables     []Table
	Identity   *IdentityMap
	New        []*State
	Deleted    []*State
	AutoFlush  bool
	SQL        []string
	Rows       map[string]map[int]*Obj
	inFlush    bool
	states     map[*Obj]*State
	sortedOnce []Table
}

func NewSession(tables []Table, autoflush bool) *Session {
	return &Session{
		Tables:    tables,
		Identity:  newIdentityMap(),
		AutoFlush: autoflush,
		Rows:      map[string]map[int]*Obj{},
		states:    map[*Obj]*State{},
	}
}

// SortedTables 按外键依赖排成拓扑序（父表在前）。
func (s *Session) SortedTables() []Table {
	if s.sortedOnce != nil {
		return s.sortedOnce
	}
	depth := map[string]int{}
	byName := map[string]Table{}
	for _, t := range s.Tables {
		byName[t.Name] = t
	}
	var d func(name string) int
	d = func(name string) int {
		if v, ok := depth[name]; ok {
			return v
		}
		depth[name] = 0
		t := byName[name]
		if t.Parent != "" {
			depth[name] = d(t.Parent) + 1
		}
		return depth[name]
	}
	out := append([]Table{}, s.Tables...)
	for _, t := range s.Tables {
		d(t.Name)
	}
	sort.Slice(out, func(i, j int) bool { return depth[out[i].Name] < depth[out[j].Name] })
	s.sortedOnce = out
	return out
}

func (s *Session) state(o *Obj) *State {
	if st, ok := s.states[o]; ok {
		return st
	}
	st := newState(o)
	s.states[o] = st
	return st
}

func (s *Session) Add(o *Obj) {
	st := s.state(o)
	if st.Session == s {
		return
	}
	st.Session = s
	s.New = append(s.New, st)
}

func (s *Session) Delete(o *Obj) {
	st := s.state(o)
	if !st.HasIdentity {
		return
	}
	st.Deleted = true
	s.Deleted = append(s.Deleted, st)
}

func (s *Session) Get(kind string, id int) *Obj {
	st := s.Identity.Get(Key(fmt.Sprintf("%s|%d", kind, id)))
	if st == nil {
		return nil
	}
	return st.Obj
}

// QueryAll 模拟 SQL 查询：官方 FAQ 明确，查询不查身份映射。
func (s *Session) QueryAll(table string) []*Obj {
	s.autoFlush()
	var out []*Obj
	for _, o := range s.Rows[table] {
		out = append(out, o)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

func (s *Session) needsFlush() bool {
	if len(s.New) > 0 || len(s.Deleted) > 0 {
		return true
	}
	for _, st := range s.Identity.liveStates() {
		if st.isDirty() {
			return true
		}
	}
	return false
}

func (s *Session) autoFlush() {
	if s.AutoFlush && !s.inFlush && s.needsFlush() {
		s.Flush()
	}
}

func (s *Session) tableOf(st *State) string { return strings.ToLower(st.Obj.Kind) }

// Flush 的顺序照官方 persistence.py：
//  1. 保存：对每张表（拓扑序）先 UPDATE 后 INSERT；
//  2. 删除：按 SortedTables 的逆序（先子表后父表）。
func (s *Session) Flush() []string {
	s.inFlush = true
	s.emitSaves()
	s.emitDeletes()
	s.New = nil
	s.Deleted = nil
	s.inFlush = false
	return s.SQL
}

func (s *Session) emitSaves() {
	for _, t := range s.SortedTables() {
		for _, st := range s.Identity.liveStates() {
			if s.tableOf(st) != t.Name || st.Deleted || !st.isDirty() {
				continue
			}
			s.SQL = append(s.SQL, fmt.Sprintf("UPDATE %s SET %s WHERE id=%d",
				t.Name, strings.Join(st.dirtyAttrs(), ","), st.Obj.ID))
			for k, v := range st.attrs() {
				st.Committed[k] = v
			}
		}
		for _, st := range s.New {
			if s.tableOf(st) != t.Name {
				continue
			}
			st.Key = st.Obj.identityKey()
			if _, err := s.Identity.Add(st); err != nil {
				s.SQL = append(s.SQL, "ERROR "+err.Error())
				continue
			}
			st.HasIdentity = true
			for k, v := range st.attrs() {
				st.Committed[k] = v
			}
			var cols []string
			for k := range st.attrs() {
				cols = append(cols, k)
			}
			sort.Strings(cols)
			s.SQL = append(s.SQL, fmt.Sprintf("INSERT INTO %s (%s)", t.Name, strings.Join(cols, ",")))
			if s.Rows[t.Name] == nil {
				s.Rows[t.Name] = map[int]*Obj{}
			}
			s.Rows[t.Name][st.Obj.ID] = st.Obj
		}
	}
}

func (s *Session) emitDeletes() {
	ordered := s.SortedTables()
	for i := len(ordered) - 1; i >= 0; i-- {
		t := ordered[i]
		for _, st := range s.Identity.liveStates() {
			if !st.Deleted || s.tableOf(st) != t.Name {
				continue
			}
			s.SQL = append(s.SQL, fmt.Sprintf("DELETE FROM %s WHERE id=%d", t.Name, st.Obj.ID))
		}
	}
}

// Commit 内部无条件 flush，之后把已删除对象转 detached。
func (s *Session) Commit() []string {
	s.Flush()
	for _, st := range s.Identity.liveStates() {
		if st.Deleted {
			st.Deleted = false
			st.HasIdentity = false
			st.Session = nil
			delete(s.Identity.dict, st.Key)
		}
	}
	return s.SQL
}

// Rollback 把属性恢复到已提交快照。
func (s *Session) Rollback() {
	for _, st := range s.Identity.liveStates() {
		st.Deleted = false
		for k, v := range st.Committed {
			st.Obj.Fields[k] = v
		}
	}
	s.New = nil
	s.Deleted = nil
}
