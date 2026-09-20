# futex：用户态快路径 + 内核慢路径

## 一、简介

futex（fast user-space mutex）是 Linux 提供的**最小同步原语**，不是给应用直接用的锁，而是给 `pthread_mutex` / 条件变量 / 信号量打底的那块砖（`futex(7)` 原文：*"lend themselves well for building higher-level locking abstractions such as mutexes, condition variables, read-write locks, barriers, and semaphores"*；*"Most programmers will in fact not be using futexes directly"*）。

它解决的问题很具体：**锁的状态放在共享内存里，无竞争时一次系统调用都不做；只有在真的要睡的时候才进内核**。`futex(2)` 的说法是 *"The majority of the synchronization operations are performed in user space"*，内核 *"maintains no information about the lock state"*。

本 demo 把 futex 的**内核侧语义**建成了可执行模型，并把手册页与内核源码里的位编码逐条对拍。

## 二、原理详解

### 2.1 futex word 就是 4 个字节

*"A futex is a 32-bit value—referred to below as a futex word ... Futexes are 32 bits in size on all platforms, including 64-bit systems"*，且必须 4 字节对齐（不对齐 `EINVAL`）。两个进程可以把它放在共享映射里，**虚拟地址不同但物理页相同**，内核按 key 匹配。模型里 `FutexWord.set()` 一律 `& 0xFFFFFFFF`，所以 `-1` 存进去是 `0xFFFFFFFF`。

### 2.2 FUTEX_WAIT 是「原子的比较-并-阻塞」

这是 futex 全部正确性的支点。`futex(2)`：*"blocking via a futex is an atomic compare-and-block operation"*；`FUTEX_WAIT(2const)`：*"If the futex value does not match val, then the call fails immediately with the error EAGAIN"*，「比较」的**唯一目的**是 *"to prevent lost wake-ups"*。

丢失唤醒长什么样？等待者在用户态读到 `1`（锁被持有）准备睡下，就在进内核之前，持有者已经解锁并 `FUTEX_WAKE` 了。若 `FUTEX_WAIT` 不比较，这个等待者会睡进一个再也不会有人唤醒的队列。自检里做了对照：

| 实现 | 结果 |
| --- | --- |
| `futex_wait(w, 1, tid)`（带比较） | 立即 `EAGAIN`，未入队（`pending == 0`） |
| `futex_wait_naive(w, tid)`（不带比较） | 睡下且 `woken == False`，永远留在队列里 |

### 2.3 FUTEX_WAKE 返回的是「实际唤醒数」

不是剩余等待者数：3 个等待者下 `WAKE(1)` 返回 1、`WAKE(5)` 返回 2、`WAKE(5)` 再调返回 0。

另外 `FUTEX_WAIT(2const)` 明确提醒：返回 0 **也可能是假唤醒**（*"a wake-up can also be caused by common futex usage patterns in unrelated code that happened to have previously used the futex word's memory location"*），所以调用方 *"should always conservatively assume that a return value of 0 can mean a spurious wake-up, and use the futex word's value ... to decide whether to continue to block"*。正确写法永远是**回环**：`cmpxchg` 失败 → `FUTEX_WAIT` → 醒来再 `cmpxchg`。

### 2.4 超时：FUTEX_WAIT 是相对值，其它操作是绝对值

`FUTEX_WAIT(2const)` 的 CAVEATS：*"timeout is interpreted as a relative value. This differs from other futex operations, where timeout is interpreted as an absolute value."* 想用绝对 deadline 等待，得改用 `FUTEX_WAIT_BITSET` 并把 `val3` 设为 `FUTEX_BITSET_MATCH_ANY`。自检里两个口径各验一次（相对 5 在 `now=4` 不超时、`now=5` 超时；绝对 5 在 `now=10` 时立即 `ETIMEDOUT`）。

### 2.5 FUTEX_WAKE_OP：一次原子操作 + 两处唤醒

`uapi/linux/futex.h` 的注释给出语义：

```c
int oldval = *(int *)UADDR2;
*(int *)UADDR2 = oldval OP OPARG;
if (oldval CMP CMPARG) wake UADDR2;
```

真正容易写错的是两点（都取自 `kernel/futex/waitwake.c` 的 `futex_wake_op()`）：

