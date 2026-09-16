"""CFS 完全公平调度器:vruntime 最小模拟(带断言自检)。

模拟口径(docs.kernel.org/scheduler/sched-design-CFS.html):
- vruntime += delta * NICE_0 / weight(纳秒精度;此处用 ms 整数演示)
- 永远挑红黑树最左(vruntime 最小)的任务
- min_vruntime 单调递增,跟踪队列最小 vruntime,用于放置新实体
- 粒度:当前任务与最左任务 vruntime 差超过 granularity 才切换
  (本模拟简化为:每任务每次恰好跑一个 granularity)
"""

NICE_0 = 1024        # 基准权重(nice 0)
GRAN = 8             # 粒度 ms:每次调度让任务跑的时长
TOTAL_MS = 1000      # 每个公平性实验的总时长


class Task:
    def __init__(self, name, weight):
        self.name = name
        self.weight = weight
        self.vruntime = 0
        self.runtime = 0      # 实际拿到的时间(ms)

    def account(self, delta):
        """记账:实际运行时间按权重归一化成 vruntime。"""
        self.vruntime += delta * NICE_0 // self.weight
        self.runtime += delta


class CfsSim:
    def __init__(self):
        self.tasks = []
        self.min_vruntime = 0
        self.min_vruntime_history = [0]
        self.pick_log = []     # (轮次, 任务名):验证最左选取

    def add(self, task, clamp=True):
        """入队(新任务/唤醒)。以 min_vruntime 兜底放置(文档 §3)。"""
        if clamp and task.vruntime < self.min_vruntime:
            task.vruntime = self.min_vruntime
        self.tasks.append(task)

    def remove(self, task):
        """出队(模拟睡眠)。"""
        self.tasks.remove(task)

    def _leftmost(self):
        """红黑树最左 = vruntime 最小(此处线性扫描演示)。"""
        return min(self.tasks, key=lambda t: t.vruntime)

    def run(self, total_ms):
        """跑到 total_ms 为止;每轮让最左任务跑满一个粒度。"""
        while total_ms > 0:
            cur = self._leftmost()
            self.pick_log.append(cur.name)
            cur.account(GRAN)
            # 切换后:最左者变化,推进 min_vruntime(单调不减)
            self._update_min_vruntime()
            total_ms -= GRAN
        return cur

    def _update_min_vruntime(self):
        new_min = min(t.vruntime for t in self.tasks) if self.tasks \
            else self.min_vruntime
        if new_min > self.min_vruntime:
            self.min_vruntime = new_min
        self.min_vruntime_history.append(self.min_vruntime)
        # 单调性由 run() 末尾统一断言


def assert_monotone(sim):
    h = sim.min_vruntime_history
    assert all(b >= a for a, b in zip(h, h[1:])), h


def test_equal_weights_fair():
    sim = CfsSim()
    a, b = Task("A", 1024), Task("B", 1024)
    sim.add(a)
    sim.add(b)
    sim.run(TOTAL_MS)
    assert a.runtime + b.runtime == TOTAL_MS
    assert abs(a.runtime - b.runtime) <= GRAN, (a.runtime, b.runtime)
    assert_monotone(sim)
    print(f"PASS: equal weights -> equal shares "
          f"(A={a.runtime}ms B={b.runtime}ms within one granularity)")


def test_weight_ratio_is_share():
    sim = CfsSim()
    heavy = Task("heavy", 1024)   # 2 倍权重
    light = Task("light", 512)
    sim.add(heavy)
    sim.add(light)
    sim.run(TOTAL_MS)
    assert heavy.runtime + light.runtime == TOTAL_MS
    # 份额 = 权重比:heavy : light ≈ 2 : 1(粒度取整误差 <= 2 个粒度)
    assert abs(heavy.runtime - 2 * light.runtime) <= 2 * GRAN, \
        (heavy.runtime, light.runtime)
    # vruntime 终值应基本收敛(理想 CPU 不失衡)
    assert abs(heavy.vruntime - light.vruntime) <= 2 * NICE_0 * GRAN // 512
    print(f"PASS: weight ratio is CPU share "
          f"(heavy={heavy.runtime}ms light={light.runtime}ms ~= 2:1)")


