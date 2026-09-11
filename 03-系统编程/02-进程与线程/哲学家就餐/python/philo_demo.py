"""
哲学家就餐问题：3 种方案串行对比（Python 版）

  naive       —— "先左后右"，高竞争下极可能死锁（trylock + 重试 → 超时判死）
  hier        —— Resource Hierarchy，按筷子编号小者优先，破除循环等待
  tanenbaum   —— Dijkstra+Tanenbaum 监视器方案，Condition 包装
                 1 RLock + state[] + notify()，既无死锁也几乎无饥饿

运行：python3 philo_demo.py
"""

import random
import threading
import time

N = 5
THINKING, HUNGRY, EATING = 0, 1, 2
MAX_MEALS = 6                 # 每个哲学家最多进餐次数


# ============================================================
# Tanenbaum 监视器
#   Condition 内部用 RLock；wait() 原子释放 RLock + 阻塞；notify() 唤醒一个
# ============================================================
class TanenbaumMonitor:
    def __init__(self, n=N):
        self.n = n
        self.state = [THINKING] * n
        self.cv = threading.Condition()       # 内部 RLock 保证 release/wait 原子性

    def _test(self, i):
        l, r = (i - 1) % self.n, (i + 1) % self.n
        if self.state[l] != EATING and self.state[r] != EATING \
                and self.state[i] == HUNGRY:
            self.state[i] = EATING
            self.cv.notify()                  # 等价于 pthread_cond_signal

    def pickup(self, i):
        with self.cv:                         # 内部 acquire(self.cv._lock)
            self.state[i] = HUNGRY
            self._test(i)
            while self.state[i] != EATING:    # 谓词循环：处理 spurious wakeup
                self.cv.wait()                # 原子释放 RLock + 阻塞；唤醒后再次锁 RLock

    def putdown(self, i):
        with self.cv:
            self.state[i] = THINKING
            self._test((i - 1) % self.n)      # 通知左邻
            self._test((i + 1) % self.n)      # 通知右邻


# ============================================================
# 筷子：threading.Lock 不可重入（= POSIX mutex）
# ============================================================
class Chopstick:
    def __init__(self, idx):
        self.idx = idx
        self.lock = threading.Lock()


# ============================================================
# 三个策略的具体 pickup / putdown
# ============================================================
def make_pickup_putdown(strategy, forks):
    """返回 (pickup_func, putdown_func, monitor_obj)"""

    if strategy == "naive":
        def pickup(i):
            giveups = 0
            l, r = (i - 1) % N, (i + 1) % N
            while True:
                forks[l].lock.acquire()
                if forks[r].lock.acquire(blocking=False):    # 模拟 pthread_mutex_trylock
                    return
                forks[l].lock.release()                      # 失败：放左、短暂退避、重试
                giveups += 1
                if giveups > 50:                             # 50 次失败 ≈ 1 s 多 → 判死锁
                    raise RuntimeError("deadlock: cycle detected")
                time.sleep(random.uniform(0.01, 0.04))
        def putdown(i):
            forks[(i - 1) % N].lock.release()
            forks[(i + 1) % N].lock.release()
        return pickup, putdown, None

    if strategy == "hier":
        def pickup(i):
            l, r = (i - 1) % N, (i + 1) % N
            first, second = (l, r) if l < r else (r, l)
            forks[first].lock.acquire()
            forks[second].lock.acquire()
        def putdown(i):
            forks[(i - 1) % N].lock.release()
            forks[(i + 1) % N].lock.release()
        return pickup, putdown, None

    if strategy == "tanenbaum":
        monitor = TanenbaumMonitor(N)
        return monitor.pickup, monitor.putdown, monitor

    raise ValueError(f"unknown strategy: {strategy}")


# ============================================================
# 哲学家线程
# ============================================================
def run_strategy(strategy):
    forks = [Chopstick(i) for i in range(N)]
    pickup, putdown, monitor = make_pickup_putdown(strategy, forks)

    meals = [0] * N
    deadlock_flag = [False]
    barrier = threading.Barrier(N + 1)        # N 个线程 + 1 个主线程放行信号

    def philosopher(i):
        barrier.wait()                         # 等待主线程放行
        while meals[i] < MAX_MEALS and not deadlock_flag[0]:
            time.sleep(random.uniform(0.05, 0.10))   # think
            try:
                pickup(i)
            except RuntimeError:
                deadlock_flag[0] = True
                return
            time.sleep(random.uniform(0.05, 0.10))   # eat
            meals[i] += 1
            putdown(i)

    threads = [threading.Thread(target=philosopher, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    time.sleep(0.05)                          # 让所有线程都到 barrier
    barrier.wait()                            # 同时放行
    for t in threads:
        t.join()

    print(f"\n=== [{strategy}] 结果 ===")
    if deadlock_flag[0]:
        print(">> 死锁出现（Naive 高竞争下预期之中，重试过多即判循环等待）")
    else:
        for i in range(N):
            print(f"    P{i} 吃了 {meals[i]} 次")


if __name__ == "__main__":
    print("================ 哲学家就餐问题演示 ================\n")
    for strat in ("naive", "hier", "tanenbaum"):
        run_strategy(strat)
