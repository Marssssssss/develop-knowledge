// CypherNullLogic 自检：官方真值表 9 行 × 4 运算符 + IN / [] / ORDER BY 等。
package main

import "fmt"

var okCount int
var failures []string

func ck(name string, cond bool, detail string) {
	if cond {
		okCount++
		return
	}
	failures = append(failures, fmt.Sprintf("%s  %s", name, detail))
}

// triEq 比较两个 Tri（nil 与 nil 相等）。
func triEq(a, b Tri) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return *a == *b
}

func eqTri(name string, got, want Tri) {
	ck(name, triEq(got, want), fmt.Sprintf("got=%v want=%v", got, want))
}

func eqInt(name string, got, want int) {
	ck(name, got == want, fmt.Sprintf("got=%d want=%d", got, want))
}

func main() {
	// ---------------------------------------------- 三值逻辑真值表（官方 9 行）
	for i, row := range TruthTable {
		tag := fmt.Sprintf("row%d", i)
		eqTri("AND "+tag, And(row[0], row[1]), row[2])
		eqTri("OR  "+tag, Or(row[0], row[1]), row[3])
		eqTri("XOR "+tag, Xor(row[0], row[1]), row[4])
		eqTri("NOT "+tag, Not(row[0]), row[5])
	}

	// ---------------------------------------------- null 不等于 null
	eqTri("null = null 得到 null", EqInt(nil, nil), nil)
	ck("null = null 不是 true", !Where(EqInt(nil, nil)), "")
	eqTri("null <> null 也是 null", NeInt(nil, nil), nil)
	eqTri("1 = 1", EqInt(IPtr(1), IPtr(1)), B(true))
	eqTri("1 = null", EqInt(IPtr(1), nil), nil)

	// ---------------------------------------------- WHERE 的二值化
	ck("WHERE true 通过", Where(B(true)), "")
	ck("WHERE false 过滤", !Where(B(false)), "")
	ck("WHERE null 过滤", !Where(nil), "")
	eqTri("2 IN [1,null,3] 结果为 null", In(IPtr(2), []*int{IPtr(1), nil,
		IPtr(3)}), nil)
	ck("WHERE 把该 null 当 false", !Where(In(IPtr(2), []*int{IPtr(1), nil,
		IPtr(3)})), "")

	// ---------------------------------------------- IN 的官方 8 例
	eqTri("2 IN [1,2,3]", In(IPtr(2), []*int{IPtr(1), IPtr(2), IPtr(3)}), B(true))
	eqTri("2 IN [1,null,3]", In(IPtr(2), []*int{IPtr(1), nil, IPtr(3)}), nil)
	eqTri("2 IN [1,2,null]", In(IPtr(2), []*int{IPtr(1), IPtr(2), nil}), B(true))
	eqTri("2 IN [1]", In(IPtr(2), []*int{IPtr(1)}), B(false))
	eqTri("2 IN []", In(IPtr(2), []*int{}), B(false))
	eqTri("null IN [1,2,3]", In(nil, []*int{IPtr(1), IPtr(2), IPtr(3)}), nil)
	eqTri("null IN [1,null,3]", In(nil, []*int{IPtr(1), nil, IPtr(3)}), nil)
	eqTri("null IN []", In(nil, []*int{}), B(false))
	ck("null IN [] 与 null IN [1,2,3] 不同",
		!triEq(In(nil, []*int{}), In(nil, []*int{IPtr(1), IPtr(2), IPtr(3)})), "")

	// ---------------------------------------------- [] 取值与区间
	ck("[1,2,3][null]", Get([]int{1, 2, 3}, nil) == nil, "")
	ck("[1,2,3,4][null..2]", Slice([]int{1, 2, 3, 4}, nil, IPtr(2)) == nil, "")
	ck("[1,2,3][1..null]", Slice([]int{1, 2, 3}, IPtr(1), nil) == nil, "")
	ck("[1,2,3][0..2] 正常切片", fmt.Sprint(Slice([]int{1, 2, 3}, IPtr(0),
		IPtr(2))) == "[1 2]", "")
	lo, hi := IPtr(0), IPtr(3)
	ck("coalesce 兜底后区间可用",
		fmt.Sprint(Slice([]int{1, 2, 3}, Coalesce(lo), Coalesce(hi))) == "[1 2 3]", "")

	// ---------------------------------------------- 产出 null 的表达式
	ck("[][0]", Get([]int{}, IPtr(0)) == nil, "")
	ck("head([])", Head([]int{}) == nil, "")
	ck("head([1])", *Head([]int{1}) == 1, "")
	eqTri("1 < null", Lt(IPtr(1), nil), nil)
	eqTri("null < 1", Lt(nil, IPtr(1)), nil)
	eqTri("1 < 2", Lt(IPtr(1), IPtr(2)), B(true))
	ck("1 + null", Arith(IPtr(1), nil) == nil, "")
	ck("1 + 2", *Arith(IPtr(1), IPtr(2)) == 3, "")
	ck("sin(null)", Sin(nil) == nil, "")
	z := 0.0
	ck("sin(0) 有实值", *Sin(&z) == 0.0, "")

	// ---------------------------------------------- all / any / none / single
	eqTri("any([true,null]) → true", Any([]Tri{B(true), nil}), B(true))
	eqTri("all([false,null]) → false", All([]Tri{B(false), nil}), B(false))
	eqTri("all([true,null]) → null", All([]Tri{B(true), nil}), nil)
	eqTri("any([false,null]) → null", Any([]Tri{B(false), nil}), nil)
	eqTri("none([true,null]) → false", None([]Tri{B(true), nil}), B(false))
	eqTri("none([false,null]) → null", None([]Tri{B(false), nil}), nil)
	eqTri("single([true]) → true", Single([]Tri{B(true)}), B(true))
	eqTri("single([true,null]) → null", Single([]Tri{B(true), nil}), nil)
	eqTri("single([true,true,null]) → false",
		Single([]Tri{B(true), B(true), nil}), B(false))

	// ---------------------------------------------- 类型谓词
	ck("null IS :: INTEGER → true", TypePredicate(nil, "INTEGER"), "")
	ck("null IS :: STRING → true", TypePredicate(nil, "STRING"), "")
	ck("1 IS :: INTEGER → true", TypePredicate(1, "INTEGER"), "")
	ck("\"a\" IS :: INTEGER → false", !TypePredicate("a", "INTEGER"), "")
	ck("\"a\" IS :: STRING → true", TypePredicate("a", "STRING"), "")

	// ---------------------------------------------- ORDER BY 的 null 位置
	rows := []*string{nil, SPtr("shipped"), SPtr("pending"), nil, SPtr("shipped")}
	asc, desc := OrderBy(rows, false), OrderBy(rows, true)
	ascNull, descNull := []bool{}, []bool{}
	for _, r := range asc {
		ascNull = append(ascNull, r == nil)
	}
	for _, r := range desc {
		descNull = append(descNull, r == nil)
	}
	ck("升序 null 都在尾部",
		fmt.Sprint(ascNull) == "[false false false true true]", fmt.Sprint(ascNull))
	ck("降序 null 都在头部",
		fmt.Sprint(descNull) == "[true true false false false]", fmt.Sprint(descNull))
	ck("升序非 null 有序",
		*asc[0] == "pending" && *asc[1] == "shipped" && *asc[2] == "shipped", "")
	ck("降序非 null 有序",
		*desc[2] == "shipped" && *desc[3] == "shipped" && *desc[4] == "pending", "")

	// ---------------------------------------------- coalesce
	ck("coalesce(null,null,3)", *Coalesce(nil, nil, IPtr(3)) == 3, "")
	ck("coalesce(null,null)", Coalesce(nil, nil) == nil, "")
	ck("coalesce(1,null)", *Coalesce(IPtr(1), nil) == 1, "")

	fmt.Printf("断言通过: %d\n", okCount)
	if len(failures) > 0 {
		fmt.Printf("失败 %d 条:\n", len(failures))
		for _, f := range failures {
			fmt.Println("  - " + f)
		}
		return
	}
	fmt.Println("ALL OK")
}
