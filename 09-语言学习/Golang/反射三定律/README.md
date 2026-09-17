# Go 反射三定律：interface 对、flag 位与「可设置性」的真实边界

## 简介

Rob Pike 2011 年的 *The Laws of Reflection* 把 Go 反射压成三句话：

1. 反射是**从 interface 值到反射对象**；
2. 反射是**从反射对象回到 interface 值**；
3. **要修改反射对象，值必须是可设置的（settable）**。

三条读起来都像废话，但第三条背后是 `reflect.Value` 里一个 9 位的 `flag`
字段，而"未导出字段""嵌入字段""指针解引用"分别落在**不同的位**上，行为差异
极大：`CanAddr` 为真不代表 `CanSet` 为真，嵌入字段的导出成员反而**可以**设置。

本 demo 把 `flag` 位判据做成可执行模型，逐条验证这三条定律的边界。核心概念：

| 概念 | 一句话 |
| --- | --- |
| interface 对 | 接口变量内部永远是 `(值, 具体类型)` 二元组，**不能**是 `(值, 接口类型)` |
| `Kind` vs `Type` | `Kind` 给底层类型（`MyInt` → `Int`），`Type` 给静态类型（`MyInt`） |
| zero Value | `ValueOf(nil)` 或 `Value{}`：`flag == 0`，`Kind() == Invalid`，其余方法 panic |
| settable | `flagAddr` 置位**且** `flagRO` 清零；写死在 `CanSet()` 一行里 |
| flagRO | `flagStickyRO`（未导出非嵌入字段）\| `flagEmbedRO`（未导出嵌入字段） |

## 原理详解

### 1. 第一定律：入口一定经过 interface

`TypeOf` 的签名本身就把这件事说穿了：

```go
func TypeOf(i interface{}) Type
func ValueOf(i any) Value      // ValueOf(nil) 返回 zero Value
```

调用 `reflect.ValueOf(x)` 时 `x` 先被装进一个空接口再传参。**这一步是复制**，
所以对 `v` 调 `SetFloat` 也不可能改到 `x`（模型的 C 组断言专门验证了这一点：
`ValueOf` 之后改原变量，`v` 里仍是旧值）。

接口对的第二个要点：接口里装的永远是**具体类型**。`var i any = r` 之后
`ValueOf(i).Elem()` 剥出来的 `Type()` 是 `*os.File`，而不会是 `io.Reader`。

### 2. Kind 与 Type 不能互相替代

```go
type MyInt int
var x MyInt = 7
v := reflect.ValueOf(x)      // v.Type() == MyInt, v.Kind() == reflect.Int
```

官方原文：*the Kind of a reflection object describes the underlying type, not
the static type... the Kind cannot discriminate an int from a MyInt even though
the Type can.* 另一个副作用是 getter/setter 一律走**能装下该值的最大类型**
（`Int()`/`SetInt` 用 `int64`，`Uint()` 用 `uint64`），写入时再按实际类型截断
—— 所以 `int8` 的 `SetInt(300)` 落盘是 `44`。

### 3. 第三定律：`flagAddr` 与 `flagRO`

`value.go` 里 `CanSet` 只有一行：

```go
func (v Value) CanSet() bool {
	return v.flag&(flagAddr|flagRO) == flagAddr
}
```

```text
flag uintptr:
  bit0..4  Kind（27 个，flagKindWidth = 5）
  bit5     flagStickyRO   经"未导出且非嵌入"字段得到 → 只读
  bit6     flagEmbedRO    经"未导出嵌入"字段得到   → 只读
  bit7     flagIndir      ptr 指向数据
  bit8     flagAddr       CanAddr 为真（隐含 flagIndir 且 ptr 非 nil）
  bit9     flagMethod     方法值
flagRO = flagStickyRO | flagEmbedRO
```

由此得到三个反直觉结论：

- **`ValueOf(&x)` 自身不可设置**，要 `ValueOf(&x).Elem()` 才可设置（`Elem`
  里写死 `fl := v.flag&flagRO | flagIndir | flagAddr`）；
- **未导出字段可取址但不可设置**：`CanAddr()` 为真、`CanSet()` 为假
  —— 所以 `CanSet ⇒ CanAddr` 成立，反之不成立；
- **未导出字段只挡 `Interface()`/`Set`，不挡 `Int()`**：读数值走 `mustBe(Int)`，
  没有 `mustBeExported` 检查。

### 4. `Field()` 的掩码：为什么嵌入字段的导出成员可写

```go
// Inherit permission bits from v, but clear flagEmbedRO.
fl := v.flag&(flagStickyRO|flagIndir|flagAddr) | flag(typ.Kind())
if !field.Name.IsExported() {
	if field.Embedded() { fl |= flagEmbedRO } else { fl |= flagStickyRO }
}
```

掩码里**没有 `flagEmbedRO`，也没有 `flagMethod`**。于是：