1. **uaddr1 上的 `nwake1` 个等待者是无条件唤醒的**，比较失败照样唤醒 —— 比较只决定要不要额外唤醒 uaddr2；
2. 比较用的是**旧值**，不是写完之后的新值。

操作码编码（与 `FUTEX_OP()` 宏逐位一致）：

```
((op & 0xf) << 28) | ((cmp & 0xf) << 24) | ((oparg & 0xfff) << 12) | (cmparg & 0xfff)
```

而内核解码时 `op` 只取 `0x70000000 >> 28`（**低 3 位**），最高位 `0x80000000` 是 `FUTEX_OP_OPARG_SHIFT` 标志：置位后 `oparg = 1 << (oparg & 31)`；`oparg` 本身还要过一次 `sign_extend32(..., 11)`，所以 `-1` 编成 `0xFFF` 还能原样解回来。

### 2.6 REQUEUE / CMP_REQUEUE

`FUTEX_REQUEUE` 把等待者从 uaddr1 搬到 uaddr2，`FUTEX_CMP_REQUEUE` 先校验 uaddr1 的值：不符就 `EAGAIN` 且**一个都不搬**（这是条件变量「先等 mutex、再等 cond」免惊群的底子）。自检里 4 个等待者 `REQUEUE(wake=1, requeue=2)` 后源队列剩 1、目标队列有 2，后续分别唤醒 2 与 1。

### 2.7 PI futex：把「谁持有锁」写进 word 本身

普通 futex 的 word 只表示状态，PI futex 强制一套**用户态与内核约定好的取值策略**（`futex(2)`）：

| 状态 | word 值 |
| --- | --- |
| 未持有 | `0` |
| 已持有 | 持有者 TID |
| 已持有且有等待者 | `FUTEX_WAITERS \| TID` |

常量取自 `uapi/linux/futex.h`：`FUTEX_WAITERS = 0x80000000`、`FUTEX_OWNER_DIED = 0x40000000`、`FUTEX_TID_MASK = 0x3fffffff`。于是「无竞争取锁」就是用户态 `cmpxchg(0 → TID)`（自检断言此时 `syscalls == 0`）；释放只能由持有者做，否则 `EPERM`。

持有者猝死时，内核清理 RT-mutex 并把锁交给下一个等待者，同时置 `FUTEX_OWNER_DIED` 位 —— *"User space can detect this situation via the presence of the FUTEX_OWNER_DIED bit and is then responsible for cleaning up the stale state"*。

优先级继承必须是**传递**的：*"if a high-priority task blocks on a lock held by a lower-priority task that is itself blocked by a lock held by another intermediate-priority task ... then all of those tasks ... have their priorities raised"*。自检里构造 H(10) → lock1(持有者 L，1) → L 又等 lock2(持有者 M，5)，迭代到不动点后 `eff[M] == 10`，能压过优先级 7 的无关任务。

## 三、对比

| 维度 | 普通 futex | PI futex |
| --- | --- | --- |
| word 含义 | 自定义（状态/计数） | 内核规定：0 / TID / WAITERS\|TID |
| 无竞争取锁 | 用户态原子 | 用户态 `cmpxchg(0→TID)` |
| 释放者校验 | 无 | 非持有者 → `EPERM` |
| 优先级反转 | 不管 | 内核 RT-mutex 传递继承 |
| 猝死恢复 | 不管（用户自己兜） | 置 `FUTEX_OWNER_DIED` 并交锁 |

| 维度 | FUTEX_WAIT | FUTEX_WAIT_BITSET |
| --- | --- | --- |
| timeout | 相对 | 绝对 |
| 唤醒过滤 | 无 | 按 bitset 匹配 |

## 四、环境与运行

- Python 3.13（模型与自检，本机实跑）：

```bash
cd 03-系统编程/02-进程与线程/futex机制
python selfcheck_futex.py     # 77 项断言全绿
```

- `futex_mutex.c`：Linux 专有（`syscall(SYS_futex, ...)`），本机只做静态审查未编译。
- `futex_model.go`：Go 复刻版，本机无 Linux/未编译，人工审查。

## 五、关键代码

