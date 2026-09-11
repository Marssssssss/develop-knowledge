# 哲学家就餐问题（Dining Philosophers）

## 简介

五个哲学家围坐圆桌，每人左右各有一根筷子（共五根），思考与进餐交替；进餐需同时拿到左右两把筷子。问题挑战是设计并发算法，使 **不死锁、不饥饿、并发度尽可能高**（理论上界 `⌊N/2⌋ = 2`）。

- **关键概念**
  - **死锁四条件**（Coffman/Elphick/Shoshani 1971）：互斥 / 持有并等待 / 不可抢占 / 循环等待。
  - **资源分级（Resource Hierarchy）**：全局给筷子编号 0…4，所有哲学家先拿编号更小的筷子，破除循环等待。
  - **监视器方案**（Dijkstra 1965 + Tanenbaum 修订）：1 mutex + 每哲学家 1 condvar + state[]，既无死锁也几乎无饥饿。
- **历史**：1965 年由 Edsger W. Dijkstra 作为学生考题提出，原型是「五台计算机争用磁带机」；后由 Tony Hoare 改写为「哲学家与筷子」的现表述。

## 原理详解

### 死锁四条件

| 条件 | 是否成立（Naive） | Naive 死锁示例 |
| --- | --- | --- |
| 互斥（筷子一次一人） | ✓ | 筷子互斥 |
| 持有并等待（持一根等另一根） | ✓ | P₀ 持 left 等 right |
| 不可抢占（不能从邻居手里夺） | ✓ | 邻居不会让出 |
| 循环等待（P₀→P₁→…→P₄→P₀） | ✓ | 环 P₀→P₁→P₂→P₃→P₄→P₀ |

任意一条被打破即可避开死锁。

### Naive 方案：**没有破除任何条件**，故可能死锁

```
think → pickup(left) → pickup(right) → eat → putdown(left) → putdown(right) → repeat
```

5 个线程同时「先左后右」：所有人持左等右 → 循环等。

### Resource Hierarchy：**打破循环等待**

```
philosopher i 的筷子是 i（左）和 (i+1) % 5（右）
先拿 min(i, (i+1)%5)，后拿另一根
```

P₀ 在 fork₄（左）和 fork₀（右）之间 → 先拿 fork₀；
P₁ 在 fork₀（左）和 fork₁（右）之间 → 先拿 fork₀；
P₄ 在 fork₃（左）和 fork₄（右）之间 → 先拿 fork₃。

环必须所有箭头同方向；P₀ 抢先拿右筷打破对称 → **不可能形成完整循环**。

### Tanenbaum 监视器：**打破持有并等待**

```
state[i] ∈ { THINKING, HUNGRY, EATING }
1 mutex 保护全部 state[N]
N condvar：cv[i]，进餐前 cv.wait，进餐完 cv[left] 与 cv[right] .signal
```

```
pickup_forks(i):
    mutex.lock
    state[i] = HUNGRY
    test(i)                  // 自己能否吃：左右邻居都不是 EATING
    while state[i] != EATING:
        cv[i].wait(mutex)    // 失败则阻塞；原子释放 mutex（绝不"持左等右"）
    mutex.unlock

put_forks(i):
    mutex.lock
    state[i] = THINKING
    test(left)
    test(right)
    mutex.unlock

test(k):
    if state[left(k)] != EATING and state[right(k)] != EATING and state[k] == HUNGRY:
        state[k] = EATING
        cv[k].signal
```

为何不死锁：拿不到筷子时阻塞而不持锁等待 → 不会出现「持一根等另一根」。

### 三方案对照

| 方案 | 破除条件 | 死锁 | 饥饿 | 并发度 | 实现复杂度 |
| --- | --- | --- | --- | --- | --- |
| Naive | （无） | 可能 | 可能 | 高 | 极简 |
| Resource Hierarchy | 循环等待 | 不会 | 可能 | 中 | 简单 |
| Tanenbaum Monitor | 持有并等待 | 不会 | 极少 | ⌊N/2⌋ | 中 |

## 环境准备

- 操作系统：POSIX（Linux / macOS / WSL）；Windows 用 MinGW 也可。
- C：gcc/clang + pthread（`-pthread`）。
- Python：3.10+（标准库 `threading.Condition` 内置 RLock）。
- Go：1.21+（用 `sync.Mutex.TryLock`，1.18+ 引入）。

## 运行方式

```bash
# C
gcc -O2 -Wall -Wextra -std=c11 -pthread c/philo_demo.c -o c/philo_demo
./c/philo_demo

# Python
python3 python/philo_demo.py

# Go
cd go && go run philo_demo.go
```

## 关键代码片段（Tanenbaum 监视器）

