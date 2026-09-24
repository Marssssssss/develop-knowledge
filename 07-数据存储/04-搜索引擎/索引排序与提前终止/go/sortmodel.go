// Sort / SortField 语义与索引排序（Go 侧等价实现）。
//
// 转写自 apache/lucene@main
//   lucene/core/src/java/org/apache/lucene/search/SortField.java
//   lucene/core/src/java/org/apache/lucene/search/Sort.java
//   lucene/core/src/java/org/apache/lucene/index/IndexWriterConfig.java
//
// 语言差异（已在代码中显式落地）：
//   * Java 抛 IllegalArgumentException，Go 侧用 error 返回；
//   * Java 的 `new Sort()`（无参）与 `new Sort(new SortField[0])`（显式空数组）是两条路径，
//     Go 用 NewSort() 与 NewSortFromArray() 两个构造函数分别表达；
//   * Java 的 hashCode 是 32 位有符号溢出，Go 侧显式做符号还原。
//
// 建模口径：IndexSorter 的**实际排序行为**不还原，只关心「哪些 SortField 允许做索引排序」
// 这个可判定的开关，以及 getPrimarySortField 的跳过规则。
package main

import (
	"errors"
	"fmt"
	"sort"
	"strings"
)

// SortField.Type 的可取值
const (
	TypeScore      = "SCORE"
	TypeDoc        = "DOC"
	TypeString     = "STRING"
	TypeInt        = "INT"
	TypeLong       = "LONG"
	TypeDouble     = "DOUBLE"
	TypeFloat      = "FLOAT"
	TypeCustom     = "CUSTOM"
	TypeRewritable = "REWRITEABLE"
	TypeStringVal  = "STRING_VAL"
)

// IndexSorterTypes SortField#getIndexSorter 里返回实现的那几类，其余（含 default）返回 nil
var IndexSorterTypes = map[string]bool{
	TypeString: true, TypeInt: true, TypeLong: true, TypeDouble: true, TypeFloat: true,
}

// 缺失值的两个哨兵
const (
	StringFirst = "<STRING_FIRST>"
	StringLast  = "<STRING_LAST>"
)

// ------------------------------------------------------------------ SortField

// SortField 字段名 + 类型 + 是否倒序。
type SortField struct {
	Field        string
	Type         string
	Reverse      bool
	MissingValue interface{}
	RewritesTo   *SortField // 非 nil 时模拟 rewrite 后发生变化
}

// NewSortField 对应 SortField(String field, Type type)，含 validateField 的两条校验。
func NewSortField(field, typ string) (*SortField, error) {
	return newSortField(field, typ, false, nil)
}

func newSortField(field, typ string, reverse bool, mv interface{}) (*SortField, error) {
	if field == "" && typ != TypeScore && typ != TypeDoc {
		return nil, errors.New("field can only be null when type is SCORE or DOC")
	}
	if typ == TypeString && mv != nil && mv != StringFirst && mv != StringLast {
		return nil, errors.New(
			"For Type.STRING, missing value must be either STRING_FIRST or STRING_LAST")
	}
	return &SortField{field, typ, reverse, mv, nil}, nil
}

func (f *SortField) GetField() string { return f.Field }

// NeedsScores 只有 SCORE 需要算分。
func (f *SortField) NeedsScores() bool { return f.Type == TypeScore }

// GetIndexSorter SCORE / DOC / CUSTOM / REWRITEABLE / STRING_VAL 一律 false。
func (f *SortField) GetIndexSorter() bool { return IndexSorterTypes[f.Type] }

// Rewrite 返回改写后的字段；本 demo 用 RewritesTo 模拟「发生了变化」。
func (f *SortField) Rewrite() *SortField {
	if f.RewritesTo != nil {
		return f.RewritesTo
	}
	return f
}

func (f *SortField) String() string {
	var b strings.Builder
	b.WriteString("<" + strings.ToLower(f.Type))
	if f.Reverse {
		b.WriteString("!")
	}
	if f.Field != "" {
		fmt.Fprintf(&b, ": %q", f.Field)
	}
	b.WriteString(">")
	return b.String()
}

// FieldScore / FieldDoc 两个单例
var (
	FieldScore, _ = newSortField("", TypeScore, false, nil)
	FieldDoc, _   = newSortField("", TypeDoc, false, nil)
)

// ---------------------------------------------------------------------- Sort

// Sort 排序条件：第一个字段为主序，同分依次用后续字段，最后用 docid 兜底。
type Sort struct {
	Fields []*SortField
}

// NewSort 对应 Java 的无参构造：等价于 [FIELD_SCORE]。
func NewSort(fields ...*SortField) *Sort {
	if len(fields) == 0 {
		fields = []*SortField{FieldScore}
	}
	return &Sort{fields}
}

// NewSortFromArray 对应 Java 的 new Sort(SortField[])：显式传空数组会报错。
func NewSortFromArray(fields []*SortField) (*Sort, error) {
	if len(fields) == 0 {
		return nil, errors.New("There must be at least 1 sort field")
	}
	return &Sort{fields}, nil
}

