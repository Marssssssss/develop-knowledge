# Go unsafe 与内存布局：Sizeof/Offsetof 语义、字段排序与 Pointer 的六种合法模式

## 简介

`unsafe` 包只有 5 个函数、1 个类型，却是 Go 里唯一能"绕过类型系统读写任意内存"的口子。
它的难点不在 API，而在**两条容易被忽略的边界**：`Sizeof`/`Alignof`/`Offsetof` 报的是
**布局**而非"数据大小"（不含引用内存，且规范只保证最小对齐）；而 `unsafe.Pointer`
的转换**只有六种模式合法**——`uintptr` 不是引用，一旦存进变量，对象就可能被 GC
搬走，代码"今天能跑、明天就错"。

本 demo 把布局算法（含 GC 的 pointer bytes 与 size class 上取整）与六种模式的
合法性校验都做成可执行模型：

| 概念 | 一句话 |
| --- | --- |
| `Sizeof` | 只算类型本身，切片算 24 字节描述符、不含底层数组 |
| `Alignof` | 规范只给"最小对齐"；结构体取字段最大值、数组取元素值 |
| `Offsetof` | 参数必须是 `structValue.field` 形式，返回字段起始偏移 |
| pointer bytes | GC 需要扫描的**前缀**长度：`struct{string;uint32}` 是 8 而不是 24 |
| size class | 堆分配按 68 级尺寸上取整，`classSize(56) == 64` |
| 六种模式 | 文档点名的合法用法；`go vet` 只做启发式检查 |

## 原理详解

### 1. 规范只承诺"最小对齐"

规范 *Size and alignment guarantees* 一节给出的尺寸表是硬保证：

```text
byte/uint8/int8 = 1    uint16/int16 = 2          uint32/int32/float32 = 4
uint64/int64/float64/complex64 = 8               complex128 = 16
```

对齐则只是**下界**：

- `Alignof(x)` 至少为 1；
- 结构体 `x`：`Alignof(x)` 是各字段 `Alignof(x.f)` 的**最大值**，至少 1；
- 数组 `x`：等于元素的 `Alignof`。

另外两条容易漏：**空的 struct/array 尺寸为 0**，且"两个不同的零尺寸变量可以有相同的
地址"——这正是 Go 里 `&struct{}{} == &struct{}{}` 可能成立、但零尺寸字段放在结构体
**末尾**时必须被填 1 字节的原因（否则末字段会与下一个对象同址）。本 demo 的 D15 断言
验证了 `struct{int64; struct{}}` 尺寸是 **16** 而不是 8。

`Sizeof` 的语义要点是"不含任何被引用的内存"：切片 24（数据指针+长度+容量）、
字符串 16、接口 16、map/chan/func/指针各 8。

### 2. pointer bytes：GC 扫描前缀

`fieldalignment` 分析器的文档给了三个例子，直接把"布局"和"GC 成本"分开：

```text
struct { uint32; string }   → 16     struct { string; *uint32 } → 24
struct { string; uint32 }   →  8
```

三者尺寸都是 24，差别在 **GC 只需扫描"最后一个含指针字段的结束偏移"这一段前缀**。
把含指针的字段排在后面会白白扩大扫描范围：D17~D22 断言里
`struct{[100]byte; *int64}` 的 pointer bytes 是 **112**，重排后降到 **8** 而尺寸不变。
这也是字段排序有**两个**独立诊断的原因（尺寸浪费 / pointer bytes）。

### 3. 字段排序的五条优先级

`fieldalignment` 的 `optimalOrder` 逐条比较（顺序即优先级）：

1. **零尺寸字段排最前**（不占空间，放哪都不影响对齐）；
2. **对齐要求大的排前面**（减少填充）；
3. **含指针的排在无指针的前面**（缩小 GC 扫描前缀）；
4. 都含指针时，**尾随非指针字节少的**排前面（把非指针尾巴挤到指针区末尾）；
5. 最后按**尺寸降序**。

