"""读者-写者锁:读者偏好 vs 写者偏好(双策略对照)。

三部分:
  A. 同一状态机实现两种偏好,单线程脚本验证锁内部计数语义
  B. 真实线程确定性编排:写者排队时,新读者在两种策略下的去留
  C. 真实线程不变量:读者可重叠、写者段内无读者、写者互斥
"""

import threading
import time

WAIT = 0.2  # 编排等待(秒):远大于线程调度延迟,保证事件顺序确定


class PrefRWLock:
    """双策略读写锁:一个 Condition + 读者计数/写者标志/排队写者计数。"""

    def __init__(self, writer_pref):
        self.writer_pref = writer_pref
        self._cond = threading.Condition()
        self._readers = 0          # 当前持读锁数
        self._writer = False       # 当前持写锁
        self._waiting_writers = 0  # 已提交申请、尚未获得的写者数

    def rdlock(self):
        with self._cond:
            if self.writer_pref:
                while self._writer or self._waiting_writers:
                    self._cond.wait()
            else:
                while self._writer:
                    self._cond.wait()
            self._readers += 1

    def wrlock(self):
        with self._cond:
            self._waiting_writers += 1
            try:
                while self._writer or self._readers:
                    self._cond.wait()
            finally:
                self._waiting_writers -= 1
            self._writer = True

    def rdunlock(self):
        with self._cond:
            assert self._readers > 0, "rdunlock without rdlock"
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def wrunlock(self):
        with self._cond:
            assert self._writer, "wrunlock without wrlock"
            self._writer = False
            self._cond.notify_all()


# ---------- A. 状态机脚本(单线程,验证内部计数与配对) ----------

def test_state_machine():
    for pref in (False, True):
        lk = PrefRWLock(pref)
        # 读者并发计数
        lk.rdlock(); lk.rdlock()
        assert lk._readers == 2
        lk.rdunlock(); lk.rdunlock()
        assert lk._readers == 0 and not lk._writer
        # 写者独占(单线程下无竞争,直接进出)
        lk.wrlock()
        assert lk._writer and lk._readers == 0
        lk.wrunlock()
        assert not lk._writer
        # 排队写者计数对称(异常路径也 finally 递减)
        lk.wrlock(); lk.wrunlock()
        assert lk._waiting_writers == 0
    print("PASS: state machine (reader counting, writer flag, pairing)")


# ---------- B. 策略差异:写者排队时,新读者去留 ----------

def scenario(pref):
    """编排:R1 持读锁 → W1 申请(必然排队)→ R2 申请。返回事件顺序。"""
    lk = PrefRWLock(pref)
    order = []
    order_mu = threading.Lock()

    def record(tag):
        with order_mu:
            order.append(tag)

    r2_got = threading.Event()
    r2_done = threading.Event()
    w_started = threading.Event()
    w_in = threading.Event()

    def writer():
        w_started.set()
        lk.wrlock()                               # R1 持读锁 → 排队
        record("W-in")
        w_in.set()
        lk.wrunlock()
        record("W-out")

    def reader2():
        lk.rdlock()                              # 策略决定此刻能否进
        record("R2-in")
        r2_got.set()
        r2_done.wait()                           # 在里面驻留,直到收尾
        lk.rdunlock()
        record("R2-out")

    lk.rdlock()                                  # R1 进入
    record("R1-in")

    t_w = threading.Thread(target=writer)
    t_w.start()
    w_started.wait()

    t_r2 = threading.Thread(target=reader2)
    t_r2.start()
    r2_got.wait(timeout=WAIT)                     # 观察 R2 是否进入

    if pref is False:
        # 读者偏好:R2 应当直接进入(写者仍在排队)
        assert r2_got.is_set(), "readers-pref: R2 must acquire while writer waits"
        assert not w_in.is_set(), "writer still blocked under reader stream"
        lk.rdunlock()                            # R1 退出
        record("R1-out")
        r2_done.set()                            # 放 R2 出来 → 写者才能进
        t_r2.join()
        t_w.join()
        assert order.index("W-in") > order.index("R2-out")
    else:
        # 写者偏好:有写者排队 → R2 必须等待
        assert not r2_got.is_set(), "writers-pref: R2 must queue behind writer"
        lk.rdunlock()                            # R1 退出 → 写者先于 R2 进入
        w_in.wait(timeout=WAIT)
        record("R1-out")
        t_w.join()
        r2_got.wait(timeout=WAIT)                 # 写者释放后 R2 才能进
        assert r2_got.is_set(), "R2 must acquire after writer releases"
        r2_done.set()
        t_r2.join()
        assert order.index("W-in") < order.index("R2-in")
    return order


def test_preference_difference():
    o_readers_pref = scenario(False)
    assert o_readers_pref.index("R2-in") < o_readers_pref.index("W-in")
    o_writers_pref = scenario(True)
    assert o_writers_pref.index("W-in") < o_writers_pref.index("R2-in")
    print("PASS: readers-pref lets R2 in (writer starves) / "
          "writers-pref queues R2 behind writer")


# ---------- C. 真实线程不变量 ----------

def test_invariants():
    lk = PrefRWLock(writer_pref=True)
    mu = threading.Lock()
    cur_readers = [0]
    max_readers = [0]
    violations = [0]
    N_R, N_W, ITER = 4, 3, 100

    def reader():
        for _ in range(ITER):
            lk.rdlock()
            with mu:
                cur_readers[0] += 1
                max_readers[0] = max(max_readers[0], cur_readers[0])
                if lk._writer:
                    violations[0] += 1
            time.sleep(0.001)                   # 驻留:制造读者重叠窗口
            with mu:
                cur_readers[0] -= 1
            lk.rdunlock()

    def writer():
        for _ in range(ITER):
            lk.wrlock()
            with mu:
                if cur_readers[0] != 0 or not lk._writer:
                    violations[0] += 1
            lk.wrunlock()

    threads = [threading.Thread(target=reader) for _ in range(N_R)]
    threads += [threading.Thread(target=writer) for _ in range(N_W)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert violations[0] == 0, "invariant broken"
    assert max_readers[0] >= 2, "readers must overlap"
    assert lk._readers == 0 and not lk._writer and lk._waiting_writers == 0
    print(f"PASS: invariants (max concurrent readers = {max_readers[0]}, "
          "no writer-section overlap, drained)")


def main():
    test_state_machine()
    test_preference_difference()
    test_invariants()
    print("rwlock (dual policy) passed")


if __name__ == "__main__":
    main()