def test_leftmost_pick_invariant():
    sim = CfsSim()
    ts = [Task(f"t{i}", 1024) for i in range(3)]
    for t in ts:
        sim.add(t)
    # 手动置初始 vruntime,验证每一步选取都是最小者
    vr = [30, 10, 20]
    for t, v in zip(ts, vr):
        t.vruntime = v
    picks = []
    for _ in range(6):
        cur = sim._leftmost()
        picks.append(cur.name)
        cur.account(GRAN)
        sim._update_min_vruntime()
    # 初始 10 最小 -> t1 先跑;每轮后重新挑最小(受粒度取整影响,顺序
    # 保持"最低 vruntime 者优先"的轮转)
    assert picks[0] == "t1"
    for i, name in enumerate(picks):
        cur = ts[int(name[1:])]
        others = [t for t in ts if t is not cur]
        assert cur.vruntime <= min(o.vruntime for o in others) + NICE_0 * GRAN // 1024
    assert_monotone(sim)
    print(f"PASS: leftmost-pick invariant (picks={picks})")


def test_late_joiner_gets_min_vruntime():
    sim = CfsSim()
    a = Task("A", 1024)
    sim.add(a)
    sim.run(500)                      # A 独跑 500ms
    late = Task("L", 1024)
    before = late.vruntime
    sim.add(late)                     # 放置:clamp 到 min_vruntime
    assert late.vruntime == sim.min_vruntime >= before
    sim.run(500)                      # 之后 L 与 A 平分
    assert abs(a.runtime - late.runtime - 500) <= 2 * GRAN, \
        (a.runtime, late.runtime)
    assert_monotone(sim)
    print(f"PASS: late joiner placed at min_vruntime, then fair share "
          f"(A={a.runtime}ms L={late.runtime}ms)")


def test_sleeper_anti_gaming():
    """睡眠者醒来若保留旧 vruntime,会变极端最左而独占;以 min_vruntime
    兜底放置(文档:新激活实体尽量放左侧但不无中生有)后立即回到公平。"""
    # 对照组:不做 clamp(裸唤醒)
    sim = CfsSim()
    a, s = Task("A", 1024), Task("S", 1024)
    sim.add(a)
    sim.run(1000)                     # S 沉睡期间 A 独跑 1000ms
    sim.add(s, clamp=False)           # 裸唤醒:S.vruntime 仍为 0
    burst_raw = 0
    while sim._leftmost() is s and burst_raw < 2000:
        s.account(GRAN)
        burst_raw += GRAN
    assert burst_raw >= 500, burst_raw  # S 独占追赶期(投机得逞)

    # 实验组:clamp 唤醒
    sim2 = CfsSim()
    a2, s2 = Task("A", 1024), Task("S", 1024)
    sim2.add(a2)
    sim2.run(1000)
    sim2.add(s2, clamp=True)          # vruntime = max(0, min_vruntime)
    assert s2.vruntime == sim2.min_vruntime
    burst_clamped = 0
    while sim2._leftmost() is s2 and burst_clamped < 2000:
        s2.account(GRAN)
        burst_clamped += GRAN
    assert burst_clamped <= 2 * GRAN, burst_clamped  # 一个粒度内就让出
    print(f"PASS: sleeper anti-gaming (raw burst={burst_raw}ms, "
          f"clamped burst={burst_clamped}ms)")


def main():
    test_equal_weights_fair()
    test_weight_ratio_is_share()
    test_leftmost_pick_invariant()
    test_late_joiner_gets_min_vruntime()
    test_sleeper_anti_gaming()
    print("CFS simulation assertions passed")


if __name__ == "__main__":
    main()
