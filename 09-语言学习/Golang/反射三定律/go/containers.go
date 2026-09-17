package main

// 模型里的容器：类型描述、值描述、内存 Store。
// 与 value_model.go（reflect.Value 的 flag 语义）分开，避免单文件超 300 行。

import "unicode"

// ---------------------------------------------------------------- 容器
type Field struct {
	Name     string
	Kind     Kind
	Typ      string
	Struct   *StructType
	Embedded bool
}

// Exported 对应 field.Name.IsExported()：首字符为大写字母。
func (f Field) Exported() bool {
	r := []rune(f.Name)
	return len(r) > 0 && unicode.IsUpper(r[0])
}

type StructType struct {
	Name   string
	Fields []Field
}

type levelItem struct {
	idx []int
	f   *Field
}

// FieldByName 返回字段索引路径；找不到或同深度存在歧义时第二个返回值为 false。
func (t *StructType) FieldByName(name string) ([]int, bool) {
	var level []levelItem
	for i := range t.Fields {
		level = append(level, levelItem{[]int{i}, &t.Fields[i]})
	}
	for depth := 0; depth < 8; depth++ {
		var hits []levelItem
		for _, it := range level {
			if it.f.Name == name {
				hits = append(hits, it)
			}
		}
		if len(hits) == 1 {
			return hits[0].idx, true
		}
		if len(hits) > 1 {
			return nil, false // 歧义
		}
		var next []levelItem
		for _, it := range level {
			if it.f.Embedded && it.f.Struct != nil {
				for j := range it.f.Struct.Fields {
					idx := append(append([]int{}, it.idx...), j)
					next = append(next, levelItem{idx, &it.f.Struct.Fields[j]})
				}
			}
		}
		if len(next) == 0 {
			break
		}
		level = next
	}
	return nil, false
}

// Goval 是一个 Go 值的三要素：Kind、类型名、数据。
type Goval struct {
	Kind   Kind
	Typ    string
	Value  interface{}
	Struct *StructType
}

func (g *Goval) Copy() *Goval {
	c := *g
	return &c
}

// Ptr 是模型里的指针：指向某个 slot，并记住被指对象的 Kind/类型。
type Ptr struct {
	Target  int
	Pointee *Goval
}

// StructObj 是模型里的结构体实例：一份字段 slot 列表。
type StructObj struct {
	Slots []int
}

// Store 模拟可取地址的存储：slot id → 数据。
type Store struct {
	slots map[int]interface{}
	next  int
}

func NewStore() *Store {
	return &Store{slots: map[int]interface{}{}, next: 1}
}

func (s *Store) Alloc(v interface{}) int {
	id := s.next
	s.next++
	s.slots[id] = v
	return id
}

func (s *Store) Load(slot int) interface{}   { return s.slots[slot] }
func (s *Store) Put(slot int, v interface{}) { s.slots[slot] = v }