**C（pthread_cond_wait 的正确用法：必须 while 谓词循环）**

```c
static void test(int i) {
    if (state[(i + 4) % 5] != EATING &&
        state[(i + 1) % 5] != EATING &&
        state[i] == HUNGRY) {
        state[i] = EATING;
        pthread_cond_signal(&cv[i]);
    }
}

static void pickup_forks(int i) {
    pthread_mutex_lock(&mtx);
    state[i] = HUNGRY;
    test(i);
    while (state[i] != EATING)                    /* 谓词循环 */
        pthread_cond_wait(&cv[i], &mtx);          /* 原子释放 mutex + 阻塞 */
    pthread_mutex_unlock(&mtx);
}
```

**Python（`threading.Condition` 包装）**

```python
class TanenbaumMonitor:
    def __init__(self, n):
        self.state = [THINKING] * n
        self.cv = threading.Condition()       # 内部 RLock

    def _test(self, i):
        if self.state[(i-1) % self.n] != EATING and \
           self.state[(i+1) % self.n] != EATING and \
           self.state[i] == HUNGRY:
            self.state[i] = EATING
            self.cv.notify()

    def pickup(self, i):
        with self.cv:
            self.state[i] = HUNGRY
            self._test(i)
            while self.state[i] != EATING:
                self.cv.wait()                # 原子释放 RLock + 阻塞
```

**Go（`*sync.Cond`；不可复制）**

```go
func (m *monitor) test(i int) {
    l, r := (i-1+N)%N, (i+1)%N
    if m.state[l] != EATING && m.state[r] != EATING && m.state[i] == HUNGRY {
        m.state[i] = EATING
        m.cv.Signal()
    }
}

func (m *monitor) pickup(i int) {
    m.mu.Lock(); defer m.mu.Unlock()
    m.state[i] = HUNGRY
    m.test(i)
    for m.state[i] != EATING {
        m.cv.Wait() // 原子释放 mu + 阻塞
    }
}
```

## 性能与边界

- 时间复杂度：单次 pickup/putdown 各 O(1)；N 哲学家稳态吞吐 **⌊N/2⌋ 次进餐/轮**。
- 空间复杂度：O(N) state、O(N) condvar / mutex 表，N 即可为哲学家数。
- 平台差异：Linux 走 NPTL futex（轻量）；macOS 的 `pthread_cond_signal` 不保证 FIFO（POSIX 也不保证）；Windows MinGW-pthreads 是用户态互模拟，性能差。

## 注意事项与常见坑

1. **`while (!predicate) cv.wait(...)` 而不是 `if`**：POSIX / Python / Go 均允许 spurious wakeup；且「先 signal 后 wait」信号会丢失。
2. **`signal` 一侧必须持 mutex**：否则生产者先 signal、消费者后 wait → 信号丢失，线程永久阻塞。
3. **Naive 死锁具有偶发性**：5 线程同时先左的概率受调度影响，高负载/容器下显著增大。**永远别在生产用 Naive**。
4. **Resource Hierarchy 仍可能饥饿**：方案只破「循环等待」，不保证公平；某哲学家慢则邻居反复回抢。
5. **`sync.Cond` 不可复制**：Go 的 `sync.Cond` 内部 mutex 不可复制，必须用指针；`range m` 时会在某些场景下发生零值拷贝导致 `panic`。
6. **死锁检测工具的复现**：手动加 pause（如 trylock 退避 10-30 ms）或共享计数器「持左筷未持右筷的哲学家计数 ≥4 持续 1 s」都能把偶发死锁变成可测。

## 参考资料（实际阅读过的权威来源）

- [Dining philosophers problem — Wikiwand](https://www.wikiwand.com/en/Dining_philosophers_problem) —— 1965 提出、死锁四条件、Tanenbaum C++20 监视器方案完整代码。
- [The Dining Philosophers — unseel.com](https://unseel.com/cs/dining-philosophers) —— 可视化 + 资源分级方案「O(0) 开销」讨论 + 服务员方案对比。
- [哲学家就餐问题解法大全 — diningphilosophers.eu 中文](https://diningphilosophers.eu/) —— 10+ 方案对比表（超时、资源分级、Asymmetric、Chandy-Misra 分布式、服务员、Tanenbaum 等）。
- [Deadlock — Loyola University Chicago CS](https://os.cs.luc.edu/deadlock.html) —— Resource Hierarchy vs Try-and-Release 两条破除路径 + C `multi_lock` 排序示例。
- [`pthread_cond` — NetBSD Library Functions Manual](https://www.daemon-systems.org/man/pthread_cond.3.html) —— POSIX 规范：condvar 必须与 mutex 绑定 / 原子释放 / 谓词循环 / spurious wakeup。
