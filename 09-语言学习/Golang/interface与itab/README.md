# interface 与 itab

## 简介

- Go 接口的实现机制:接口值是**双字对**(类型信息指针 + 数据指针),方法派发通过 **itable**(i-table,接口表)完成——itable 在**值被赋进接口的那一刻**计算(或命中缓存),调用点只做"几次内存读取 + 一次间接调用"。
- 关键概念:
  - **双字对**:interface value = (指向类型/itable 的指针, 指向数据的指针),两个指针对程序隐式不可见
  - **itable**:对应一个 (接口类型, 具体类型) 配对,头部是元数据、尾部是**只含接口要求方法**的函数指针列表
  - **方法集**:T 的方法集只含值接收者方法;*T 含值+指针接收者方法——决定"谁能实现接口"
  - **类型断言**:从接口取动态类型(comma-ok / 单值 panic / type switch)
  - **typed-nil 陷阱**:装箱了 `(*T)(nil)` 的接口 `!= nil`
- 历史背景:Russ Cox(Rob Pike、Ken Thompson 的 Go 三巨头之外的核心开发者)2009 年发表《Go Data Structures: Interfaces》奠定了这套设计;该设计比 C++/Java 的静态方法表灵活、比动态语言逐次查找高效。

## 原理详解

### 接口值与 itable 结构(rsc 模型)

```text
            interface value(双字对)
          ┌─────────────┬─────────────┐
          │  tab 指针    │  data 指针  │
          └──────┬──────┴──────┬──────┘
                 │             │
        ┌────────▼────────┐   ┌▼──────────────┐
        │ itable          │   │ 数据(堆上副本或 │
        │ ├ inter 元数据   │   │ 单字值直接内联) │
        │ ├ concrete 元数据│   └───────────────┘
        │ ├ fun[0] String │
        │ ├ fun[1] ...    │   ← 只含接口要求的方法,
        └─────────────────┘     具体类型的其他方法不出现
```

分步工作机制:

1. **赋值** `var s Speaker = b`:把 b 拷贝(必要时堆分配一个副本,data 字存指针)。
2. **itable 生成**:运行时拿接口方法表在具体类型方法表里逐个找实现;两个表**排序后同步遍历**,把 O(ni×nt) 降为 **O(ni+nt)**(ni=接口方法数,nt=具体类型方法数)。
3. **缓存**:itable 计算一次后进全局缓存,(接口类型, 具体类型) 配对太多无法预计算,绝大多数不需要。
4. **调用** `s.String()`:编译器生成等价于 `s.tab->fun[0](s.data)` 的代码——只做一次间接跳转,方法查找已前移到赋值点。
5. **断言** `v.(T)`:取 `s.tab->type` 与目标类型做**指针比较**,匹配后解引用 data。

两种内存优化(rsc):

- **空接口**:无方法则 itable 无用武之地,第一字**直接指向类型描述结构**(现代 runtime 的 eface/iface 双结构雏形)。
- **单字值**:值能塞进一个机器字时**取消间接、不堆分配**,直接内联在 data 字。

### 方法集与实现判定(规范)

| 方法接收者 | `T` 的方法集 | `*T` 的方法集 |
| --- | --- | --- |
| `func (t T) M()` | ✅ | ✅ |
| `func (t *T) M()` | ❌ | ✅ |

- 实现 = **纯结构化判定**:T 在接口的 type set 中即实现,无 `implements` 关键字;"Binary 的作者可以从未听说过 Stringer"。
- 空接口 `interface{}` 的 type set 是所有非接口类型;`any` 是它的预声明别名(Go 1.18)。
- 嵌入接口的 type set = 各元素 type set 的**交集**;同名方法签名必须一致。

### nil 的三种面孔

```go
var x interface{}       // x 是 nil,没有动态类型
var v *T                // v 是 (*T)(nil)
x = v                   // x 非 nil!动态类型 *T、值 (*T)(nil)
```

