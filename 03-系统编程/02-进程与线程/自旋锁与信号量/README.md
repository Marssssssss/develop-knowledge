# 自旋锁与信号量：忙等到阻塞的两端

## 一、简介

互斥锁把「抢不到」交给内核去睡；自旋锁选择**站在原地空转**：*"The calling thread shall acquire the lock if it is not held by another thread. Otherwise, the thread shall spin (that is, shall not return from the pthread_spin_lock() call) until the lock becomes available"*（POSIX `pthread_spin_lock`）。换来的是没有上下文切换、没有系统调用，代价是**占用 CPU 直到拿到锁**。

信号量是另一种形状：它不表达「归谁」，只表达「还剩几份」，*"A semaphore is an integer whose value is never allowed to fall below zero"*（`sem_overview(7)`）。

两者都写进了 POSIX 基类（`pthread_spin_lock` 在 Issue 7 才从 Spin Locks 选项移入 Base），本 demo 把它们的关键语义做成可执行模型。

## 二、原理详解

### 2.1 自旋锁不排队

最常见的实现是 test-and-set：一条原子指令把标志位置 1 并读回旧值，谁先抢到谁进。**没有队列**，所以调度顺序就是获取顺序 —— 4 个线程按 `0,1,2,3` 到达、却按 `3,2,1,0` 被调度时，TAS 的获取序列就是 `[3,2,1,0]`，插队 6 次。

排队自旋锁（ticket lock）先取号再等叫号：`next_ticket` 发号、`now_serving` 叫号，只有持票等于叫号者能进。同样调度下获取序列恒为 `[0,1,2,3]`，插队 0 次。这就是「公平」与「吞吐」的经典取舍：TAS 少一次取号的原子操作且能利用「刚释放立刻被相邻核抢到」的局部性，ticket 消除了饥饿但每次释放都要广播叫号。

### 2.2 递归上锁是 undefined，不是错误码

`pthread_spin_lock`：*"The results are undefined if the calling thread holds the lock at the time the call is made"*。Issue 7 的 CHANGE HISTORY 记录得很清楚：

> *The [EDEADLK] error for a spin lock object for which the calling thread already holds the lock is removed; this condition results in undefined behavior.*

也就是说**别指望** `EDEADLK`（标准只说 `may fail if ... A deadlock condition was detected`）。模型里持有者再次上锁会一直自旋到观测窗口耗尽。

另两条常常被写反：
- `pthread_spin_trylock` 被持有时是 **shall fail** with `[EBUSY]`（不是「可能」）；
- 两个函数都 **shall not return an error code of [EINTR]** —— 自旋不会被信号打断，这与 `sem_wait` 完全相反。

### 2.3 单核 + 不可抢占 = 死锁

自旋的前提是「锁的持有者能继续跑」。单核且不可抢占时，自旋者占着唯一的 CPU，持有者永远出不了临界区。模型里 `quantum=0` 跑 500 个 tick：持有者进度 0，自旋者空转 500 次；改成轮转（`quantum=5`，临界区只要 3 tick）后，总耗时 8 tick、其中 5 tick 是纯粹的空转 —— **空转比实际工作还多**。

### 2.4 优先级反转：标准直接点名

POSIX 在 `pthread_spin_lock` 的 APPLICATION USAGE 里写：*"Applications using this function may be subject to priority inversion, as discussed in XBD Priority Inversion."*

场景：L（低）持锁，H（高）自旋，M（中）可运行。自旋锁没有「等待者」这个内核可见的概念，没人知道该提升 L，于是优先级 5 的 M 一直压着优先级 1 的 L 跑，H 在旁边干等。模型给出的数：临界区只需 3 tick、M 要跑 100 tick 时，**H 等了 103 tick**；换成会传递提升的锁（futex PI 那种口径），H 只等 **3 tick**，M 一次都没跑成。

### 2.5 信号量：值永不为负，出错时值不变

`sem_wait(3)`：值大于 0 就减一并立即返回；等于 0 就阻塞，"*until either it becomes possible to perform the decrement ... or a signal handler interrupts the call*"。

两条易错的：
- **EINTR 时值不变**：*"on error, the value of the semaphore is left unchanged"*。所以被信号打断不会「偷走」一份许可，但调用方必须自己重试（官方示例就是 `while (sem_timedwait(...) == -1 && errno == EINTR) continue;`）。
- **`sem_post` 是 async-signal-safe**（`sem_wait(3)` 属性表 MT-Safe，官方示例就是在信号处理器里 `sem_post` 唤醒主流程）。这也是信号量比「条件变量 + 互斥锁」更适合做异步通知的原因之一。

计数 vs 二进制：`sem_init(&s, 0, 2)` 允许 2 个并发，第 3 个 `sem_wait` 阻塞；`sem_post` 若发现队列里有等待者，就直接把它交给被唤醒者（值净变化为 0），没有等待者才真正累加。

### 2.6 命名信号量的名字与寿命

