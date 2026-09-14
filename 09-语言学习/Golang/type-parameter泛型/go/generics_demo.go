// generics_demo.go — 单文件串讲 Go 1.18 泛型:类型参数 / 类型集约束 /
// ~ 底层类型 / 两种类型推断 / 泛型类型
// Demo 6: type parameter(Go 1.18+ 泛型)
// 内容依据官方博客《An Introduction To Generics》(Griesemer & Taylor, 2022):
//   Min -> GMin 改造、实例化两步、约束是接口(type set)、
//   ~ 底层类型集合、函数实参推断 + 约束类型推断(Scale 例子)、Tree[T] 泛型类型
package main

import (
	"fmt"
	"strings"
)

// ---------- 1) 从重复代码到类型参数:Min -> GMin ----------
// 改造前:func Min(x, y float64) float64 —— 只能用于 float64,
// int/string 各写一份就是泛型要消灭的重复。
// 改造后:类型参数列表用方括号,T 的约束限定它可以是哪些类型。

// Number 自定义约束(对标 constraints.Ordered = Integer|Float|~string 的写法):
// 竖线是类型集的并集;~int 表示"底层类型是 int 的所有类型"
type Number interface {
	~int | ~int64 | ~float64 | ~string
}

func GMin[T Number](x, y T) T {
	if x < y { // 允许 < :约束类型集里每个类型都支持
		return x
	}
	return y
}

func demoGMin() {
	fmt.Println("[1] Min -> GMin")
	fmt.Println("  GMin[int](2, 3)          =", GMin[int](2, 3))   // 显式类型实参
	fmt.Println("  GMin(2.71, 3.14)         =", GMin(2.71, 3.14))  // 类型推断省略
	// 实例化两步:① 用类型实参替换类型参数 ② 校验实参满足约束
	// fmin := GMin[float64] 之后就是普通函数,可当值传递
	fmin := GMin[float64]
	fmt.Println("  fmin := GMin[float64]    =", fmin(2.71, 3.14))

	// ~ 的意义:type MyInt int 的底层类型是 int,满足 ~int
	type MyInt int
	fmt.Println("  GMin(MyInt(3), MyInt(2)) =", GMin(MyInt(3), MyInt(2)))
}

// ---------- 2) 约束即类型集:方法集视图 vs 类型集视图 ----------
// 规范:接口定义一个 type set;类型集视图的优势是可以"显式往集合里加类型"。
// 约束决定函数体内允许哪些操作:操作须被类型集内所有类型支持。
type Doubler interface {
	~int | ~float64
}

func Double[T Doubler](x T) T { return x * 2 }

func demoTypeSet() {
	fmt.Println("\n[2] 约束即类型集")
	type Celsius float64 // 底层类型 float64 -> 满足 ~float64
	fmt.Println("  Double(21)          =", Double(21))
	fmt.Println("  Double(Celsius(36)) =", Double(Celsius(36.5)))
}

// ---------- 3) 函数实参类型推断 ----------
// 推断从函数实参的类型出发;限制:只对出现在函数参数里的类型参数有效,
// 仅出现在返回值/函数体里的类型参数推断不了。
type Slice[T any] []T // any 是空接口的新预声明别名

func Make[T any]() T { // T 不在参数列表
	var zero T
	return zero
}

func demoArgInference() {
	fmt.Println("\n[3] 函数实参类型推断")
	s := Slice[int]{1, 2, 3} // 泛型类型使用前必须实例化
	fmt.Printf("  Slice[int]{1,2,3} -> %v\n", s)
	// Make() 推不出 T:必须显式 Make[int]()
	fmt.Println("  Make[int]() =", Make[int]())
}

// ---------- 4) 约束类型推断:Scale 例子(官方博客完整复刻)----------
// 第一版问题:func Scale[E constraints.Integer](s []E, c E) []E 返回 []E,
// 传 Point([]int32) 进去得到 []int32,丢失 Point 类型 -> 下游 .String() 编译失败。
// 修复:再加一个切片类型参数 S,约束 ~[]E(底层类型是 E 的切片)。
type Point []int32

func (p Point) String() string {
	parts := make([]string, len(p))
	for i, v := range p {
		parts[i] = fmt.Sprint(v)
	}
	return "Point(" + strings.Join(parts, ",") + ")"
}

func Scale[S ~[]E, E ~int32 | ~float64](s S, c E) S {
	r := make(S, len(s))
	for i, v := range s {
		r[i] = v * c // E 类型集内所有类型都支持 *,逐元素乘
	}
	return r
}

func demoConstraintInference() {
	fmt.Println("\n[4] 约束类型推断(Scale 例子)")
	p := Point{1, 2, 3}
	// Scale(p, 2) 无需显式类型实参:
	//   函数实参推断从 p 推出 S = Point;
	//   2 是无类型常量推不出 E ->
	//   约束类型推断从 S 的约束 ~[]E 反推出 E = int32(切片的元素类型)
	r := Scale(p, 2)
	fmt.Println("  Scale(Point{1,2,3}, 2) ->", r.String()) // 仍是 Point,有 String 方法
}

// ---------- 5) 泛型类型:Tree[T] 与带类型参数的方法 ----------
type Tree[T any] struct {
	left, right *Tree[T]
	value       T
}

func (t *Tree[T]) Lookup(x T) *Tree[T] { // 方法接收者携带类型参数
	// 注意:T any 不保证可比较(== 需要类型集全部支持),故用字符串序比较;
	// 这正是 any 约束的边界 —— 需要相等比较时应改用更强的约束
	for t != nil {
		xs, vs := fmt.Sprint(x), fmt.Sprint(t.value)
		if xs == vs {
			return t
		}
		if xs < vs {
			t = t.left
		} else {
			t = t.right
		}
	}
	return nil
}

func NewTree[T any](v T) *Tree[T] { return &Tree[T]{value: v} }

func (t *Tree[T]) Insert(v T) *Tree[T] {
	if t == nil {
		return &Tree[T]{value: v}
	}
	if fmt.Sprint(v) < fmt.Sprint(t.value) {
		t.left = t.left.Insert(v)
	} else {
		t.right = t.right.Insert(v)
	}
	return t
}

func demoGenericType() {
	fmt.Println("\n[5] 泛型类型 Tree[T]")
	var root *Tree[string]
	for _, w := range []string{"mango", "apple", "pear", "banana"} {
		root = root.Insert(w)
	}
	fmt.Println("  Lookup(pear) 命中?", root.Lookup("pear") != nil)
	fmt.Println("  Lookup(grape) 命中?", root.Lookup("grape") != nil)
}

// ---------- 6) 约束位置可省 interface{}:三种等价写法 ----------
// [S interface{~[]E}, E interface{}] -> [S ~[]E, E interface{}] -> [S ~[]E, E any]
func Head[S ~[]E, E any](s S) (E, bool) {
	if len(s) == 0 {
		var zero E
		return zero, false
	}
	return s[0], true
}

func demoSugar() {
	fmt.Println("\n[6] 约束写法的三步简化")
	if v, ok := Head([]int{42, 7}); ok {
		fmt.Println("  Head([]int{42,7}) =", v)
	}
	if _, ok := Head([]int{}); !ok {
		fmt.Println("  Head(空切片) -> zero value + false")
	}
}

func main() {
	demoGMin()
	demoTypeSet()
	demoArgInference()
	demoConstraintInference()
	demoGenericType()
	demoSugar()
}