// SortRelevance = new Sort()
var SortRelevance = NewSort()

// SortIndexOrder = new Sort(SortField.FIELD_DOC)
var SortIndexOrder = NewSort(FieldDoc)

func (s *Sort) GetSort() []*SortField { return s.Fields }

// NeedsScores 任一字段需要算分即需要。
func (s *Sort) NeedsScores() bool {
	for _, f := range s.Fields {
		if f.NeedsScores() {
			return true
		}
	}
	return false
}

// Rewrite 任一字段被改写就返回新的 Sort，否则返回自身。
func (s *Sort) Rewrite() *Sort {
	rewritten := make([]*SortField, len(s.Fields))
	changed := false
	for i, f := range s.Fields {
		rewritten[i] = f.Rewrite()
		if rewritten[i] != f {
			changed = true
		}
	}
	if !changed {
		return s
	}
	out, _ := NewSortFromArray(rewritten)
	return out
}

func (s *Sort) String() string {
	parts := make([]string, len(s.Fields))
	for i, f := range s.Fields {
		parts[i] = f.String()
	}
	return strings.Join(parts, ",")
}

// GetPrimarySortField 该段上真正生效的第一个排序字段；全被跳过则返回 nil。
//
// 跳过规则（源码注释逐条对应）：
//  1. sort == nil → nil
//  2. field == "" → 直接返回（自定义字段，无从判断，视为有效）
//  3. 该字段在本段没有值（fieldInfo == nil）→ 跳过
//  4. 有 DocValuesSkipper 且覆盖全部文档（docCount == maxDoc）且 min == max → 跳过
func GetPrimarySortField(reader *LeafReader) *SortField {
	s := reader.Meta.Sort
	if s == nil {
		return nil
	}
	for _, sf := range s.GetSort() {
		field := sf.GetField()
		if field == "" {
			return sf
		}
		if !reader.HasField(field) {
			continue
		}
		sk := reader.DocValuesSkipper(field)
		if sk != nil && sk.DocCount == reader.MaxDoc && sk.Min == sk.Max {
			continue
		}
		return sf
	}
	return nil
}

// ------------------------------------------------------- IndexWriterConfig

// IndexWriterConfig 只建模索引排序相关部分与默认值。
type IndexWriterConfig struct {
	IndexSort                      *Sort
	IndexSortFields                map[string]bool
	MaxFullFlushMergeWaitMillis    int64
	RamBufferSizeMB                float64
}

// IndexWriterConfig 默认值
const (
	DefaultMaxFullFlushMergeWaitMillis = 500
	DefaultRamBufferSizeMB             = 16.0
	DefaultMaxBufferedDocs             = -1 // DISABLE_AUTO_FLUSH
)

func NewIndexWriterConfig() *IndexWriterConfig {
	return &IndexWriterConfig{nil, nil, DefaultMaxFullFlushMergeWaitMillis,
		DefaultRamBufferSizeMB}
}

// SetIndexSort 每个字段都必须有 IndexSorter，否则报错（不会部分生效）。
func (c *IndexWriterConfig) SetIndexSort(s *Sort) (*IndexWriterConfig, error) {
	for _, sf := range s.GetSort() {
		if !sf.GetIndexSorter() {
			return nil, fmt.Errorf("Cannot sort index with sort field %s", sf)
		}
	}
	c.IndexSort = s
	c.IndexSortFields = map[string]bool{}
	for _, sf := range s.GetSort() {
		c.IndexSortFields[sf.GetField()] = true
	}
	return c, nil
}

// FullFlushMergeEnabled 文档：Set to 0 to disable merging on full flush.
func (c *IndexWriterConfig) FullFlushMergeEnabled() bool {
	return c.MaxFullFlushMergeWaitMillis != 0
}

// ------------------------------------------------------------------ LeafReader

// Skipper DocValuesSkipper 的替身。
type Skipper struct{ DocCount int; Min, Max int64 }

// LeafReader 够用的 LeafReader 替身。
type LeafReader struct {
	MaxDoc    int
	FieldInfo map[string]bool
	Meta      struct{ Sort *Sort }
	Skippers  map[string]*Skipper
}

// NewLeafReader 构造一个 LeafReader。
func NewLeafReader(maxDoc int, fields []string, s *Sort, sk map[string]*Skipper) *LeafReader {
	r := &LeafReader{MaxDoc: maxDoc, FieldInfo: map[string]bool{}, Skippers: sk}
	for _, f := range fields {
		r.FieldInfo[f] = true
	}
	r.Meta.Sort = s
	return r
}

// HasField 该字段在本段是否有值。
func (r *LeafReader) HasField(f string) bool { return r.FieldInfo[f] }

// DocValuesSkipper 返回该字段的 skipper，没有则 nil。
func (r *LeafReader) DocValuesSkipper(f string) *Skipper {
	if r.Skippers == nil {
		return nil
	}
	return r.Skippers[f]
}

// SortedFieldNames 便于打印。
func (c *IndexWriterConfig) SortedFieldNames() []string {
	out := []string{}
	for k := range c.IndexSortFields {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
