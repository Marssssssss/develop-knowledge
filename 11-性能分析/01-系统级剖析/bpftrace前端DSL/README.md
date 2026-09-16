# bpftrace 前端 DSL 与一行式追踪

## 简介

**bpftrace** 是 Linux 上的 eBPF 高级追踪前端：把「探针 + 谓词 + 动作」压成一门小型 DSL，一条命令行就能完成原本要写 C + Python 双语言（bcc 风格）的活。它由 Brendan Gregg 等人推动，语法血统来自 DTrace 的 awk 风格（`probe /filter/ { action }`），是 `bcc` 之上的第二层封装。

**关键概念**

- **探针（probe）**：插桩点。`provider:option1:option2`，如 `kprobe:vfs_read`、`tracepoint:syscalls:sys_enter_openat`、`profile:hz:99`
- **谓词（predicate/filter）**：`/.../` 包裹的布尔表达式，探针照样触发但为假时跳过动作
- **map**：`@name[key]`，底层是 BPF map，程序退出时自动打印；对照 `$name` 是存 BPF 栈的 scratch 变量，**词法块外不可访问**
- **聚合函数**：`count()/sum()/avg()/min()/max()/stats()/hist()/lhist()`，写在赋值右侧时由内核增量维护，无需把每条事件搬到用户态
- **一行式**：`-e 'program'`，`bpftrace -l 'tracepoint:syscalls:sys_enter_*'` 列出探针

**为什么它重要**：eBPF 的原始形态是受限的 64 位字节码（见 `01-系统级剖析/eBPF动态追踪/`），写裸字节码不现实。bpftrace 把「读哪个探针、取哪个内置变量、按什么方式聚合」变成声明式，让**一行式**成为可能。

## 原理详解

### 1. 程序结构

```
[name=]probe[,probe] /predicate/ { action }
```

多个探针可逗号并列（动作须对全部探针合法），也可用通配符 `kprobe:tcp_*`；provider 有短名，`k:f` 等价 `kprobe:f`。

### 2. 探针类型（官方 language.md 的完整表）

| 探针 | 短名 | 说明 |
| --- | --- | --- |
| `tracepoint` | `t` | 内核静态追踪点（API 稳定，优先用） |
| `kprobe` / `kretprobe` | `k` / `kr` | 内核函数入口 / 返回（动态追踪，不稳定 API） |
| `uprobe` / `uretprobe` | `u` / `ur` | 用户态函数入口 / 返回 |
| `usdt` | `U` | 用户态静态追踪点 |
| `profile` | `p` | 全 CPU 定时采样，`profile:hz:99` |
| `interval` | `i` | 单 CPU 定时输出，`interval:s:5` |
| `software` / `hardware` | `s` / `h` | 预定义软件 / 硬件（PMU）事件 |
| `fentry` / `fexit` | `f` / `fr` | 带 BTF 的内核函数追踪 |
| `rawtracepoint` | `rt` | 静态追踪点的裸参数版 |
| `watchpoint` | `w` | 内存监视点 |
| `iter` | `it` | 遍历内核对象（`iter:task` 等） |
| `begin` / `end` | — | 内置事件：全部探针挂载前 / 卸载后各跑一次 |

### 3. 内置变量与聚合

`pid` `tid` `uid` `username` `comm` `curtask` `nsecs`（自开机纳秒，做时间差用）`elapsed` `arg0..argN` `args`（tracepoint 参数结构）`retval` `func` `probe` `$1..$N` `$#` `cgroup`；栈用 `kstack` / `ustack`。

聚合函数中 `hist(int64 n[, int k])` 是 **log2 直方图**，`k` 表示「每个 2 的幂区间再细分成 2^k 个桶」（`0 ≤ k ≤ 5`，默认 0）；`lhist(int64 n, min, max, step)` 是**线性直方图**，在 `[min,max)` 上开 `(max-min)/step` 个桶，`(-inf,min)` 与 `(max,+inf)` 各占一个额外桶，**总桶数 M+2**。

### 4. 输出格式（本 demo 复刻的部分）

