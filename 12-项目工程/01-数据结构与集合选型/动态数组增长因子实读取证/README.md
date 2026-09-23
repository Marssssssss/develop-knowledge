# 动态数组增长因子实读取证

> 「vector 满了就翻倍」这句话只对了一半：六家主流标准库里**只有四家是 2×**，
> MSVC 是 1.5×，CPython 是约 1.125× 且还带一条「宁可精确分配」的覆盖分支，
> Go 在 256 以上从 2× 平滑滑向 1.25×。本 demo 把六家的增长函数逐行扒出来，
> 用同一套驱动跑出容量序列、摊销搬移次数与峰值内存比，并给出「旧块能否复用」的闭式判据。

## 一、结论速览

| 实现 | 增长公式 | 序列开头 | 扩容次数¹ | 摊销搬移 | 峰值内存比 |
|---|---|---|---|---|---|
| libstdc++ `vector` | `size() + max(n, size())` | 1,2,4,8,16 | 10 | 1.02×N | 1.500× |
| libc++ `vector` | `max(2*cap, new_size)` | 1,2,4,8,16 | 10 | 1.02×N | 1.500× |
| MSVC STL `vector` | `cap + cap/2` | 1,2,3,4,6,9,13 | 17 | 2.14×N | 1.750× |
| Go `slice` | <256 翻倍，之后 `+1/4` | 1,2,…,256,512,832 | 11 | 1.85×N | 1.675× |
| CPython `list` | `(n + n>>3 + 6) & ~3` | 4,8,16,24,32,40 | 27 | 7.56×N | 1.885× |
| Rust `Vec` | `max(cap*2, required)` | 4,8,16,32 | 8 | 1.02×N | 1.500× |

¹ 连续 append 1000 次；峰值内存比 = 扩容瞬间 (旧块 + 新块) / 新块。

两个反直觉的点：

- **CPython 扩容次数最多（27 次）却不是最差的**：它每次只多要 1/8，搬家搬得勤但每次搬得少，
  峰值内存比 1.885× 是六家里最高——省内存和少搬家不可兼得。
- **1.5× 的峰值内存比反而比 2× 高**：峰值比是相对**新块**算的，
  `r=2` 给 `(1+2)/2 = 1.5`，`r=1.5` 给 `(1+1.5)/1.5 ≈ 1.667`（实测 MSVC 1.750，小容量整数截断所致）。
  所以选 1.5× 的动机**不是**压峰值，而是**旧块复用**（见 §四）——2× 在数学上永远复用不了。

## 二、逐家源码级事实

### libstdc++ —— `size() + max(n, size())`

`bits/stl_vector.h:2272`：

```cpp
const size_type __room = max_size() - size();
if (__room < __n)  __throw_length_error(__N(__s));
if (__n < size())  __n = size();  // Grow by (at least) doubling ...
if (__n > __room)  __n = __room;  //  ... but only as much as will fit.
return size() + __n;
```

关键是 `if (__n < size()) __n = size();`：`push_back` 一个元素时 `__n == 1 < size()`，
于是退化成 `2 * size()`；而 `insert` 一大批时 `__n` 很大，就是**精确分配**不超额。

### libc++ —— `max(2 * cap, new_size)`

`include/__vector/vector.h:880`：

```cpp
const size_type __ms = max_size();
if (__new_size > __ms)  this->__throw_length_error();
const size_type __cap = capacity();
if (__cap >= __ms / 2)  return __ms;
return std::max<size_type>(2 * __cap, __new_size);
```

与 libstdc++ 殊途同归，但写法更直接：`max` 里的 `2 * __cap` 保证指数增长，
`__new_size` 保证一次性插入大量元素时够用。

### MSVC STL —— `cap + cap / 2`

`stl/inc/vector:2014`：

```cpp
if (_Oldcapacity > _Max - _Oldcapacity / 2)  return _Max; // 溢出
const size_type _Geometric = _Oldcapacity + _Oldcapacity / 2;
if (_Geometric < _Newsize)  return _Newsize;              // 不够就精确给
return _Geometric;
```

整数除法向下取整 ⇒ 小容量处实际是 `1,2,3,4,6,9,13,19,28,42,…`。

### Go —— 256 为界，从 2× 滑向 1.25×

`runtime/slice.go:326`：

```go
const threshold = 256
if oldCap < threshold { return doublecap }
for {
    newcap += (newcap + 3*threshold) >> 2
    if uint(newcap) >= uint(newLen) { break }
}
```

`+3*threshold` 这一项是「平滑过渡」的手艺：没有它，256 之后立刻掉到 1.25× 会显得很突兀。
实测序列 `256 → 512 → 832 → 1232 → 1732`，比值 `2.0, 1.625, 1.481, 1.406, 1.361` 缓慢收敛到 1.25。
（溢出时 `newcap <= 0`，直接退回 `newLen`。）

### CPython —— 1/8 超额 + 4 字节对齐 + 覆盖分支

`Objects/listobject.c:129`：

```c
new_allocated = ((size_t)newsize + (newsize >> 3) + 6) & ~(size_t)3;
if (newsize - Py_SIZE(self) > (Py_ssize_t)(new_allocated - newsize))
    new_allocated = ((size_t)newsize + 3) & ~(size_t)3;
if (newsize == 0)  new_allocated = 0;
```

