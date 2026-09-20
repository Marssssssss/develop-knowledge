# py-spy `--native`：原生扩展的采样与符号化

> Python 层采样只沿 `PyFrameObject` 的 back 链走，**C/C++/Cython 里烧掉的 CPU 整段看不见**。
> `--native` 把原生帧并进调用栈，但随之而来的是符号化问题：
> 一个 Cython 函数到底是哪个 `.pyx` 的第几行？

## 一、不开 `--native` 会漏掉什么

py-spy 读目标进程内存重建 Python 栈（不重启、不改目标）。栈重建链路是
`PyInterpreterState → 各线程 → PyFrameObject.back`。这条链**只覆盖 Python 帧**：

```text
真实执行:  main(app.py:10) → worker(app.py:42) → __pyx_f_4fast_compute(fast.c) → ...
不开 --native 看到的:  main ← worker        （Cython 的时间全算进 worker 的自身耗时）
开   --native 看到的:  main ← worker ← __pyx_f_4fast_compute ← ...
```

所以「Python 代码明明很简单却很慢」的典型原因就是：热点在原生扩展里，而你没开 `--native`。

## 二、平台与符号前提

`--native` **不是全平台可用**（README 的支持表，空白格 = 不支持）：

| 架构 | Linux | Windows | OSX | FreeBSD |
| --- | --- | --- | --- | --- |
| i686 | — | — | — | — |
| x86-64 | ✅ | ✅ | — | — |
| ARM | ✅ | — | — | — |
| Aarch64 | ✅ | — | — | — |

另外两条官方提醒：

1. **最好带符号编译**扩展模块，否则函数名/行号拿不全。
2. 对 **Cython**，py-spy 需要 **生成的 C/C++ 文件** 才能把行号还原到原始 `.pyx`。

## 三、Cython 行号还原：靠生成的 C 文件里的注释标记

Cython 生成的 C 文件里会插这样的行：

```c
/* "fast.pyx":10
static int __pyx_f_4fast_compute(void) {
```

py-spy 用正则 `^\s*/\* "(.+\..+)":([0-9]+)` 扫一遍，建一张
`生成 C 文件的行号 → (.pyx 文件, .pyx 行号)` 的表（`src/cython.rs` 的 `SourceMap`）。

三个容易弄错的细节（demo 里逐条断言）：

1. **键是 0-based 行下标**。Rust 的 `enumerate()` 从 0 开始，而帧里的 `line` 是 1-based。
2. **查找是「严格小于」**：`range(..lineno).next_back()`。因为键是 0-based，
   标记在第 N 行（键 N−1）对「第 N 行及之后」都生效——**标记行自身包含在内**。
3. **EOF 哨兵的位置比想象中靠后**：哨兵插在 `line_count + 1`，而 `range` 是右开区间，
   所以 `lineno == line_count + 1` 时哨兵**自己被排除**，要 `lineno ≥ line_count + 2`
   才会读到哨兵并返回 `None`。

## 四、demangle：从 `__pyx_pf_8implicit_4_als_30_least_squares_cg` 到人话

Cython 的符号名是 `_<长度><名字>` 逐段拼起来的（模块 / 文件 / 类 / 函数）。
`demangle` 先切掉前缀（`__pyx_pf` / `__pyx_pw` / `__pyx_f` / `___pyx_f` / `___pyx_pw` /
fuse 变体），再循环剥「下划线 + 十进制长度」。循环里有一条**保护**：

```rust
if digits + digit_index >= current.len() { break; }
```

长度字段超过剩余字符串就停下——防止把函数名本身的长度数字再当成一段。
`src/cython.rs` 的单元测试给了四条向量（demo 直接拿来当断言）：

| 输入 | 输出 |
| --- | --- |
| `__pyx_pf_8implicit_4_als_30_least_squares_cg` | `_least_squares_cg` |
| `__pyx_pw_8implicit_4_als_5least_squares_cg` | `least_squares_cg` |
| `__pyx_fuse_1_0__pyx_pw_8implicit_4_als_31_least_squares_cg` | `_least_squares_cg` |
| `__pyx_f_6mtrand_cont0_array` | `mtrand_cont0_array` |

源码注释还坦白了一处不足：`..._4_als_30_...` 里的模块名 `_als` 本来也该剥掉，但
「区分模块段和函数名段很 tricky」，所以没剥。

另外有一份**调用胶水忽略名单**：`__Pyx_PyFunction_FastCallDict`、`__Pyx_PyObject_CallOneArg`、
`__Pyx_PyObject_Call`、`__pyx_FusedFunction_call`——它们不是用户代码，不该出现在栈顶。

## 五、行号还原失败时的表现

`SourceMap::lookup` 返回 `None` 时，帧保持 C 文件的名字与行号。
demo 里构造了「C 帧行号 900 落在标记范围之外」的情形，结果就是**拿不到 `.pyx` 行号**——
这正是「没带符号 / 没留生成的 C 文件」时的真实观感：函数名还在，行号是 C 文件的。

## 六、运行

```bash
python python/pyspy_native.py   # 33 条断言
cd go && go run .                # 同语义 Go 版（本机无工具链，人工审查）
```

## 七、注意事项与常见坑

1. **macOS 上没有 `--native`**（支持表是空的），别在 Mac 上奇怪为什么没原生帧。
2. Cython 项目要把**生成的 C 文件留在构建产物里**，否则行号还原直接降级。
3. **0-based / 1-based 混用**会造成差一行的映射错位——py-spy 自己靠 `range(..lineno).next_back()`
   把两者对齐，自己写还原逻辑时最容易在这里翻车。
4. 栈顶看到 `__pyx_...` 且没被 demangle，说明符号名不在已知前缀表里（通常是 C++ 名字改编），
   需要先过一层 C++ demangler。
5. `--native` 采样的是**整条机器栈**，Python 帧与原生帧会交错；
   读火焰图时注意 `.pyx` 行号来自标记表，不是真实机器 PC。

## 参考资料（本轮实际读过）

- [benfred/py-spy — README（`--native` 支持表与前提）](https://raw.githubusercontent.com/benfred/py-spy/master/README.md)
- [benfred/py-spy — src/cython.rs（SourceMap / demangle / ignore_frame）](https://raw.githubusercontent.com/benfred/py-spy/master/src/cython.rs)
- [Ben Frederickson — Profiling native Python extensions with py-spy](https://www.benfrederickson.com/profiling-native-python-extensions-with-py-spy/)