`hist()` 的分桶规则：`0` 与 `1` 共用首桶（所以首行标签写的是 `[0, 1]`，**右括号是方括号**），此后 `[2,4)` `[4,8)` `[8,16)` … 左闭右开；桶边界 ≥1024 起改用 `k`/`M`/`G` 刻度。每行布局为

```
<标签左对齐 15 列><计数右对齐 9 列> |<柱区 52 列>|
```

柱长按「本行计数 / 最大桶计数 × 52」取整，非零计数至少 1 个 `@`；只打印到最后一个非空桶，中间空桶保留为 0 行。

### 5. 一行式的经典例子：syscall 延迟

```
tracepoint:syscalls:sys_enter_* /comm == "app"/ { @start[tid] = nsecs; }
tracepoint:syscalls:sys_exit_*  /comm == "app" && @start[tid]/
    { @ns[comm] = hist(nsecs - @start[tid]); delete(@start, tid); }
```

要点：① 用 `tid` 作键——同一时刻一个线程只能在一个 syscall 里，故 `tid` 天然是唯一标识；② 第二个块的谓词必须带 `@start[tid]`，否则程序若在某次 syscall **中途**启动，只会抓到 exit 而算成 `now - 0`；③ `delete()` 及时释放，否则 `@start` 无界增长。`delete(@map, key)` 的写法是 **map 不写方括号**、键作为独立实参。

## 对比

| 维度 | bpftrace | bcc | 裸 libbpf |
| --- | --- | --- | --- |
| 编程模型 | 单一 DSL，一行式 | C（内核态）+ Python（用户态） | C 为主 |
| 上手成本 | 低 | 中 | 高 |
| 聚合位置 | 内核态增量聚合 | 内核态聚合，用户态加工 | 手写 |
| 表达能力上限 | 无循环（可用 `unroll`）、无自定义 struct | 完整 C | 完整 |
| 稳定 API | 依赖探针类型（kprobe 不稳定） | 同 | 同 |
| 典型场景 | 快速定性、生产热修 | 复杂分析工具 | 自研可观测产品 |

## 环境准备

- 操作系统：Linux（内核 ≥ 4.x 且开启 `CONFIG_BPF`/`CONFIG_BPF_SYSCALL`；`tracepoint` 类探针无需 BTF）
- 语言：Python 3.10+（本 demo 自检）；Go 1.21+；gcc/clang（C 版）
- 依赖：**无第三方库**（Python/Go/C 均只用标准库 / libc）

## 运行方式

### Python（含 30 项自检，可直接跑）

```bash
python mini_bpftrace.py
```

### Go

```bash
go run .
```

### C

```bash
gcc -O2 -Wall -Wextra -pedantic mini_bpftrace.c -o mini_bpftrace
./mini_bpftrace
```

### 在真机上跑原生 bpftrace

```bash
bpftrace -l 'tracepoint:syscalls:sys_enter_*'          # 列探针
bpftrace -e 'tracepoint:raw_syscalls:sys_enter { @[comm] = count(); }'
bpftrace -e 'kretprobe:vfs_read { @bytes = lhist(retval, 0, 2000, 200); }'
```

## 关键代码片段

分桶与标签（`hist()` 的语义核心，三语言同口径）：

```python
def hist_index(v: int) -> int:
    """0/1 -> 0 ; 2..3 -> 1 ; 4..7 -> 2 ; 8..15 -> 3 …"""
    return 0 if v < 2 else v.bit_length() - 1

def fmt_bucket(n: int) -> str:
    for shift, suf in ((30, "G"), (20, "M"), (10, "k")):
        if n >= (1 << shift):
            return f"{n >> shift}{suf}"
    return str(n)

def hist_label(i: int) -> str:
    return "[0, 1]" if i == 0 else f"[{fmt_bucket(1 << i)}, {fmt_bucket(1 << (i + 1))})"
```

渲染一行（列宽 15/9/52 与官方样例逐字符对齐）：

```python
def render_rows(rows) -> list[str]:
    maxc = max(c for _, c in rows) or 1
    return [f"{label:<15}{c:>9} |{('@' * max(1, c * BAR_WIDTH // maxc) if c else 0):<{BAR_WIDTH}}|"
            for label, c in rows]
```

配对与释放（复刻 Lesson 7 的一行式）：

