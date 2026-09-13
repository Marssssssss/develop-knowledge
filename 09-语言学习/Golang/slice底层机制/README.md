# Slice 底层机制 — 三字头、growslice 与共享数组陷阱

> 讲清 slice 这个 Go 最常用引用类型的内部结构、增长算法,以及最易踩坑的共享底层数组语义。

## 简介

**Slice 是 Go 中最常用、最易踩坑的引用类型**。表面上它像"动态数组",但实质是**底层数组的一段视图**——ptr(指向元素 0) + len(可见长度) + cap(可扩展到哪)三字头组合。任何对切片的修改,只要写在共享底层数组的范围内,都会**透过所有切片反映出来**——这是大量 bug 的根源。

**关键概念**:
- **三字头**:`ptr`(指向底层数组某位置) + `len`(切片当前长度) + `cap`(从 ptr 起到底层数组末的长度)
- **`make([]T, len, cap)`** vs `new([]T)`:前者分配数组并切片,后者只分配切片头(nil slice)
- **`append` 触发 growslice**:cap 足够时不分配(共享底层数组),cap 不够时分配新数组并拷贝
- **切片表达式 `s[lo:hi:max]`**:三索引形式可限制 `cap(切片) = max-lo`,避免 append 时意外覆盖底层数组后续元素

**历史**:slice 与 channel 同期由 Newsqueak → Alef → Limbo → Go 演化而来,核心设计 30 年未变。

## 原理详解

### 1. 三字头结构

```
切片头 sliceHeader(24 字节,ptr+len+cap)
   │
   ▼
┌───────────────────────┐  ←─ ptr 指向底层数组某位置
│ ptr(*[N]T 或 内存地址) │
├───────────────────────┤
│ len   int              │  ←─ 可访问的 元素数
├───────────────────────┤
│ cap   int              │  ←─ 从 ptr 起可扩展的元素数
└───────────────────────┘
   │
   ▼
[底层数组,长度 ≥ cap]
```

零值是 `nil` slice(`ptr=nil, len=0, cap=0`)。`var s []int` 与 `s = nil` 是同一回事。

### 2. `make` 等价性

```go
make([]int, 50, 100)
new([100]int)[0:50]  // 完全等价
```

`make([]T, len, cap)` 一步完成"分配 cap 大小的数组 + 截取前 len 个元素"。`cap` 省略时 `cap == len`。

### 3. append 的 growslice 算法

当 `len == cap` 时,`append` 必须分配新数组:

```
旧 cap < 256:  新 cap = 旧 cap * 2          // 1 → 2 → 4 → 8 → ... → 256
旧 cap ≥ 256:  新 cap = 旧 cap * 1.25 + 192 // 256 → 512 → 832 → 1232 → ...
大对象额外对齐: 最终 cap 向上取整到内存页(指针大小对齐)
```

源码:`runtime/slice.go growslice`。**注意**:cap < 256 时 2 倍增长是 1.18 之前的算法;1.18+ 起改为带 192 偏移的 1.25x(防小 slice 反复 realloc)。**同时还按元素大小向上对齐**(防止过度分配,例如 1KB 元素时 256→1024→1280→1600 等)。

### 4. 共享底层数组陷阱

```go
a := []int{1,2,3,4,5}
b := a[1:3]    // b 共享 a 的底层数组
b[0] = 99      // → a[1] 也变 99, 因为 a, b 同底层数组
```

**防止意外的 three-index slice**:`s[lo:hi:max]` 让 cap=max-lo,即使 len < cap,append 时也会重新分配,**不覆盖底层数组 lo 之前或 max 之后的元素**。

### 5. 内置函数

| 函数 | 返回 | 说明 |
| --- | --- | --- |
| `len(s)` | int | 切片长度(可访问的元素数) |
| `cap(s)` | int | 切片容量(可扩展到的元素数) |
| `s[i]` | T | 索引访问(0..len(s)-1),越界 panic |
| `s[lo:hi]` | []T | 切片表达式, cap = cap(s)-lo |
| `s[lo:hi:max]` | []T | 三索引切片, cap = max-lo |
| `append(s, x...)` | []T | 追加元素, 可能分配 |
| `copy(dst, src)` | int | 复制 min(len(dst), len(src)) 个元素 |

## 对比 / 选型

