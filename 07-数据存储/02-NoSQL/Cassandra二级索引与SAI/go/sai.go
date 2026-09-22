// Package main：Cassandra SAI 的两层索引结构与查询路径（口径见 README）。
package main

import "fmt"

// PrimaryKey 主键：分区键 + token（+ 可选 clustering）。
type PrimaryKey struct {
	PK         string
	Token      int
	Clustering string
}

func (k PrimaryKey) hasClustering() bool { return k.Clustering != "" }

func (k PrimaryKey) String() string {
	if k.Clustering != "" {
		return fmt.Sprintf("PK(%s, token=%d, ck=%s)", k.PK, k.Token, k.Clustering)
	}
	return fmt.Sprintf("PK(%s, token=%d)", k.PK, k.Token)
}

// RowMapping PrimaryKey -> rowID，按 flush 顺序编号；只在 FLUSH 时建立。
type RowMapping struct {
	Keys  []PrimaryKey
	Index map[PrimaryKey]int
}

func newRowMapping(opType string) *RowMapping {
	if opType != "FLUSH" {
		return nil // 源码里返回 DUMMY，所有操作都是空实现
	}
	return &RowMapping{Index: map[PrimaryKey]int{}}
}

func (m *RowMapping) Add(k PrimaryKey) int {
	id := len(m.Keys)
	m.Keys = append(m.Keys, k)
	m.Index[k] = id
	return id
}

func (m *RowMapping) Get(k PrimaryKey) int {
	if v, ok := m.Index[k]; ok {
		return v
	}
	return -1
}

func (m *RowMapping) RowIDFor(k PrimaryKey) int { return m.Index[k] }

func (m *RowMapping) KeyFor(rowID int) PrimaryKey { return m.Keys[rowID] }

// MemtableIndex memtable 侧的列索引：term -> PrimaryKey 集合。
type MemtableIndex struct {
	Column string
	Terms  map[string][]PrimaryKey
}

func NewMemtableIndex(col string) *MemtableIndex {
	return &MemtableIndex{Column: col, Terms: map[string][]PrimaryKey{}}
}

func (m *MemtableIndex) Add(term string, k PrimaryKey) {
	for _, e := range m.Terms[term] {
		if e == k {
			return // 同一个 key 重复写只在 postings 里出现一次
		}
	}
	m.Terms[term] = append(m.Terms[term], k)
}

// SSTableIndex 一个 SSTable 上一个列的索引：term -> 局部 rowID 列表。
type SSTableIndex struct {
	Name        string
	RowMapping  *RowMapping
	Postings    map[string][]int
}

func BuildSSTableIndex(name string, mem *MemtableIndex, order []PrimaryKey) *SSTableIndex {
	rm := newRowMapping("FLUSH")
	for _, k := range order {
		rm.Add(k)
	}
	idx := &SSTableIndex{Name: name, RowMapping: rm, Postings: map[string][]int{}}
	for term, keys := range mem.Terms {
		for _, k := range keys {
			if id := rm.Get(k); id >= 0 {
				idx.Postings[term] = append(idx.Postings[term], id)
			}
		}
	}
	return idx
}

func (s *SSTableIndex) Search(term string) []int { return s.Postings[term] }

// Search 全局查询：postings 是局部 rowID，必须带 SSTable 一起映射回主键，再按 token 排序。
func Search(indexes []*SSTableIndex, term string) []PrimaryKey {
	var hits []PrimaryKey
	for _, idx := range indexes {
		for _, rowID := range idx.Search(term) {
			hits = append(hits, idx.RowMapping.KeyFor(rowID))
		}
	}
	for i := 1; i < len(hits); i++ { // 按 token 插入排序
		for j := i; j > 0 && hits[j].Token < hits[j-1].Token; j-- {
			hits[j], hits[j-1] = hits[j-1], hits[j]
		}
	}
	return hits
}

// NaiveSearch 错误实现：把 rowID 当全局的，全拿去第一个 SSTable 的映射里解析。
func NaiveSearch(indexes []*SSTableIndex, term string) []PrimaryKey {
	ids := map[int]bool{}
	for _, idx := range indexes {
		for _, id := range idx.Search(term) {
			ids[id] = true
		}
	}
	first := indexes[0]
	var out []PrimaryKey
	for i := 0; i < len(first.RowMapping.Keys); i++ {
		if ids[i] {
			out = append(out, first.RowMapping.KeyFor(i))
		}
	}
	return out
}

func onDiskStructure(v interface{}) string {
	switch v.(type) {
	case string:
		return "byte-ordered trie"
	case bool:
		return "postings only"
	default:
		return "block-oriented balanced tree"
	}
}

func indexComponents() map[string][]string {
	return map[string][]string{
		"per_sstable": {"GROUP_COMPLETION_MARKER", "PrimaryKeyMap", "token/offset (只存一份)"},
		"per_column":  {"COLUMN_COMPLETION_MARKER", "trie / bbtree", "postings"},
	}
}