注释里给出的序列是 `0, 4, 8, 16, 24, 32, 40, 52, 64, 76, …`——
本 demo 的 `append_sequence("CPython", 2000)` 复现出**完全一致**的序列，可当作转写正确性的旁证。

三条容易漏的规则：

1. **旁路**：`allocated >= newsize && newsize >= allocated>>1` 时**完全不动**，
   这既是增长的短路，也是「缩到一半以下才真缩」的判据；
2. **覆盖分支**：若「这次增长的量」比「超额分配出来的量」还大，就改成精确分配
   （`+3 & ~3`，只做 4 字节对齐），避免一次大批量 extend 之后长期占着虚高的块；
3. `newsize == 0` 时归零。

因此 CPython 必须在**每次** append 都调用 `list_resize`（内部自带判据），
而其他实现只在真的不够时才调 —— 本 demo 用 `ALWAYS_CALL = {"CPython"}` 表达这个差异。

### Rust —— `max(cap*2, required)` 再过 `min_non_zero_cap`

`library/alloc/src/raw_vec/mod.rs:545`：

```rust
let cap = cmp::max(self.cap.as_inner() * 2, required_cap);
let cap = cmp::max(min_non_zero_cap(elem_layout.size()), cap);
```

`min_non_zero_cap`（同文件 `:168`）按元素大小给下限：`size == 1 → 8`，`<= 1024 → 4`，否则 `1`。
所以 `Vec<u64>` 首个容量不是 1 而是 **4**：`max(0*2, 1) = 1` 再被 `min_non_zero_cap(8) = 4` 顶上去。
这是纯粹的内存分配器友好性考虑，与增长因子无关。

## 三、统一驱动与实测

`append_sequence(name, count)` 从空容器开始连续 append，返回每次操作后的容量。
配套三个指标：

- `growth_points` —— 第几次 append 触发扩容、旧容量、新容量；
- `amortized_moves` —— 扩容时搬过的元素总数（搬的是**扩容前已有**的元素，不是新容量）；
- `peak_memory_ratio` —— 扩容瞬间新旧两块同时在世，`(旧 + 新) / 新` 的最大值。

## 四、旧块复用：为什么 2× 是「最坏」的因子

设增长因子 `r`，容量序列 `s_{i+1} = r · s_i`。第 `i` 次扩容时，此前释放的所有块之和为
`Σ_{j<i} s_j`，新块是 `s_i`。复用条件是：

```
Σ_{j<i} s_j >= s_i
(r^i - 1)/(r - 1) >= r^i
r^i (2 - r) >= 1          ← 整理后的闭式判据
```

- `r = 2` 时左边恒为 `0`，**倍增策略在数学上永远无法复用旧块**（这是 2× 的真正代价）；
- `r^2 (2-r) = 1` 的正根恰好是**黄金比例 φ ≈ 1.618** —— 因式分解
  `r^2(2-r) - 1 = -(r-1)(r^2 - r - 1)` 说明根就是 `r^2 - r - 1 = 0`；
  也就是说「第 2 次扩容就能复用」的最大倍数是 φ；
- 模型给的实际代次：`1.1×/1.25×/1.5× → 第 2 次`，`1.7×/1.8× → 第 3 次`，`1.9× → 第 4 次`，`2.0× → 永不`。

**取整只会让复用更晚**：真实标准库走整数运算，`ceil` 把新块抬高，
于是 `1.9×` 从「第 4 次」推迟到「第 5 次」、`1.8×` 从「第 3 次」推迟到「第 4 次」。
`reuse_generation` 用 `Fraction` 精确推演（float 在 `i > 53` 之后会让 `s_0+…+s_{i-1}` 与 `s_i` 相等，
把「永不复用」误判成「能复用」），`reuse_generation_ceil` 才是标准库的真实行为。

## 五、运行

```bash
cd python && python main.py            # 六家并排对比 + 复用判据
cd python && python selfcheck_growth_factor.py   # 152 条断言
```

Go 侧 `go/` 是同一套模型的转写（`go/growth_factor.go` + `go/main.go`）。

## 六、参考资料（均为实读）

- libstdc++ `bits/stl_vector.h`（`_M_check_len`）：
  <https://raw.githubusercontent.com/gcc-mirror/gcc/master/libstdc%2B%2B-v3/include/bits/stl_vector.h>
- libc++ `include/__vector/vector.h`（`__recommend`）：
  <https://raw.githubusercontent.com/llvm/llvm-project/main/libcxx/include/__vector/vector.h>
- MSVC STL `stl/inc/vector`（`_Calculate_growth`）：
  <https://github.com/microsoft/STL/blob/main/stl/inc/vector>
- Go `runtime/slice.go`（`nextslicecap`）：
  <https://raw.githubusercontent.com/golang/go/master/src/runtime/slice.go>
- CPython `Objects/listobject.c`（`list_resize`）：
  <https://raw.githubusercontent.com/python/cpython/main/Objects/listobject.c>
- Rust `library/alloc/src/raw_vec/mod.rs`（`grow_amortized` / `min_non_zero_cap`）：
  <https://github.com/rust-lang/rust/blob/master/library/alloc/src/raw_vec/mod.rs>

行号取自抓取当日的 `master` / `main` 分支，可能随上游提交漂移。