`sem_overview(7)`：名字形如 `/somename`，*"a null-terminated string of up to NAME_MAX-4 (i.e. 251) characters consisting of an initial slash, followed by one or more characters, none of which are slashes"*；在 Linux 上落在虚拟文件系统里（通常 `/dev/shm`），文件名 `sem.somename` —— 这正是名字上限是 `NAME_MAX-4` 而不是 `NAME_MAX` 的原因（`sem.` 四个字符）。

寿命是**内核持久**：*"if not removed by sem_unlink(3), a semaphore will exist until the system is shut down"*。`unlink` 解除的是**名字**，已打开的句柄照常可用，之后按同名打开拿到的是新对象。

## 三、对比

| 维度 | 自旋锁 | 互斥锁（futex 底） |
| --- | --- | --- |
| 抢不到时 | 占 CPU 空转 | 进内核睡眠 |
| 系统调用 | 0 | 竞争时 1 次 wait + 1 次 wake |
| 被信号打断 | 不会（明确不返回 EINTR） | 也不会（pthread_mutex_lock） |
| 递归上锁 | UB | 依 type（NORMAL 也是 UB / 死锁） |
| 优先级反转 | 标准点名会有 | 可用 PI/PTHREAD_PRIO_INHERIT |
| 适用 | 临界区极短、多核、不可睡眠上下文 | 一般情况 |

| 维度 | TAS | ticket |
| --- | --- | --- |
| 排队 | 无 | 有（取号/叫号） |
| 插队 | 可能（实测 6 次） | 0 |
| 释放代价 | 一条 store | 一条 fetch_add + 全体可见的叫号 |

## 四、环境与运行

```bash
cd 03-系统编程/02-进程与线程/自旋锁与信号量
python selfcheck_spinlock_sem.py    # 52 项断言全绿
```

- `spinlock_sem.c`：POSIX + C11 `<stdatomic.h>`，本机静态审查未编译（需 Linux + `-pthread`）。
- `spinlock_sem.go`：Go 复刻，人工审查。

## 五、关键代码

排队自旋锁（C11）：

```c
unsigned t = atomic_fetch_add_explicit(&l->next_ticket, 1u, memory_order_relaxed);
while (atomic_load_explicit(&l->now_serving, memory_order_acquire) != t) { /* spin */ }
```

取号用 relaxed（只需原子、不需同步），等叫号必须用 acquire（要接住释放方 release 之前的全部写入）—— 顺序反了就是数据竞争。

信号量的正确重试姿势：

```c
while (sem_wait(&empty_slots) == -1 && errno == EINTR)
    ;                    /* 手册保证出错时值不变，所以重试不会多吃一份 */
```

## 六、性能边界

- 自旋的收益来自「省掉两次上下文切换」，代价是**空转期间 CPU 100% 占用**。手册不给任何定量阈值（临界区多短才值得自旋取决于机器），故本 demo 不编造 crossover 数字，只用可数的「空转 tick / 工作 tick」作口径：示例里是 5 比 3。
- `atomic_flag_test_and_set` 的 acquire 语义在多核上有真实开销（要等 store buffer 排空），ticket 锁的释放还要让叫号对所有核可见。
- `pthread_spin_lock` 在 Issue 7 之前是可选特性，移植到老系统要先确认 `_POSIX_SPIN_LOCKS`。

## 七、注意事项与常见坑

1. **在单核/不可抢占上下文里自旋** —— 直接死锁；中断处理、单核 RT 任务尤其危险。
2. **把 EDEADLK 当可靠错误码** —— Issue 7 起递归上锁是 UB。
3. **指望自旋锁会被信号打断** —— 标准明文不返回 EINTR。
4. **临界区里做 I/O、分配内存、再拿别的锁** —— 把别人的等待时间也一起乘进去了；优先级反转正是这么被放大的。
5. **把 `sem_wait` 的 EINTR 当成「已经拿到」** —— 出错时值不变，必须重试而不是继续往下走。
6. **用 `sem_getvalue` 的结果做判断再去 wait** —— 值是「读到的那一刻」的快照，判断与 wait 之间随时会变；要非阻塞就用 `sem_trywait`（值 0 时返回 EAGAIN）。
7. **命名信号量忘记 `sem_unlink`** —— 内核持久，进程退出也留着，下次启动拿到的是旧值。
8. **把信号量当互斥锁用却 `sem_post` 放错位置** —— 计数信号量没有所有权概念，谁都能 post，写错就是凭空多发许可。

## 八、参考资料（本轮实读）

- `pthread_spin_lock, pthread_spin_trylock` — https://pubs.opengroup.org/onlinepubs/9699919799/functions/pthread_spin_lock.html （spin 语义、UB、EBUSY、不返回 EINTR、优先级反转的 APPLICATION USAGE、Issue 7 变更）
- `sem_overview(7)` — https://man7.org/linux/man-pages/man7/sem_overview.7.html （值不为负、命名/匿名、251 字符、`/dev/shm/sem.*`、内核持久性）
- `sem_wait(3)` — https://man7.org/linux/man-pages/man3/sem_wait.3.html （阻塞语义、EINTR 时值不变、EAGAIN、信号处理器里 `sem_post` 的官方示例）
