# type-parameter 泛型(Go 1.18+)

## 简介

- Go 1.18 引入的**类型参数**机制:函数与类型可以独立于具体类型书写,一套代码服务一组类型。三大支柱:**类型参数**(方括号语法 + 实例化)、**接口即类型集**(可含无方法的类型/并集/底层类型)、**类型推断**(调用时多数可省类型实参)。
- 关键概念:
  - **类型参数列表**:`[T Number]`——方括号而非圆括号,Number 是 T 的约束
  - **实例化两步**:① 编译器用类型实参替换全部类型参数 ② 校验每个实参满足对应约束
  - **type set**:约束就是接口;`|` 并集、`~T` 底层类型集合
  - **~T**:底层类型为 T 的所有类型(`~int` 包含 `int` 与 `type MyInt int`)
  - **两种类型推断**:函数实参推断 + 约束类型推断
- 历史背景:Griesemer 与 Ian Lance Taylor(Go 泛型两位主设)2022 年 3 月发布官方博客,基于 GopherCon 2021 演讲;Go 团队自 2010 年起多稿设计(2019 type functions、2020 contracts、2021 定稿 type parameters)最终落地。

## 原理详解

### 从 Min 到 GMin

```go
func Min(x, y float64) float64        // 只能 float64
func GMin[T Number](x, y T) T         // 一套代码服务整个 Number 类型集
```

实例化:`GMin[float64]` 之后"就是原先那个 float64 版 Min 的普通函数",可当值传递(`fmin := GMin[float64]`);**约束校验发生在实例化**,实参不满足约束则程序无效。

### 约束即类型集

- 方法集视图(传统接口)之外,Go 1.18 起可显式往集合里**加类型**:`interface{ ~int | ~string }`。
- 函数体内允许的操作 = **类型集内所有类型都支持的操作**(如 `Number` 集合内全体支持 `<`,故函数体可比大小)。
- `constraints.Ordered` 的真身是 `Integer|Float|~string`——"所有整型 ∪ 所有浮点型 ∪ 底层为 string 的类型",比 `int|float64|string` 宽得多(`int32`、`type MyString string` 都满足)。
- 约束写法三步简化:`[S interface{~[]E}, E interface{}]` → `[S ~[]E, E interface{}]` → `[S ~[]E, E any]`(`any` 是空接口的新预声明别名)。
- 限制:用了新语法(union/~)的接口**只能用作约束**,不能作变量类型。

### 两种类型推断

1. **函数实参推断**:从实参类型推类型参数——`GMin(a, b)` 推出 T=float64。限制:只对**出现在函数参数里的**类型参数有效;只在返回值/函数体里的(如 `Make[T any]() T`)推不出,必须显式 `Make[int]()`。
2. **约束类型推断**(Scale 例子,官方博客最详细的部分):
   - 第一版 `Scale[E Integer](s []E, c E) []E` 传 `Point`(`type Point []int32`)得到 `[]int32`,**丢失 Point 类型**,下游 `.String()` 编译失败;
   - 修复:`Scale[S ~[]E, E Integer](s S, c E) S`——实参推断从 p 推出 S=Point;`2` 是**无类型常量**推不出 E,此时**从 S 的约束 `~[]E` 反推 E=int32**(切片的元素类型);
   - 设计哲学:"宁可不推断,也不推断错"(err on the side of failing to infer)。推断要么成功要么失败,失败就显式写类型实参。

### 泛型类型

```go
type Tree[T any] struct { left, right *Tree[T]; value T }
func (t *Tree[T]) Lookup(x T) *Tree[T]  // 接收者携带类型参数
var stringTree Tree[string]             // 使用前必须实例化
```

## 对比 / 选型

| 维度 | 接口+反射 | 代码生成 | **类型参数** |
| --- | --- | --- | --- |
| 类型安全 | 运行时断言 | 编译期 | **编译期** |
| 性能 | 反射开销 | 零开销 | 实例化后同普通函数 |
| 侵入性 | 低 | 需生成步骤 | 语言内置 |
| 适用 | 真正动态 | 高性能容器 | **算法与容器同构复用** |

## 环境准备

- 操作系统:任意
- 语言版本:**Go 1.18+**(demo 用 1.21)
- 依赖:无(自定义 Number 约束,不引 golang.org/x/exp/constraints)

## 运行方式

### Go

```bash
cd go
go run generics_demo.go
```

## 关键代码片段

约束类型推断(对应"两种类型推断"第 2 条):

```go
type Point []int32
func Scale[S ~[]E, E ~int32 | ~float64](s S, c E) S {
    r := make(S, len(s))
    for i, v := range s { r[i] = v * c }
    return r
}
r := Scale(p, 2)   // S=Point(实参推断),E=int32(约束推断);返回仍是 Point
```

~ 底层类型集合(对应"约束即类型集"):

```go
type Number interface{ ~int | ~int64 | ~float64 | ~string }
type MyInt int          // 底层类型 int -> 满足 ~int
GMin(MyInt(3), MyInt(2)) // OK;若约束写裸 int 则拒绝 MyInt
```

## 性能与边界

- 成功实例化后就是"非泛型函数",调用无额外分发成本(实现机制如 GC shape stenciling/字典不在官方博客范围内,本 demo 不展开)。
- 官方提醒(2022):新代码"未经大量生产环境检验",生产部署保持谨慎。
- `any` 约束不保证可比较——需要 `==` 应改用更强约束(demo 的 Tree.Lookup 因此走字符串序比较)。

## 注意事项与常见坑

- **坑 1:`Scale` 只写一个类型参数丢具名类型**。现象:传 `Point` 返回 `[]int32`,下游方法消失。原因:参数类型 `[]E` 会抹掉具名切片类型。规避:加 `S ~[]E` 双参数(官方修复版)。
- **坑 2:裸类型 `int` vs `~int` 混淆**。裸 `int` 只匹配 int 本身;`type MyInt int` 被拒。约束场景几乎总该写 `~`。
- **坑 3:推断失败以为能自动补**。`Make[T any]() T` 这类 T 不在参数列表的必须显式 `Make[int]()`。
- **坑 4:union 接口当变量类型**。`interface{~int|~string}` 只能作约束,声明变量是编译错误。
- **坑 5:对 `any` 类型参数用 `==`**。类型集含 slice/map 等不可比较类型,编译报错;需比较时收紧约束。

## 参考资料(实际阅读过的权威来源)

- [An Introduction To Generics — Griesemer & Taylor, go.dev/blog/intro-generics](https://go.dev/blog/intro-generics) — Min→GMin、实例化两步、Ordered=`Integer|Float|~string`、~ 语义、约束写法三步简化、函数实参/约束两种推断与 Scale 完整案例、Tree[T] 泛型类型、生产谨慎提醒。
- [The Go Programming Language Specification — go.dev/ref/spec](https://go.dev/ref/spec) — Interface types 一节的 type set 产生式(MethodElem/TypeElem/`~` UnderlyingType)、type set 五条规则、`any` 别名、非基本接口只能用作约束。
