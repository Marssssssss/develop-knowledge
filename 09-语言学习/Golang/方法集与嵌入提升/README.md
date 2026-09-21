# Go 方法集与嵌入提升（Go 规范 §Method sets / §Selectors）

## 简介

「为什么 `var t T2` 能调 `t.M0()` 而 `T2` 自己没定义 `M0`？」「为什么两个嵌入类型都有 `F` 时
`c.F` 编译不过？」——答案全在语言规范的两节里：**方法集**决定「这个类型有哪些方法」，
**选择器**决定 `x.f` 到底指向哪一个（按**深度最浅且唯一**）。

关键概念：

| 概念 | 一句话 |
| --- | --- |
| 方法集 | 类型 `T` 的方法集 = 接收者为 `T` 的方法；`*T` 的方法集 = 接收者为 `T` **或** `*T` 的方法 |
| 提升（promoted） | `x.f` 合法且指向嵌入字段里的 `f` 时，`f` 称为被提升到外层 |
| 深度（depth） | 到达 `f` 需要穿过几层嵌入字段；自己声明的是 0 |
| 歧义选择器 | 最浅深度上有不止一个 `f` → 表达式非法 |

## 原理详解

### 1. 方法集：T 是 *T 的子集

> 规范原文：The method set of a defined type `T` consists of all methods declared with
> receiver type `T`. The method set of a pointer to a defined type `T` (where `T` is neither
> a pointer nor an interface) is the set of all methods declared with receiver `*T` or `T`.

所以：

```go
func (t T)  Val() {}   // 属于 T 和 *T
func (t *T) Ptr() {}   // 只属于 *T
```

**这是最容易记反的一条**：`*T` 的方法集更大，值类型 `T` 的方法集更小。

接口的方法集是**类型集里每个类型方法集的交集**（规范原文用了 intersection）。
实测：`IA{M,N}` 与 `IB{N,O}` 组成的接口，方法集只有 `{N}`。

### 2. 提升规则：嵌入值 vs 嵌入指针，差别很大

> 规范原文（§Struct types）：
> - If `S` contains an embedded field `T`, the method sets of `S` and `*S` both include
>   promoted methods with receiver `T`. The method set of `*S` also includes promoted
>   methods with receiver `*T`.
> - If `S` contains an embedded field `*T`, the method sets of `S` and `*S` both include
>   promoted methods with receiver `T` or `*T`.

实测（自检 E3）：

| 声明 | `S` 的方法集 | `*S` 的方法集 |
| --- | --- | --- |
| `struct { A }`（A 有 `Val`/`Ptr`） | `{Val}` | `{Val, Ptr}` |
| `struct { *A }` | `{Val, Ptr}` | `{Val, Ptr}` |

> 嵌入 `*A` 时，**值类型 `S` 也能调到指针接收者方法**——因为它提升的是 `*A` 的方法集。

### 3. 选择器：深度最浅且唯一

> 规范原文：For a value `x` of type `T` or `*T` where `T` is not a pointer or interface type,
> `x.f` denotes the field or method at the **shallowest depth** in `T` where there is such an `f`.
> If there is **not exactly one** `f` with shallowest depth, the selector expression is illegal.

深度定义：在 `T` 里声明的 `f` 深度为 0；在嵌入字段 `A` 里的 `f` 深度 = `f` 在 `A` 里的深度 + 1。

**规范自带的官方示例**（本 demo 逐条复现）：

```go
type T0 struct{ x int }
func (*T0) M0()
type T1 struct{ y int }
func (T1) M1()
type T2 struct {
    z int
    T1          // 嵌入值
    *T0         // 嵌入指针
}
func (*T2) M2()
type Q *T2
```

| 表达式 | 解析 | 深度 |
| --- | --- | --- |
| `t.z` | `t.z` | 0 |
| `t.y` | `t.T1.y` | 1 |
| `t.x` | `(*t.T0).x` | 1 |
| `p.M0()` | `((*p).T0).M0()`（需要 `*T0` 接收者） | 1 |
| `p.M1()` | `((*p).T1).M1()` | 1 |
| `p.M2()` | `p.M2()` | 0 |
| `t.M2()` | `(&t).M2()`（可寻址时自动取址，见 §Calls） | 0 |

方法集实测：`T2` = `{M0, M1}`，`*T2` = `{M0, M1, M2}`（`M2` 是指针接收者，值类型拿不到）。

### 4. 定义型指针的例外：只放行字段

> 规范原文：As an exception, if the type of `x` is a defined pointer type and `(*x).f` is a
> valid selector expression denoting a **field (but not a method)**, `x.f` is shorthand for `(*x).f`.

所以 `Q = *T2` 时：