- 规范原文:`x = v` 之后 "x has value `(*T)(nil)` and dynamic type `*T`"——接口第一字(类型信息)非空,整体就非 nil。
- 纯 nil 接口没有动态类型(`nil ... has no type`),对它调方法无法定位函数表。

## 对比 / 选型

| 方案 | 方法表时机 | 调用点开销 | 说明 |
| --- | --- | --- | --- |
| C++/Java vtable | 编译/加载期静态确定 | 一次间接跳转 | 但要求类层次声明式实现接口 |
| Smalltalk/Python | 每次调用查找+内联缓存 | 循环内重复查找 | 对象方法可运行时改变,查找无法轻易外提 |
| **Go itable** | **赋值期计算+缓存** | 一次间接跳转 | "介于两者之间:有方法表,但运行时计算"(rsc) |

代价:Go 牺牲了动态语言"调用点换类型"的灵活性(rsc 评论区对此有争论);换来的是无锁、无缓存争用的调用路径。

## 环境准备

- 操作系统:任意(Go 跨平台)
- 语言版本:Go 1.18+(用了 `any`)
- 依赖:无(纯标准库)

## 运行方式

### Go

```bash
cd go
go run interface_itab.go
```

## 关键代码片段

itable 生成 + 缓存(对应原理第 2/3 步):

```go
// 排序两个方法表后同步遍历:O(ni+nt)
sorted := append([]string(nil), interMethods...)
sort.Strings(sorted)
for _, m := range sorted {
    if f, ok := d.methods[m]; ok { found[m] = f }
}
tab := &itable{inter: interName, concrete: d.name, fun: found}
itabCache[key] = tab // 生成一次,永久缓存
```

typed-nil 陷阱(对应"三种面孔"):

```go
func failSometimes(fail bool) error {
    var e *MyErr          // e == nil(*MyErr 类型)
    if fail { e = &MyErr{42} }
    return e              // 装箱:(动态类型 *MyErr, data=nil) => 接口 != nil!
}
```

## 性能与边界

- itable 生成 O(ni+nt),只发生一次/配对;调用 = "a couple of memory fetches and a single indirect call instruction"(rsc)。
- rsc 原文明确:"I don't have any numbers to back this discussion"——定性结论可靠、无官方基准数字,别引用具体百分比。
- 接口装箱大值会堆分配(拷贝语义);单字值除外。

## 注意事项与常见坑

- **坑 1:返回 `*MyErr` 类型的 nil 被判 `err != nil`**。现象:函数明明"没出错"调用方却走错误分支。原因:装箱后接口第一字非 nil。规避:函数里显式 `return nil`,或让函数返回具体类型由调用方装箱。
- **坑 2:值类型不实现只含指针接收者方法的接口**。现象:`Dog` 赋给 `Speaker` 编译报错。原因:方法集规则(指针接收者只在 *T 方法集)。规避:统一接收者类型;接口存值前想清楚会不会需要改字段。
- **坑 3:单值断言 `v.(int)` 失败直接 panic**。规避:用 comma-ok `x, ok := v.(T)`。
- **坑 4:动态语言习惯迁移**。Go 调用点不能像 Python/JS 那样每轮换方法表——itable 绑定在值上而非变量上。

## 参考资料(实际阅读过的权威来源)

- [Go Data Structures: Interfaces — Russ Cox, research.swtch.com/interfaces](https://research.swtch.com/interfaces) — 双字对/itable 结构、O(ni+nt) 生成算法、缓存、赋值期派发、空接口与单字优化、调用汇编证据,全部出自本文。
- [The Go Programming Language Specification — go.dev/ref/spec](https://go.dev/ref/spec) — 接口 type set 定义、方法集三条规则、结构性实现判定、`any` 别名、nil 接口无动态类型与 `x = v` 的 `(*T)(nil)` 示例。