三态互斥锁的骨架（`futex_mutex.c`）：无竞争 `0→1`；抢不到就先 `1→2` 把「有等待者」写进 word，再 `FUTEX_WAIT(CONTENDED)`；释放时 `atomic_exchange` 读到的旧值是 `2` 才 `FUTEX_WAKE`。

```c
uint32_t cur = LOCKED_NOWAITERS;
if (atomic_compare_exchange_weak_explicit(&lock_word, &cur, LOCKED_CONTENDED, ...))
    cur = LOCKED_CONTENDED;
futex((uint32_t *)&lock_word, FUTEX_WAIT, LOCKED_CONTENDED, NULL, NULL, 0);   /* 比较-并-阻塞 */
```

没有「2」这个状态，unlock 就无从判断要不要进内核 —— 要么每次都 `WAKE`（浪费系统调用），要么漏唤醒。

## 六、性能边界

- 无竞争加锁 = **0 次系统调用**（纯 `cmpxchg`）；竞争才 1 次 `FUTEX_WAIT` + 释放侧 1 次 `FUTEX_WAKE`。
- `FUTEX_PRIVATE_FLAG`（Linux 2.6.22 起）告诉内核这是进程内的，可省掉跨进程的 key 查找；`<linux/futex.h>` 里每个操作都有对应的 `_PRIVATE` 常量。
- `FUTEX_CLOCK_REALTIME` 默认不置位，超时按 `CLOCK_MONOTONIC` 计。
- 手册不给出任何定量收益（"快多少"取决于竞争率与本轮实现），故本 demo 不编造数字：只以「系统调用次数」这一可数指标作对比口径。

## 七、注意事项与常见坑

1. **忘记回环**：把 `FUTEX_WAIT` 当成「醒来即拿到锁」。假唤醒是明写在手册里的，必须重新 `cmpxchg`。
2. **超时口径搞反**：`FUTEX_WAIT` 是相对、其余是绝对，混用会让「等 5 秒」变成「等到第 5 秒」。
3. **把 `FUTEX_WAKE` 的返回值当剩余等待者数**：它是实际唤醒数。
4. **`FUTEX_WAKE_OP` 以为比较失败就什么都不做**：uaddr1 那批等待者照样被唤醒（源码为证）。
5. **PI futex 手改 word**：值策略是用户态与内核的契约，乱写会让内核看到非法状态；`FUTEX_WAITERS` 置位却 TID 为 0 是非法的。
6. **PI 操作必须配对**：`LOCK_PI` 对 `UNLOCK_PI`，`WAIT_REQUEUE_PI` 对 `CMP_REQUEUE_PI`；后者要求「从非 PI futex 移到 PI futex」且**唤醒数必须是 1**，否则 `EINVAL`。
7. **`FUTEX_WAKE_OP` 遇上 PI 等待者**直接 `EINVAL`（`waitwake.c`：*`if (this->pi_state || this->rt_waiter) { ret = -EINVAL; }`*）。
8. **纯用户态轮询当锁用**：无竞争确实最快，但持有者被抢占时会把 CPU 烧满（见同目录大类下的自旋锁 demo）。

## 八、参考资料（本轮实读）

- `futex(2)` — https://man7.org/linux/man-pages/man2/futex.2.html （PI 取值策略、操作数配对、`EINVAL` 条件）
- `futex(7)` — https://man7.org/linux/man-pages/man7/futex.7.html （up/down 协议、裸 futex 语义）
- `FUTEX_WAIT(2const)` — https://man7.org/linux/man-pages/man2/FUTEX_WAIT.2const.html （EAGAIN、假唤醒、相对超时 CAVEAT）
- `include/uapi/linux/futex.h` — https://cdn.jsdelivr.net/gh/torvalds/linux@master/include/uapi/linux/futex.h （`FUTEX_WAITERS`/`OWNER_DIED`/`TID_MASK`、`FUTEX_OP()` 宏与五个操作码、六个比较码）
- `kernel/futex/waitwake.c` — https://cdn.jsdelivr.net/gh/torvalds/linux@master/kernel/futex/waitwake.c （`futex_atomic_op_inuser()` 解码、`futex_wake_op()` 的唤醒顺序与 PI 校验）