```text
struct{ A bool; B int64; C bool }              → 24 字节
重排为 { B int64; A bool; C bool }             → 16 字节
fieldalignment: "Tri has size 24 but the optimal size is 16 leading to a waste of 8 bytes (33%)"
```

### 4. size class：诊断里的第二个数字

分析器的诊断会带上 `(allocator size class N)` 后缀，因为堆分配是按 68 级
size class 上取整的（表见 `runtime/sizeclasses.go`，与同批次的
`内存分配器` demo 共用）：

```text
classSize(24) = 24              classSize(40) = 48
classSize(56) = 64              classSize(32768) = 32768
classSize(32769) = -1           # 超 maxSmallSize，走大对象分配
```

于是 `struct{bool; int64; [32]byte; bool}` 实际 56 字节、会被算作 size class 64，
重排到 48 字节只浪费 0 —— 这正是"最紧凑顺序不总是最省"的反面：尺寸浪费有时被
分配器对上取整放大。**唯一不加后缀的情形**是该侧尺寸本身就是一个 size class。

### 5. false sharing：最紧凑顺序的反例

分析器文档明确警告：*the most compact order is not always the most efficient. In rare
cases it may cause two variables each updated by its own goroutine to occupy the same CPU
cache line, inducing a form of memory contention known as "false sharing"*。

这就是"手动加填充"的正当理由：两个被不同 goroutine 高频更新的 `int64` 若相邻，
就落在同一条 64 字节 cache line 上，每次写入都会让对方的核心缓存失效。
E 组断言用偏移算术把这件事量化：偏移 0 与 8 同行，加 56 字节填充后 b 的偏移变成
64 —— 换行。

### 6. unsafe.Pointer：四种特殊操作 + 六种合法模式

文档写明 `Pointer` 有四种其它类型没有的操作：任意指针 → `Pointer`；`Pointer` → 任意
指针；`uintptr` → `Pointer`；`Pointer` → `uintptr`。**但"能转换"不等于"能随便转"**，
文档随后列出了必须是这六种之一的模式：

| # | 模式 | 要点 |
| --- | --- | --- |
| 1 | `*T1 → Pointer → *T2` | T2 **不得大于** T1，且布局等价 |
| 2 | `Pointer → uintptr` | 仅用于打印；**转回去一般不合法** |
| 3 | `Pointer ↔ uintptr` + 算术 | 两次转换必须在**同一表达式**内；结果必须仍在原对象内 |
| 4 | 传给 `syscall.Syscall` 之类的汇编实现函数 | 转换必须出现在**实参表**里 |
| 5 | `reflect.Value.Pointer/UnsafeAddr` 的结果立即转回 | 同上，不能先存变量 |
| 6 | `reflect.SliceHeader/StringHeader` 的 `Data` | 只能通过指向真实 slice/string 的指针使用 |

文档点名的 `// INVALID:` 反例全部可被本 demo 的校验器识别：

```text
u := uintptr(p); p = unsafe.Pointer(u + offset)      # uintptr 存进了变量
end = unsafe.Pointer(uintptr(unsafe.Pointer(&s)) + unsafe.Sizeof(s))  # 越界一个字节
p := unsafe.Pointer(uintptr(unsafe.Pointer(nil)) + offset)            # nil 不能做算术
u := uintptr(unsafe.Pointer(p)); syscall.Syscall(..., u, ...)         # 实参没就地转换
var hdr reflect.StringHeader; hdr.Data = uintptr(...)                 # 直接声明 header
```

`go vet` 能查一部分，但文档明说：*silence from "go vet" is not a guarantee that the
code is valid*。

### 7. 新增的辅助函数

`unsafe.Add`（1.17）等价 `Pointer(uintptr(ptr)+uintptr(len))`，`Slice`/`SliceData`/
`String`/`StringData`（1.20）把模式 3/6 包装成函数，但 panic 条件要记住：
`Slice(ptr, len)` 与 `String(ptr, len)` 在 `len < 0` 或 `ptr == nil && len != 0` 时 panic。

## 环境准备与运行

