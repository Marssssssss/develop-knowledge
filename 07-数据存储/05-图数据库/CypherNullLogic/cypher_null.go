// Package main 实现 Cypher null 与三值逻辑的最小模型。
//
// 依据 Neo4j Cypher Manual → Values and types → Working with null（实读）：
//   - null 表示缺失或未知值；所有数据类型都可为空，故**类型谓词对 null 恒为
//     true**。
//   - 读取不存在的属性得到 null；大多数以 null 为输入的表达式产出 null。
//   - WHERE 里任何不是 true 的结果都被解释为 false。
//   - null 不等于 null：null = null 得到 null 而不是 true。
//   - AND / OR / XOR / NOT 把 null 当作三值逻辑的未知值。
//   - IN：能确定存在则 true；列表含 null 且无匹配元素则 null；否则 false。
//   - [] 取值与区间任一端为 null 则结果为 null。
//   - ORDER BY：升序 null 最后，降序 null 最前。
package main

import "math"

// Tri 表示三值逻辑值：nil 即 Cypher 的 null。
type Tri = *bool

// B 把 Go 的 bool 提升为 Tri。
func B(v bool) Tri { return &v }

// IPtr 把 int 提升为可空的 int。
func IPtr(v int) *int { return &v }

// SPtr 把 string 提升为可空的 string。
func SPtr(v string) *string { return &v }

// TruthTable 是官方真值表原始 9 行：(a, b, a AND b, a OR b, a XOR b, NOT a)。
var TruthTable = [][6]Tri{
	{B(false), B(false), B(false), B(false), B(false), B(true)},
	{B(false), nil, B(false), nil, nil, B(true)},
	{B(false), B(true), B(false), B(true), B(true), B(true)},
	{B(true), B(false), B(false), B(true), B(true), B(false)},
	{B(true), nil, nil, B(true), nil, B(false)},
	{B(true), B(true), B(true), B(true), B(false), B(false)},
	{nil, B(false), B(false), nil, nil, nil},
	{nil, nil, nil, nil, nil, nil},
	{nil, B(true), nil, B(true), nil, nil},
}

// And 三值 AND：false 吸收，其余含 null 则为 null。
func And(a, b Tri) Tri {
	if a != nil && !*a {
		return B(false)
	}
	if b != nil && !*b {
		return B(false)
	}
	if a == nil || b == nil {
		return nil
	}
	return B(true)
}

// Or 三值 OR：true 吸收，其余含 null 则为 null。
func Or(a, b Tri) Tri {
	if a != nil && *a {
		return B(true)
	}
	if b != nil && *b {
		return B(true)
	}
	if a == nil || b == nil {
		return nil
	}
	return B(false)
}

// Not 三值 NOT：null 取反仍是 null。
func Not(a Tri) Tri {
	if a == nil {
		return nil
	}
	return B(!*a)
}

// Xor 三值 XOR：任一侧为 null 则为 null（XOR 没有吸收律）。
func Xor(a, b Tri) Tri {
	if a == nil || b == nil {
		return nil
	}
	return B(*a != *b)
}

// EqInt 实现 =：任一侧为 null 得到 null（故 null = null 是 null）。
func EqInt(a, b *int) Tri {
	if a == nil || b == nil {
		return nil
	}
	return B(*a == *b)
}

// NeInt 实现 <>：同样任一侧为 null 得到 null。
func NeInt(a, b *int) Tri {
	if a == nil || b == nil {
		return nil
	}
	return B(*a != *b)
}

// Where 是 WHERE 的判定：只有 true 通过，false 与 null 都当 false。
func Where(p Tri) bool { return p != nil && *p }

// In 实现 Cypher 的 IN 运算符。
func In(x *int, list []*int) Tri {
	hit := false
	for _, v := range list {
		r := EqInt(x, v)
		if r != nil && *r {
			return B(true)
		}
		if r == nil {
			hit = true
		}
	}
	if hit {
		return nil
	}
	return B(false)
}

// Get 按下标取值：下标为 null 或越界得到 null。
func Get(list []int, idx *int) *int {
	if idx == nil || *idx < 0 || *idx >= len(list) {
		return nil
	}
	return IPtr(list[*idx])
}

// Slice 区间切片：任一端为 null 则整个结果为 null。
func Slice(s []int, lo, hi *int) []int {
	if lo == nil || hi == nil {
		return nil
	}
	return s[*lo:*hi]
}

// All 是 all()：AND 折叠。
func All(list []Tri) Tri {
	acc := B(true)
	for _, v := range list {
		acc = And(acc, v)
	}
	return acc
}

// Any 是 any()：OR 折叠。
func Any(list []Tri) Tri {
	acc := B(false)
	for _, v := range list {
		acc = Or(acc, v)
	}
	return acc
}

// None 是 none()：NOT any()。
func None(list []Tri) Tri { return Not(Any(list)) }

// Single 是 single()：能确定「恰好一个 true」才给 true/false，否则 null。
//
// 口径：官方只给出「all/any/none/single 遵循同样规则」的总纲，没有逐函数表格。
func Single(list []Tri) Tri {
	nTrue, hasNull := 0, false
	for _, v := range list {
		if v == nil {
			hasNull = true
		} else if *v {
			nTrue++
		}
	}
	if nTrue >= 2 {
		return B(false)
	}
	if nTrue == 1 {
		if hasNull {
			return nil
		}
		return B(true)
	}
	if hasNull {
		return nil
	}
	return B(false)
}

// Coalesce 返回第一个非 null 的参数。
func Coalesce(args ...*int) *int {
	for _, a := range args {
		if a != nil {
			return a
		}
	}
	return nil
}

// TypePredicate 实现 IS :: T：对 null 恒为 true，对非 null 才做真正的类型判定。
func TypePredicate(x interface{}, typename string) bool {
	if x == nil {
		return true
	}
	switch typename {
	case "INTEGER":
		_, ok := x.(int)
		return ok
	case "STRING":
		_, ok := x.(string)
		return ok
	}
	return false
}

// Arith 是算术表达式：任一侧为 null 得 null。
func Arith(a, b *int) *int {
	if a == nil || b == nil {
		return nil
	}
	return IPtr(*a + *b)
}

// Lt 是 < 比较：任一侧为 null 得 null。
func Lt(a, b *int) Tri {
	if a == nil || b == nil {
		return nil
	}
	return B(*a < *b)
}

// Sin 是 sin(x)：参数为 null 得 null。
func Sin(x *float64) *float64 {
	if x == nil {
		return nil
	}
	v := math.Sin(*x)
	return &v
}

// OrderBy 实现 ORDER BY：升序 null 最后，降序 null 最前。
func OrderBy(rows []*string, desc bool) []*string {
	var nulls, rest []*string
	for _, r := range rows {
		if r == nil {
			nulls = append(nulls, r)
		} else {
			rest = append(rest, r)
		}
	}
	for i := 0; i < len(rest); i++ {
		for j := i + 1; j < len(rest); j++ {
			if (desc && *rest[i] < *rest[j]) || (!desc && *rest[i] > *rest[j]) {
				rest[i], rest[j] = rest[j], rest[i]
			}
		}
	}
	if desc {
		return append(nulls, rest...)
	}
	return append(rest, nulls...)
}

// Head 是 head(list)：空列表得 null。
func Head(list []int) *int {
	if len(list) == 0 {
		return nil
	}
	return IPtr(list[0])
}
