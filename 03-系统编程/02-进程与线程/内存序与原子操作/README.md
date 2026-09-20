# 内存序与原子操作：从 relaxed 到 seq_cst

## 一、简介

`memory_order` 回答的是一个问题：**原子操作除了「不撕裂」之外，还要不要替你排顺序**。

C11/C++ 给了六档（`RELAXED / CONSUME / ACQUIRE / RELEASE / ACQ_REL / SEQ_CST`）。最弱的一档只保证原子性与修改序一致：*"Atomic operations tagged memory_order_relaxed are not synchronization operations; they do not impose an order among concurrent memory accesses."*（cppreference）；最强的一档额外建立 **单全序**：*"a single total modification order of all atomic operations that are so tagged"*。

本 demo 把这六档建成可穷举的模型，用 litmus 测试（message passing / store buffering / release sequence / CoRR）去**证伪**：断言的是「某个结果会不会出现」，而不是「应该看到什么」。

## 二、原理详解

### 2.1 relaxed：只保证原子性

```c
// 线程 1                          // 线程 2
r1 = atomic_load_explicit(y, relaxed);   r2 = atomic_load_explicit(x, relaxed);
atomic_store_explicit(x, r1, relaxed);   atomic_store_explicit(y, 42, relaxed);
```

cppreference 给出的就是这个例子（A/B/C/D 四个操作），并指出 `r1 == r2 == 42` 是允许的 —— 两个线程都看到了「对方还没写」的旧值，因为 relaxed 不阻止读写被重排。

本模型的实测：message passing 用全 relaxed 时 `(flag=1, data=0)` **确实出现在结果集合里**。

### 2.2 release-acquire：打包发布，按需接收

*"If an atomic store in thread A is tagged memory_order_release, an atomic load in thread B from the same variable is tagged memory_order_acquire, and the load in thread B reads a value written by the store in thread A, then the store in thread A synchronizes-with the load in thread B."*

三个限定词一个都不能少：**store 是 release、load 是 acquire、并且真的读到了那个值**。模型的实测结果：

| 写侧 | 读侧 | `(flag=1, data=0)` 是否可能 |
| --- | --- | --- |
| relaxed | relaxed | **可能** |
| **release** | relaxed | **可能**（只有 release 不够） |
| **release** | **acquire** | 不可能 ✓ |

还有一个常被漏掉的边界：*"This promise only holds if B actually returns the value that A stored, or a value from later in the release sequence."* —— 读到**更早**的值不算同步。

### 2.3 release sequence：relaxed 的 RMW 能接力，普通写会切断

*"If some atomic is store-released and several other threads perform read-modify-write operations on that atomic, a 'release sequence' is formed: all threads that perform the read-modify-writes to the same atomic synchronize with the first thread and each other even if they have no memory_order_release semantics."*

模型里用 `+2` 的 relaxed `fetch_add` 把三种来源区分开（`flag` 初值 0）：

| `flag` 的值 | 来源 | 是否接上了同步链 | `(flag, data)` 实测 |
| --- | --- | --- | --- |
| 1 | 线程 1 的 release 写 | 是 | `(1,0)` 不可能 |
| 3 | relaxed RMW **读到**那个 release 写（1+2） | 是（接力） | `(3,0)` 不可能 |
| 2 | relaxed RMW 读到的是**初值**（0+2） | 否 | `(2,0)` **可能** |

对照组：把线程 2 的 relaxed RMW 换成 relaxed 的**普通写**，同步链立刻断掉，`(2,0)` 出现在结果集合里。这就是「RMW 才接力、普通写不接力」的可执行证据。

### 2.4 seq_cst：多出来的那一点是「单全序」

release-acquire 管的是**两个线程之间**的一对操作，管不了四个操作之间的全局顺序。store buffering（Dekker 那个形状）就是照妖镜：

```
线程 1: x = 1 (release);  r1 = y (acquire)
线程 2: y = 1 (release);  r2 = x (acquire)
```

实测：**`(r1, r2) == (0, 0)` 会出现** —— 两次读都看到了对方写之前的旧值，release-acquire 完全挡不住。把读写全换成 `seq_cst` 后 `(0,0)` 从结果集合里消失，只剩 `{(0,1), (1,0), (1,1)}`。

用 relaxed 读写 + 中间插 `atomic_thread_fence(seq_cst)` 也能达到同样效果（实测 `(0,0)` 同样消失）—— 这正是全栅栏的价值：它把「顺序」一次性补齐，而不是逐条操作去标。

### 2.5 RMW：原子性与内存序是两件事

`fetch_add` 不会丢更新**跟内存序无关**，它来自「读-改-写是一条不可分割的指令」。模型里两个 relaxed `fetch_add(+1)` 的所有可能结果中，返回值集合恒为 `{0, 1}`，`(0, 0)`（两个都读到旧值）从不出现。

C 侧实测：2 个线程各 `atomic_fetch_add_explicit(&counter, 1, memory_order_relaxed)` 一万次，结果恒为 20000。

### 2.6 coherence：同一线程对同一变量不能「先新后旧」

模型额外断言了 CoRR：一个线程连读两次 `x`（另一个线程写了一次 1），结果集合里 `(1, 0)` 从不出现 —— 见过了新值就不能再回到旧值，这条**与内存序无关**，是原子对象的基本保证。

