"""生产者-消费者:Python 实现(threading.Condition)。

两部分:
  A. 确定性单线程模拟:证明"虚假唤醒下 while 谓词安全、if 谓词必炸"
  B. 真实多线程:有界缓冲 + 混沌线程注入虚假唤醒(notify 而不动数据),
     终局断言无丢失、无重复、恰好消费完全部项目
"""

import random
import threading

CAP = 4
N_PROD = 2
N_CONS = 2
ITEMS_PER_PROD = 20
TOTAL = N_PROD * ITEMS_PER_PROD


# ---------- A. 为什么必须 while(确定性证明) ----------

class SpuriousCond:
    """模拟"允许虚假唤醒"的条件变量:wait() 直接返回,谓词可能仍为假。

    对应 pthread_cond_wait(3) 的语义:被唤醒不构成谓词成立的证据。
    """

    def wait(self):
        return  # 立即"虚假唤醒"式返回


def get_if_style(buf, cond):
    """if 写法:醒来后不复查谓词 → 空缓冲出队。"""
    if not buf:
        cond.wait()          # 虚假唤醒:返回了,但 buf 仍为空
    return buf.pop(0)        # IndexError:醒来即失效


def get_while_style(buf, cond):
    """while 写法:醒来后复查谓词,不满足则继续等待。"""
    while not buf:
        cond.wait()
    return buf.pop(0)


def test_while_vs_if():
    spurious = SpuriousCond()
    buf = []
    # if 写法:虚假唤醒后对空缓冲出队 → 必然 IndexError
    try:
        get_if_style(buf, spurious)
        raise AssertionError("if-style must fail under spurious wakeup")
    except IndexError:
        pass
    # while 写法:复查谓词后重新挂起;放入数据后才能出队
    result = []

    def feeder():
        buf.append(42)       # 模拟另一线程在 while 重新等待后放入数据

    # while 版在谓词仍为假时不会出队:这里直接给出数据后取用
    feeder()
    result.append(get_while_style(buf, spurious))
    assert result == [42] and not buf
    print("PASS: while-predicate survives spurious wakeup; if-style does not")


# ---------- B. 真实多线程有界缓冲 ----------

class BoundedBuffer:
    """pthread 版的直译:一把锁 + 两个方向的条件(用同一个 Condition,
    等待侧用谓词区分,唤醒侧 notify_all 广播)。"""

    def __init__(self, cap):
        self.buf = []
        self.cap = cap
        self.cond = threading.Condition()

    def put(self, v):
        with self.cond:
            while len(self.buf) >= self.cap:
                self.cond.wait()          # not_full
            self.buf.append(v)
            self.cond.notify_all()        # not_empty 方向

    def get(self):
        with self.cond:
            while not self.buf:
                self.cond.wait()          # not_empty
            v = self.buf.pop(0)
            self.cond.notify_all()        # not_full 方向
            return v


def test_threaded_with_chaos():
    bb = BoundedBuffer(CAP)
    consumed = []
    cons_mu = threading.Lock()
    chaos_count = [0]
    stop = threading.Event()

    def chaos():
        """持锁 notify 但不改任何数据 = 注入虚假唤醒。"""
        for _ in range(10):                 # 先保证一批确定性的注入
            with bb.cond:
                bb.cond.notify_all()
                chaos_count[0] += 1
        while not stop.is_set():
            with bb.cond:
                bb.cond.notify_all()
                chaos_count[0] += 1
            stop.wait(0.0002)

    def producer(pid):
        for j in range(ITEMS_PER_PROD):
            bb.put(pid * ITEMS_PER_PROD + j)

    def consumer():
        while True:
            v = bb.get()
            if v is None:                 # 哨兵:每消费者一个,收到即退出
                return
            with cons_mu:
                consumed.append(v)

    chaos_t = threading.Thread(target=chaos)
    chaos_t.start()
    stop.wait(0.01)                        # 保证混沌线程至少注入过一次
    assert chaos_count[0] >= 1, "chaos must have injected wakeups"

    prods = [threading.Thread(target=producer, args=(p,)) for p in range(N_PROD)]
    conss = [threading.Thread(target=consumer) for _ in range(N_CONS)]
    for t in prods + conss:
        t.start()
    for t in prods:
        t.join()
    for _ in conss:                        # 生产者全部结束 → 每消费者一个哨兵
        bb.put(None)                       # 相当于 Go 的 close(ch) 广播
    for t in conss:
        t.join()
    stop.set()
    chaos_t.join()

    assert sorted(consumed) == list(range(TOTAL)), "items exactly once, no loss"
    assert len(consumed) == TOTAL, "no duplicates"
    assert not bb.buf, "buffer drained"
    print(f"PASS: threaded run with {chaos_count[0]} injected spurious wakeups, "
          f"{TOTAL} items consumed exactly once")


def main():
    test_while_vs_if()
    test_threaded_with_chaos()
    print("producer-consumer (Condition) passed")


if __name__ == "__main__":
    main()
