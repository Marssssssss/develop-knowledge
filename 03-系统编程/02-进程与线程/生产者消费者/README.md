# 生产者-消费者:互斥锁+条件变量 vs channel

## 简介

生产者-消费者是并发编程的"hello world":共享一个**有界缓冲区**,生产者在满时阻塞、
消费者在空时阻塞,双方通过唤醒机制协作。本 demo 用三种实现对照同一语义:

- **C**:`pthread_mutex` + 两个条件变量(`not_full` / `not_empty`)
- **Python**:`threading.Condition` + 注入"虚假唤醒"的混沌线程,验证 while 谓词的必要性
- **Go**:带缓冲 channel + `close` 广播,几乎不需要手写同步原语

关键概念:

- **条件变量**:让线程挂起直到某谓词成立;必须**始终与互斥锁配对**使用。
- **虚假唤醒(spurious wakeup)**:被唤醒 ≠ 谓词成立,醒来后必须重新检查。
- **谓词循环(while,not if)**:`pthread_cond_wait` 返回后谓词可能又不成立/从未成立。
- **happens-before**:Go channel 的发送/接收构成同步边,支撑无锁的数据搬运推理。

## 原理详解

### 1. pthread_cond_wait 的原子三步

man7 pthread_cond_wait(3) 规定的语义,一次调用内完成:

```text
进入: 必须已持有 mutex
  1. 原子地解锁 mutex 并挂起线程(两者不可分割——消灭"准备等待"与"发信号"之间的竞态)
  2. 被 signal/broadcast 唤醒后,重新加锁 mutex,然后才返回
谓词: 返回时谓词不保证成立 → 必须用 while 循环:
        pthread_mutex_lock(&mu);
        while (predicate_false)
            pthread_cond_wait(&cond, &mu);   ← 原子"解锁+等待"+"再加锁"
        /* 操作共享数据 */
        pthread_mutex_unlock(&mu);
```

- `pthread_cond_signal` 唤醒**至少一个**等待者;无人等待则什么也不发生(信号不累积)。
- 修改共享数据后**在持有锁时**发信号:若所有线程都持锁后才 signal,则"条件在加锁与
  挂起之间被置真又漏掉"的竞态不可能发生(man 手册 CAVEATS 示例的保证来源)。
- 拿不准 signal 还是 broadcast 时用 broadcast(broadcast 更保守)。

### 2. 有界缓冲的四条路径

| 状态 | 生产者 | 消费者 |
| --- | --- | --- |
| buffer 未满未空 | 入队 → signal `not_empty` | 出队 → signal `not_full` |
| buffer 满 | `while(count==CAP) wait(not_full)` | 出队 → signal `not_full` |
| buffer 空 | 入队 → signal `not_empty` | `while(count==0) wait(not_empty)` |

环型缓冲:`tail = (tail+1) % CAP`,`count` 独立维护(入队处加、出队处减),避免
head==tail 的"空/满"二义性。

### 3. 为什么必须 while(本 demo 的确定性证明)

Python 版内置一个"必然虚假唤醒"的单线程模拟:`wait()` 一返回但谓词仍为假——
`if` 写法直接对空缓冲出队(`IndexError`),`while` 写法则重新挂起。真实内核的
`futex_wait` 同样允许无理由返回(条件变量语义允许),多消费者醒来抢空最后一个
元素是另一种"醒来即失效"。

### 4. Go channel:happens-before 替代锁

go.dev/ref/mem 规定的同步边(本 demo 依赖的三条):

- 某 channel 的第 k 次**发送** happens-before 对应的第 k 次**接收完成**;
- 容量 C 的 channel 第 k 次**接收** happens-before 第 k+C 次**发送完成**
  (即缓冲区大小=允许的超前发送数,可用作计数信号量);
- `close(c)` happens-before 因关闭而收到零值的接收。

因此:生产者把数据放进 channel 前的全部写入,对收到它的消费者**可见**——无需锁。
关闭时机由 `WaitGroup` 保证在所有发送之后(`Wait(); close(ch)`),消费者 `for v := range ch`
在收到关闭后自动退出,这正是"broadcast = close"的官方对照(pkg/sync 文档)。

## 对比 / 选型

| 维度 | mutex+condvar(C/Python) | buffered channel(Go) |
| --- | --- | --- |
| 表达力 | 谓词任意(状态机复杂时仍可控) | 数据流即同步,queue+信号合一 |
| 出错面 | 忘 while / 忘 signal / 死锁 | close 时机错(向已关 channel 发送 panic) |
| 多消费者公平性 | signal 只保证唤醒≥1,指定谁 | 队列 FIFO 就绪 |
| 适用 | C 代码库、已有共享状态设计 | Go 默认选择("通过通信共享内存") |

## 环境准备

- C:Linux + gcc + pthread(`-lpthread`)。
- Python:3.8+,仅标准库(Windows 可跑,本仓库自检方式)。
- Go:1.21+,仅标准库。

## 运行方式

```bash
# C
gcc -O2 -Wall -Wextra main.c -o pc -lpthread && ./pc
# Python
python3 main.py
# Go
go run .
```

## 关键代码片段

C——谓词循环 + 成对信号(原理 §1/§2):

```c
pthread_mutex_lock(&bb.mu);
while (bb.count == BUF_CAP)                    /* while:防虚假唤醒 */
    pthread_cond_wait(&bb.not_full, &bb.mu);
bb.slots[bb.tail] = v; bb.tail = (bb.tail+1)%BUF_CAP; bb.count++;
pthread_cond_signal(&bb.not_empty);           /* 持锁时发信号 */
pthread_mutex_unlock(&bb.mu);
```

Go——close 即广播(原理 §4):

```go
go func() { wg.Wait(); close(ch) }()   // 所有发送完成后关闭
for v := range ch { ... }              // 收到零值广播后自然退出
```

## 性能与边界

- 有界环形缓冲每步 O(1);竞争度由 CAP 与生产消费速率差决定。
- Python `list.pop(0)` 是 O(n)——demo 规模(40 项)无感;生产应 `collections.deque`。
- channel 容量是**背压**边界:容量 0(无缓冲)即同步 rendezvous。

## 注意事项与常见坑

- **现象**:偶发 `IndexError`/读到旧值 → **原因**:`if` 判断谓词(虚假唤醒/被抢先)
  → **规避**:一律 `while` 循环(三份实现均如此)。
- **现象**:程序卡死 → **原因**:signal 时不持锁且谓词在"解锁后、挂起前"翻转
  (丢失唤醒),或忘了在另一方向上 signal → **规避**:统一"持锁改数据+signal"模板。
- **现象**:Go 向已 close 的 channel 发送 panic → **原因**:close 与发送未建立
  happens-before → **规避**:close 只由唯一的发送方管理者在 `Wait()` 后执行。
- 消费者结束条件必须与生产者关闭条件闭环(否则 `range` 永不退出)。

## 参考资料(实际阅读过的权威来源)

- [pthread_cond_init(3) - Linux manual page](https://man7.org/linux/man-pages/man3/pthread_cond_wait.3.html) —
  原子"解锁+挂起/重加锁"、while 谓词示例、signal≥1 与持锁发信号的竞态论证
- [The Go Memory Model](https://go.dev/ref/mem) — channel 发送/接收/close 的
  happens-before 规则、容量 C 计数信号量模型
- [sync package - Go Packages](https://go.dev/pkg/sync/) — Cond.Wait 的循环模板、
  Broadcast 对应 close(channel)的官方对照