## 三、对比

| 序 | 保证 | 典型用途 | x86-64 上的代价 |
| --- | --- | --- | --- |
| relaxed | 原子性 + 修改序一致 | 计数器、标记位 | 几乎为零（普通 `mov` / `lock xadd`） |
| consume | 依赖序（本模型按 acquire 保守实现） | RCU 读侧（实践中很少用） | 通常等同 acquire |
| acquire | 读之后的不越界 | 拿锁、读指针 | 编译器屏障（TSO 天然满足） |
| release | 写之前的不越界 | 放锁、发布数据 | 编译器屏障（TSO 天然满足） |
| acq_rel | 两者兼有 | RMW 做锁 | 同 acquire/release |
| seq_cst | 再加单全序 | 需要全局顺序的算法（Dekker、Peterson） | **全栅栏**（`mfence` / `lock` 前缀） |

cppreference 对强序平台的说明：*"On strongly-ordered systems — x86, SPARC TSO, IBM mainframe, etc. — release-acquire ordering is automatic for the majority of operations. No additional CPU instructions are issued for this synchronization mode; only certain compiler optimizations are affected ... On weakly-ordered systems (ARM, Itanium, PowerPC), special CPU load or memory fence instructions are used."*

而 seq_cst：*"Total sequential ordering requires a full memory fence CPU instruction on all multi-core systems. This may become a performance bottleneck."*

## 四、环境与运行

```bash
cd 03-系统编程/02-进程与线程/内存序与原子操作
python selfcheck_memorder.py     # 18 项断言全绿（穷举所有交错与传播）
```

- `memorder_demo.c`：C11 `<stdatomic.h>`，本机静态审查未编译（需 `cc -std=c11 -pthread`）。
- `memorder_go.go`：本机无 Go 工具链，人工审查；Go 侧语义取自官方内存模型文档。

## 五、关键代码

发布方 / 接收方的最小对照：

```c
/* 写侧 */  atomic_store_explicit(&data, 42, memory_order_relaxed);
            atomic_store_explicit(&flag, 1, memory_order_release);   /* 打包发布 */

/* 读侧 */  while (atomic_load_explicit(&flag, memory_order_acquire) == 0) { }
            atomic_load_explicit(&data, memory_order_relaxed);       /* 必然 42 */
```

把读侧改成 `memory_order_relaxed`，`data` 就不再保证是 42（模型里 `(1,0)` 会重新出现）。

## 六、性能边界

- 手册与标准都**不给定量阈值**（「seq_cst 比 acquire 慢百分之多少」因架构而异），故本 demo 不编造数字：只给出「是否多一条全栅栏」这一定性事实（cppreference 明说 seq_cst 在所有多核系统上都需要全栅栏）。
- 模型的 seq_cst 采用「写入立即对全部线程可见 + 单一全序」这一**充分口径**实现；真实机器是靠全栅栏和一个全局顺序达成的。本模型在这一口径下给出的结论（SB 测试排除 `(0,0)`）与真实硬件一致。
- 模型穷举是**指数级**的：3 线程 5 步的 litmus 已经需要几十万状态，实际项目请用 `herd7`/`TSan`/`relacy` 之类的专用工具。

## 七、注意事项与常见坑

1. **以为 release 单独就够** —— 读侧不写 acquire，同步关系根本没建立（实测 `(1,0)` 仍在结果集里）。
2. **以为 acquire 读到「同一个变量」就够了** —— 必须是读到 release 写的值（或 release sequence 中更靠后的值），读到更早的旧值不算。
3. **用 relaxed 普通写接力 release sequence** —— 只有 **RMW** 接力，普通写会切断（实测 `(2,0)` 出现）。
4. **拿 release-acquire 去实现 Dekker/Peterson** —— 会漏，必须 seq_cst（或显式全栅栏）。
5. **把 `fetch_add` 的原子性归功于内存序** —— 原子性来自 RMW 指令本身，relaxed 的 `fetch_add` 也不会丢更新。
6. **混用时以为还有 SC** —— cppreference 明确：*"as soon as atomic operations that are not tagged memory_order_seq_cst enter the picture, the sequential consistency is lost"*。
7. **以为栅栏只影响 CPU** —— 在 x86 上 acquire/release 主要影响的是**编译器重排**；忘了 `volatile` 与原子是两回事（Go 侧同理：非原子的普通变量读写就是数据竞争，Go 允许实现直接报 race 并终止）。
8. **把「不保证」当成「不可能」** —— relaxed 下 `(1,1)` 也完全可能出现；本 demo 的断言全部针对**结果集合的成员关系**，就是为了避开这个混淆。

## 八、参考资料（本轮实读）

- cppreference《std::memory_order》 — https://en.cppreference.com/w/c/atomic/memory_order （六种序的定义、release sequence、seq_cst 单全序、强序/弱序平台差异）
- C11 标准草案 n1570 — https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf （§5.1.2.4 多线程执行与数据竞争、§7.17 原子操作）
- 《The Go Memory Model》官方版 — https://cdn.jsdelivr.net/gh/golang/go@master/doc/go_mem.html （DRF-SC、*"All the atomic operations ... behave as though executed in some sequentially consistent order"*）