任意操作系统，Python 3.8+（实测 3.13.12）；模型按 **64 位平台**（字长 8、最大对齐 8）
计算。`go/` 目录需要 Go 工具链。

```bash
cd python && python3 main.py    # 99 项断言，退出码 0 表示全绿
cd go && go run .               # layout.go + patterns.go + main.go + asserts.go
```

## 关键代码片段

`python/layout.py` —— 结构体布局（对齐 + 尾部零尺寸字段填 1）：

```python
for i, (_name, ft) in enumerate(t.fields):
    a, sz = alignof(ft), sizeof(ft)
    mx = max(mx, a)
    if i == nf - 1 and sz == 0 and o != 0:
        sz = 1          # 尾部零尺寸字段占 1 字节，避免和下一对象同址
    o = align(o, a)
    fp = ptrdata(ft)
    if fp != 0:
        p = o + fp      # pointer bytes = 最后一个含指针字段的结束偏移
    o += sz
```

`python/unsafe_patterns.py` —— 模式 3 的两条硬约束：

```python
if origin == "temp" or stored:
    bad.append("uintptr 存进过变量再转回 Pointer")
if object_size is not None and not (0 <= offset < object_size):
    bad.append("结果必须仍指向原对象内部")
```

## 性能与边界

- **不做真实内存访问**：模型只算尺寸/偏移/合法性，不模拟 `uintptr` 的真实数值，
  因此不能替代 `go vet` / `-race` / `-asan`。
- **平台假设**：字长 8、最大对齐 8（amd64/arm64）。32 位平台下指针为 4、
  `Sizeof(slice) == 12`，本模型的常量需整体替换。
- **`ptrdata` 是"前缀上界"而非精确指针集合**：只有末尾的非指针尾部才会把它顶回来。
- **未覆盖**：`//go:notinheap`、cgo 类型、`typedmemmove` 与写屏障。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 以为 `Sizeof([]byte)` 是数据长度 | 只算 24 字节描述符 | 用 `len`/`cap` 算数据量 |
| 按 `Sizeof` 计算协议头 | 布局含填充，规范只保证最小对齐 | 用 `unsafe.Offsetof` 或手工序列化 |
| `u := uintptr(p); ...; unsafe.Pointer(u)` | uintptr 不是引用，GC 会搬对象 | 两次转换写在同一表达式里 |
| `&s + Sizeof(s)` 想拿"末尾指针" | 越界一个字节也是 INVALID | 只指向对象内部 |
| 把 `reflect.StringHeader` 当普通结构体声明 | 该字段只是"另一种说法"，不持有引用 | 只通过指向真实 string 的指针用 |
| 为省内存把字段排到最紧 | 可能引入 false sharing | 高频并发字段填充到不同 cache line |

## 参考资料（实际阅读过的权威来源）

- [`unsafe` 包文档](https://pkg.go.dev/unsafe) —— `Sizeof`/`Alignof`/`Offsetof` 的完整语义、`Pointer` 的四种特殊操作、**六种合法模式**与全部 `// INVALID:` 反例、`Add`/`Slice`/`SliceData`/`String`/`StringData` 的 panic 条件、"go vet 静默不等于合法"。
- [The Go Programming Language Specification — Size and alignment guarantees](https://go.dev/ref/spec#Size_and_alignment_guarantees) —— 数值类型尺寸表、`Alignof` 的三条保证、零尺寸变量可同址。
- [`golang.org/x/tools` 的 `fieldalignment` 分析器源码](https://raw.githubusercontent.com/golang/x-tools/master/go/analysis/passes/fieldalignment/fieldalignment.go) —— `gcSizes` 的 `sizeof`/`alignof`/`ptrdata` 实现、`optimalOrder` 的五条比较规则、pointer bytes 的三个例子、false sharing 警告、`classSize` 与诊断文本格式。
- [`go1.24.0 src/runtime/sizeclasses.go`](https://raw.githubusercontent.com/golang/go/go1.24.0/src/runtime/sizeclasses.go) —— 68 级 `class_to_size` 表（`classSize` 上取整的依据）。