| 维度 | Go slice | C 数组 | Python list | Java ArrayList |
| --- | --- | --- | --- | --- |
| 长度可变 | 是(append) | 否 | 是(append) | 是(add) |
| 共享底层 | 是(ptr+len+cap) | N/A(值类型) | 引用共享 | 引用共享 |
| 内存布局 | 连续 | 连续 | 指针数组 + 连续对象 | 连续 |
| 增长策略 | 1.18+ 1.25x+192 / <256 2x | N/A | 1 + 1/4 (overalloc) | 1.5x |
| 空零值 | nil | N/A | None | null |

## 环境准备

- 操作系统:任何支持 Go 的平台
- Go 版本:Go 1.21+(本 demo 用 1.18+ growslice 行为)
- 依赖:无第三方依赖

## 运行方式

```bash
cd 09-语言学习/Golang/slice底层机制/go
go run slice_mechanism.go
```

## 关键代码片段

```go
// 1) 三字头演示:ptr/len/cap 来自 runtime.sliceHeader
s := []int{10, 20, 30, 40, 50}
fmt.Printf("ptr=%p len=%d cap=%d\n", &s[0], len(s), cap(s))
// 2) append 触发分配:cap 不足时, 新 cap 按 growslice 公式计算
s2 := make([]int, 0, 3)
for i := 0; i < 10; i++ {
    s2 = append(s2, i)
    fmt.Printf("len=%d cap=%d ptr=%p\n", len(s2), cap(s2), &s2[0])
    // cap 翻倍: 3→6→12→24 ...; 地址变化说明分配了新数组
}
// 3) 共享底层数组陷阱
a := []int{1, 2, 3, 4, 5}
b := a[0:3]
b[0] = 99
fmt.Println(a[0]) // 99, 因为 a, b 同底层数组
// 4) 三索引切片防意外覆盖
big := []int{1, 2, 3, 4, 5, 6, 7, 8, 9}
view := big[2:5:5] // len=3, cap=3, 不共享 5 之后的元素
view = append(view, 100) // 分配新数组, 不影响 big[5..]
```

## 性能与边界

- **append 不分配**:cap 足够时仅修改 len 字段,~1 ns;**与分配差距 ~100x**
- **append 触发 growslice**:~100 ns/copy 一段,小对象主要开销在内存分配
- **`copy` 复制 n 个元素**:指针 + `memmove` ~5 ns/元素
- **`for range` 性能**:Go 1.22+ 之前 range 会复用同一变量(loopvar issue),1.22+ 修复
- **大对象 overhead**:`append([]int8{}, ...)` 每 1024 元素约 1 KB 内存;`append([]int64{}, ...)` 约 8 KB

## 注意事项与常见坑

1. **共享底层数组**:`sub := big[1:5]; sub = append(sub, x)` 可能覆盖 `big[5]`(若 cap > 4);用三索引 `big[1:5:5]` 切断
2. **循环变量复用**(Go < 1.22):`for _, v := range s { go func(){ fmt.Println(v) }() }` 所有 goroutine 看到同一个 `v`;**Go 1.22 修复**,每轮迭代独立
3. **`s[i:j:j]`** 与 `s[i:j]`:前者强制 cap=j-i,后者 cap 继承底层;文档与 Effective Go 都强调三索引用法
4. **空 vs nil**:`len(nil_slice) == 0` ✓;但 `nil_slice == nil` 成立,`empty_slice{}` 不为 nil;JSON 序列化时 `nil` → `null`,`[]T{}` → `[]`
5. **大结构体切片应传指针**:`[]MyStruct` 收发会复制每个 MyStruct;改用 `[]*MyStruct` 或 `[]MyStruct` + 索引读写
6. **append nil slice**:合法,会分配新数组并返回非 nil slice

## 参考资料

- [The Go Programming Language Specification — Slice types](https://go.dev/ref/spec#Slice_types) — 三字头、零值、make 等价性的形式化定义
- [runtime/slice.go growslice 源码](https://go.dev/src/runtime/slice.go) — Go 1.18+ 增长算法(1.25x+192)
- [Go Under The Hood — 5.1 Arrays and Slices (golang.design)](https://www.golang.design/under-the-hood/en/part1overview/ch05lang/05slice) — 详细讲解 growslice 几何增长与共享底层数组陷阱
- [Effective Go — Slices](https://go.dev/doc/effective_go#slices) — 切片惯用法
- [Go Blog — Go Slices: usage and internals](https://go.dev/blog/go-slices-usage-and-internals) — Andrew Gerrand 经典博客

---

> 文件清单:`slice_mechanism.go`(本 demo 一文件涵盖三字头/make/append/growslice/共享/三索引切片/`copy`/string-byte 转换 8 个示例),`go.mod`,`README.md`(本文)。