```python
tracepoint:syscalls:sys_exit_* /comm == "app" && @start[tid]/
  { @ns[comm] = hist(nsecs - @start[tid]); delete(@start, tid); }
```

## 性能与边界

- **聚合在内核态完成**：`hist()` 每次事件只做「算桶号 + 原子自增」，把 O(N) 事件压成 O(桶数) 输出，这是 bpftrace 能长时间低开销跑在生产上的根本原因。
- **开销量级**：`profile:hz:99` 取 99 而非 100 Hz，正是为了避开与其它 100 Hz 定时活动的**步调锁定**（同频采样会系统性偏袒某些代码路径）。
- **规模上限**：map 容量有限（超出会丢键）；`delete()` 不做会让常驻 map 无界增长。
- 本 demo 的**边界**：`hist()` 只实现 `k=0`（`k>0` 的 2^k 细分未实现，代码里显式 `raise`）；`lhist()` 未被 `k` 参数影响；`if/else`、`unroll`、元组键、字符串函数（`str()`/`buf()`）未实现——都是「前端 DSL 的解析层」而非「分桶/渲染层」的事。

## 注意事项与常见坑

- **谓词必须防未配对的返回事件**：`/@start[tid]/` 不是可选项。漏了它，程序在 syscall 中途启动时会算出一个 `now - 0` 的巨值把直方图右端拉爆（官方 tutorial Lesson 7 专门强调）。
- **`delete(@map, key)` 的键不写方括号**：写成 `delete(@start[tid])` 是错的（要么报错，要么删错键）。本 demo 的 Go/Python 实现初版就把它解析成了空键 `@start[]`，导致 15 次删除全部落空、`@start[tid]` 残留——这正是「实现看不出错、只有实跑断言能抓到」的典型。
- **`begin`/`end` 的大小写**：官方 `language.md` 用小写 `begin/end`，cheat sheet 与 man page 用大写 `BEGIN/END`。两者都能跑；本 demo 一律归一成小写内部名。
- **输出列宽是「反推」出来的**：官方文档只给样例输出，没写列宽公式。本 demo 的 15/9/52 三个数字是从 tutorial 与 man page 的样例**逐字符比对反推**的——**存在口径差异**，换 bpftrace 版本可能变。
- **`END` 不覆盖非空 map 的自动打印**：想在退出前自己收尾，得在 `END` 里 `clear(@map)`，否则同一份数据会被打印两次。
- **`sched_switch` 上 `comm`/`kstack` 指「正要离开 CPU 的线程」**，不是被唤醒的那个；探针上下文与直觉不一致是本类脚本最常见的错源。

## 参考资料（实际阅读过的权威来源）

- [bpftrace Reference Guide — 官方仓库 docs/language.md](https://raw.githubusercontent.com/bpftrace/bpftrace/master/docs/language.md) — 探针类型全表、短名、谓词、`@`/`$` 语义、`begin`/`end` 定义
- [bpftrace One-Liner Tutorial（Brendan Gregg, 2018）](https://raw.githubusercontent.com/bpftrace/bpftrace/master/docs/tutorial_one_liners.md) — 12 课一行式；`hist()`/`lhist()` 的**逐字符样例输出**与 `nsecs` 时间差用法（本 demo 分桶规则与列宽的唯一依据）
- [bpftrace(8) man page（Ubuntu noble）](https://manpages.ubuntu.com/manpages/noble/en/man8/bpftrace.8.html) — `hist(int64 n[,int k])` 的 k 定义原文、`lhist` 的 M+2 桶原文、`stats()` 输出格式
- [bpftrace man/adoc/bpftrace.adoc](https://github.com/bpftrace/bpftrace/blob/6dd6fbda74959ab85fa69fa2a21fcc7b94ffac7a/man/adoc/bpftrace.adoc) — `END`/`clear()` 关系原文、探针并列与通配符说明
- [bpftrace Cheat Sheet（Brendan Gregg）](https://www.brendangregg.com/BPF/bpftrace-cheat-sheet.html) — 内置变量表与 map 函数一览（与 language.md 交叉验证）
- 配套书：《BPF Performance Tools》第 5 章（bpftrace 编程）
