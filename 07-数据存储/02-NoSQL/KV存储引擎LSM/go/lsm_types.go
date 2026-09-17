// lsm_types.go — 基础数据结构:Entry / MemTable / SSTable(与 lsm_engine.go 同属 package main)
//
// 拆文件只为满足单文件 ≤300 行的约束;Go 同包共享符号,零语义改动。
// 算法出处见 lsm_engine.go 文件头。
package main

import "sort"

// Entry 带序号的一个版本。序号越大越新(与 LevelDB 的 sequence number 同义)。
type Entry struct {
	Key   string
	Seq   int
	Kind  int
	Value string
}

// SizeBytes 近似字节数:8 字节内部 key 前缀 + 变长 key/value。
func (e Entry) SizeBytes() int { return 8 + len(e.Key) + len(e.Value) }

// IsDelete 是否为删除标记。
func (e Entry) IsDelete() bool { return e.Kind == DeleteKind }

// NewestFirst 按 (key 升序, seq 降序) 归一化 —— memtable / SSTable 的物理顺序。
func NewestFirst(entries []Entry) []Entry {
	out := append([]Entry{}, entries...)
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Key != out[j].Key {
			return out[i].Key < out[j].Key
		}
		return out[i].Seq > out[j].Seq
	})
	return out
}

// Visible 取该 key 序号最大的版本。
func Visible(entries []Entry, key string) (Entry, bool) {
	found := false
	var best Entry
	for _, e := range entries {
		if e.Key != key {
			continue
		}
		if !found || e.Seq > best.Seq {
			best = e
			found = true
		}
	}
	return best, found
}

// MemTable 内存中的有序写缓冲。满了就冻结为不可变并触发 flush。
type MemTable struct {
	WriteBufferSize int
	Entries         []Entry
}

// Add 追加一个版本。
func (m *MemTable) Add(e Entry) { m.Entries = append(m.Entries, e) }

// SizeBytes 近似占用。
func (m *MemTable) SizeBytes() int {
	t := 0
	for _, e := range m.Entries {
		t += e.SizeBytes()
	}
	return t
}

// IsFull 是否达到 write_buffer_size。
func (m *MemTable) IsFull() bool { return m.SizeBytes() >= m.WriteBufferSize }

// Get 取最新可见版本。
func (m *MemTable) Get(key string) (Entry, bool) { return Visible(m.Entries, key) }

// Drain 清空并返回归一化后的版本序列。
func (m *MemTable) Drain() []Entry {
	out := NewestFirst(m.Entries)
	m.Entries = nil
	return out
}

// SSTable 不可变有序文件。
type SSTable struct {
	Number  int
	Level   int
	Entries []Entry
}

// NewSSTable 建表时做一次归一化。
func NewSSTable(number, level int, entries []Entry) *SSTable {
	return &SSTable{Number: number, Level: level, Entries: NewestFirst(entries)}
}

// SizeBytes 近似占用。
func (s *SSTable) SizeBytes() int {
	t := 0
	for _, e := range s.Entries {
		t += e.SizeBytes()
	}
	return t
}

// Get 取最新可见版本。
func (s *SSTable) Get(key string) (Entry, bool) { return Visible(s.Entries, key) }

// Smallest 最小 key。
func (s *SSTable) Smallest() string {
	if len(s.Entries) == 0 {
		return ""
	}
	return s.Entries[0].Key
}

// Largest 最大 key。
func (s *SSTable) Largest() string {
	if len(s.Entries) == 0 {
		return ""
	}
	return s.Entries[len(s.Entries)-1].Key
}

// Overlaps [lo, hi] 与本文件闭区间 key 范围是否相交。
func (s *SSTable) Overlaps(lo, hi string) bool {
	if len(s.Entries) == 0 {
		return false
	}
	return !(hi < s.Smallest() || lo > s.Largest())
}