```text
outer{ A int; b int; inner(未导出嵌入){ X int; y int } }
  s := ValueOf(&outer).Elem()
  s.Field(1)        → flagStickyRO         CanSet false
  s.Field(2)        → flagEmbedRO          CanSet false
  s.Field(2).Field(0) → RO 被清掉           CanSet true   ← 导出成员可写
  s.Field(2).Field(1) → 重新置 StickyRO     CanSet false
  s.FieldByName("X")  → 走 FieldByIndex，同上可写
```

`flagEmbedRO` 之所以不"粘"，正是为了让提升上来的导出字段保持可写；而
`ro()` 折叠（`if f&flagRO != 0 { return flagStickyRO }`）只在 `Elem()` 的
Interface 分支出现，所以经接口派生出的值会把 `EmbedRO` 变成 `StickyRO`。

### 5. 常见 panic 消息（模型已逐条断言）

| 场景 | 消息 |
| --- | --- |
| 非指针/接口调 `Elem` | `reflect: call of reflect.Value.Elem on Int Value` |
| zero Value 上任何方法 | `reflect: call of reflect.Value.X on zero Value` |
| `Field` 越界 | `reflect: Field index out of range` |
| 不可取址却 `Set` | `reflect: reflect.Value.SetInt using unaddressable value` |
| 未导出字段 `Set` | `... using value obtained using unexported field` |
| 未导出字段 `Interface` | `reflect.Value.Interface: cannot return value obtained from unexported field or method` |

注意判定**顺序固定**：`mustBeAssignable` 先查 `flagRO`、再查 `flagAddr`、
最后才轮到 `mustBe(Kind)` —— 所以对一个不可设置的 `float64` 调 `SetInt`，
报的是 unaddressable 而不是 Kind 不符。

## 环境准备

- 任意操作系统；Python 3.8+（实测 3.13.12）。
- `go/` 目录需要 Go 工具链；模型本身与平台无关。

## 运行方式

```bash
cd python && python3 main.py    # 103 项断言，退出码 0 表示全绿
cd go && go run .               # kinds.go + model.go + main.go，同一批断言
```

## 关键代码片段

`python/value_model.py` —— `CanSet` 与 `Field` 的权限位继承：

```python
def can_set(self):
    return self.flag & (FLAG_ADDR | FLAG_RO) == FLAG_ADDR

def field(self, i):
    f = self.struct_type.fields[i]
    fl = (self.flag & (FLAG_STICKY_RO | FLAG_INDIR | FLAG_ADDR)) | f.kind
    if not f.exported:
        fl |= FLAG_EMBED_RO if f.embedded else FLAG_STICKY_RO
    return RValue(f.kind, f.typ, fl, obj.slots[i], self.store, f.struct_type)
```

## 性能与边界

- 反射调用**绕过编译期类型检查**，无法内联、无法逃逸分析优化，常见做法是
  在热路径外做一次 `reflect` 探测、把结果缓存成 `func` 闭包。
- 本 demo 只建模**可设置性**，不含字段对齐/`unsafe` 布局（见同批次的
  `unsafe与内存布局` demo）、不含方法值与方法表（`flagMethod` 仅出现在掩码断言里）。
- `StructType.field_by_name` 实现了 reflect 的**深度优先提升 + 同深度歧义判定**，
  但只下钻 8 层；真实实现见 `src/reflect/type.go` 的 `FieldByNameFunc`。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| `ValueOf(x).SetXxx` panic | `ValueOf` 传的是拷贝，`flagAddr` 未置 | 用 `ValueOf(&x).Elem()` |
| 拿到 `*T` 就以为能改 | 指针 Value 自身 `CanSet` 为假 | 先 `Elem()` |
| `CanAddr` 为真就直接写 | 未导出字段 `CanAddr` 真但 `CanSet` 假 | 一律先查 `CanSet()` |
| 用 `Int()` 读未导出字段却以为会被拦 | 只有 `Interface`/`Set` 走 `mustBeExported` | 想拿值只能用具体类型的 getter |
| 忽略 `Kind` 差异 | `Kind` 描述底层类型，`MyInt` 与 `int` 同 Kind | 需要区分时看 `Type()` |
| 两处嵌入同名成员想提升 | 同深度两名候选 → `FieldByName` 返回 zero Value | 用 `FieldByIndex` 明确路径 |

## 参考资料（实际阅读过的权威来源）

- [The Laws of Reflection](https://go.dev/blog/laws-of-reflection)（Rob Pike, 2011-09-06）—— 三定律原文、interface 的 `(值, 类型)` 表示、`Kind` 与最大类型 getter、`Elem()` 与 `flagAddr` 的关系、结构体字段与"只有导出字段可设置"。
- [go1.24.0 `src/reflect/value.go`](https://raw.githubusercontent.com/golang/go/go1.24.0/src/reflect/value.go) —— flag 常量块、`CanSet` 一行判定、`Elem`/`Field` 的完整实现与权限位掩码、`mustBeAssignable`/`mustBeExported` 的 panic 消息、`ValueError.Error()` 格式、`ro()` 折叠。
- [go1.24.0 `src/reflect/type.go`](https://raw.githubusercontent.com/golang/go/go1.24.0/src/reflect/type.go) —— `Invalid Kind = iota` 起的 27 个 Kind 常量顺序。