- `q.x`、`q.z` **合法**（`(*q).x` 是字段选择器）；
- `q.M0()` **非法**（`(*q).M0` 是方法，不适用这个例外）。

规范里 `q.M0()` 就被明确列在 "the following is invalid" 下面。

### 5. 歧义：最浅层不唯一即非法

两个嵌入类型在同深度都提供 `F` → `c.F` 编译失败，**即使类型相同也不行**：

```go
type C struct { A; B }   // A、B 都有 F
_ = c.F                  // ambiguous selector c.F
```

但只要自己在深度 0 声明一个 `F`，就**遮蔽**掉两处提升，`d.F` 恢复合法。
注意：字段与方法**共用同一个名字空间**——`A` 有字段 `F`、`B` 有方法 `F` 同样歧义。

## 对比 / 选型

| 做法 | 拿到指针接收者方法 | 拿到值接收者方法 | 备注 |
| --- | --- | --- | --- |
| 嵌入 `T` | 只有 `*S` | `S` 和 `*S` | 最常见 |
| 嵌入 `*T` | `S` 和 `*S` | `S` 和 `*S` | 需要非 nil，否则调用时 panic |
| 显式字段 + 手写转发 | 都行 | 都行 | 代码多，但语义最清楚 |

嵌入适合「组合出接口实现」，不适合单纯复用代码——提升出来的东西会出现在公开 API 里。

## 环境准备

- Go 1.21+；Python 3.8+；无第三方依赖

## 运行方式

```bash
cd python && python selfcheck_selector.py   # 47 条断言
cd python && python main.py                 # 五组结论
cd go && go run .                           # 八组结论，内置 check 断言
```

## 关键代码片段

深度优先收集 + 取最浅唯一（`go/selectors.go`）：

```go
func (u *Universe) Lookup(xType, f string) (Sel, error) {
	if f == "_" {
		return Sel{}, fmt.Errorf("selector must not be the blank identifier")
	}
	t := u.Base(xType)
	var out []Sel
	u.collect(t, f, 0, nil, &out, map[string]bool{})
	if len(out) == 0 {
		return Sel{}, fmt.Errorf("x.%s undefined ...", f)
	}
	best := out[0].Depth
	for _, s := range out { if s.Depth < best { best = s.Depth } }
	var cands []Sel
	for _, s := range out { if s.Depth == best { cands = append(cands, s) } }
	if len(cands) != 1 {
		return Sel{}, fmt.Errorf("ambiguous selector %s at depth %d", f, best)
	}
	return cands[0], nil
}
```

## 性能与边界

- 深度是**静态**概念，编译器在类型检查期算完，运行时没有开销
- 嵌入成环（`type A struct{ *A }`）在 Go 里是非法的（递归类型），模型里做了防御
- 方法集里每个方法名必须唯一（空白名 `_` 除外），否则该类型不可用作接口实现

## 注意事项与常见坑

1. **值类型拿不到指针接收者方法**：`var t T2; t.M2()` 能过是因为 `t` 可寻址、编译器自动取址；
   把它放进 `map` 或作为函数返回值（`f().M2()`）就编译不过了。
2. **`T` 不满足接口但 `*T` 满足**：实现接口时先看方法集，别只看「方法写在哪」。
3. **嵌入 `*T` 是有风险的**：`S` 的方法集里有指针接收者方法，但零值 `S{}` 里那个 `*T` 是 nil，
   一调用就 panic。
4. **歧义不是「选第一个」**：规范直接判非法，编译器报 `ambiguous selector`。
5. **`q.M0()` 与 `q.x` 待遇不同**：定义型指针只对字段开后门，见 §4。
6. **字段与方法同名同深度会冲突**：不要以为「一个是字段一个是方法」就能共存。
7. 提升是**一层层递归**的：三层嵌入的深度是 3，不是 1。

## 参考资料（实际阅读过的权威来源）

- [Go 语言规范 §Method sets](https://go.dev/ref/spec#Method_sets) — `T` 与 `*T` 方法集的定义、接口方法集是类型集方法集的交集
- [Go 语言规范 §Selectors](https://go.dev/ref/spec#Selectors) — 深度定义、「shallowest depth」与「not exactly one ⇒ illegal」、定义型指针只对字段开后门、官方 T0/T1/T2/Q 示例与 `q.M0()` 非法
- [Go 语言规范 §Struct types](https://go.dev/ref/spec#Struct_types) — 嵌入字段的字段名规则、promoted methods 的两条提升规则（嵌入 `T` 与嵌入 `*T` 的差异）
- [Effective Go · Embedding](https://go.dev/doc/effective_go#embedding) — 嵌入的惯用法与「interface 嵌入 vs struct 嵌入」的区别
