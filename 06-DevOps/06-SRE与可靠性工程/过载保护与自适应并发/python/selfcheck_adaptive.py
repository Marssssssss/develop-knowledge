"""过载保护与自适应并发 —— 自检(纯标准库,离线可跑)。

运行:`python selfcheck_adaptive.py`  期望末行 `PASS n / FAIL 0`

防"假绿"的约束:
1. 期望值手写常量或闭式关系。E4 的稳态 `L = 1/(1−g)²` 是**手推的闭式**,与 400 步
   迭代的结果对照 —— 两条独立路径必须合上。
2. **梯度=1 的临界点**用闭式验:sampleRTT = minRTT × (1 + buffer/100) 时梯度恰好为
   1。这条同时锁住了 buffer 的量纲(除不除 100)。
3. 每条"会生效"的断言配负控:buffer=0 时临界点上移;jitter=0 时全部对齐;
   threshold 触发器在**恰好等于**阈值时不动(严格 >)。
4. 浮点一律 1e-9(迭代收敛处放宽到 1e-3,因为是指数收敛的尾部)。
"""

import adaptive as A

PASS = 0
FAIL = 0
FAILED = []


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append(name)


def close(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) < tol


MR = 50.0
BUF = 10.0

# ------------------------------------------------------- E1 梯度与临界点
ok(close(A.buffer_value(MR, BUF), 5.0), "E1 B = 50 × 10% = 5")
ok(close(A.gradient(MR, BUF, 25.0), 2.2), "E1 sampleRTT=25 → g=2.2")
ok(close(A.gradient(MR, BUF, 50.0), 1.1), "E1 sampleRTT=50 → g=1.1")
ok(close(A.gradient(MR, BUF, 100.0), 0.55), "E1 sampleRTT=100 → g=0.55")
# 闭式临界点:gradient = 1  ⟺  sampleRTT = minRTT × (1 + buffer/100)
crit = MR * (1 + BUF / 100.0)
ok(close(crit, 55.0), "E1 临界 sampleRTT = 55")
ok(close(A.gradient(MR, BUF, crit), 1.0), "E1 临界点处梯度恰好为 1")
ok(A.gradient(MR, BUF, crit - 1) > 1, "E1 临界点左侧梯度 > 1")
ok(A.gradient(MR, BUF, crit + 1) < 1, "E1 临界点右侧梯度 < 1")
# 负控:buffer=0 时临界点就是 minRTT 本身
ok(close(A.gradient(MR, 0.0, MR), 1.0), "E1 负控:buffer=0 时 minRTT 处 g=1")
ok(A.gradient(MR, 0.0, 55.0) < 1, "E1 负控:buffer=0 时 55ms 已收紧")
ok(A.gradient(MR, BUF, 55.0) == 1.0, "E1 同样的 55ms 在 buffer=10% 下刚好不收紧")
try:
    A.gradient(MR, BUF, 0.0)
    ok(False, "E1 sampleRTT=0 应抛错")
except ValueError:
    ok(True, "E1 sampleRTT=0 应抛错")

# ------------------------------------------------------- E2 headroom
ok(close(A.headroom(25.0), 5.0), "E2 headroom(25) = 5")
ok(close(A.headroom(100.0), 10.0), "E2 headroom(100) = 10")
ok(close(A.headroom(1600.0), 40.0), "E2 headroom(1600) = 40")
ok(A.headroom(400.0) > A.headroom(100.0), "E2 headroom 随 limit 单调增")
ok(A.headroom(400.0) / 400.0 < A.headroom(100.0) / 100.0,
   "E2 headroom 占比随 limit 单调降")

# ------------------------------------------------------- E3 梯度>1 无稳态
traj = A.iterate(25.0, MR, BUF, lambda s, l: 50.0, 3.0, 12)
ok(all(traj[i] < traj[i + 1] for i in range(len(traj) - 1)), "E3 梯度>1 时严格递增")
ok(A.fixed_point(1.1) is None, "E3 梯度>1 时闭式稳态为 None")
ok(A.fixed_point(1.0) is None, "E3 梯度=1 时闭式稳态为 None")

# ------------------------------------------------------- E4 稳态闭式 vs 迭代
for srtt, want in ((60.0, 144.0), (80.0, 10.24), (100.0, 1.0 / 0.45 ** 2)):
    g = A.gradient(MR, BUF, srtt)
    fp = A.fixed_point(g)
    it = A.iterate(25.0, MR, BUF, lambda s, l: srtt, 1.0, 400)[-1]
    ok(close(fp, want, 1e-6), "E4 sampleRTT=%.0f 闭式稳态 %.4f" % (srtt, want))
    ok(close(it, fp, 1e-3), "E4 sampleRTT=%.0f 迭代收敛到闭式" % srtt)
    # 稳态处应当满足 L = g·L + sqrt(L)
    ok(close(g * it + A.headroom(it), it, 1e-3), "E4 sampleRTT=%.0f 稳态自洽" % srtt)
ok(close(A.fixed_point(0.5), 4.0), "E4 g=0.5 → L=4")
ok(close(A.fixed_point(0.9), 100.0), "E4 g=0.9 → L=100")

# ------------------------------------------------------- E5 min_limit 钳制
ok(A.next_limit(5.0, MR, BUF, 200.0, 10.0) == 10.0, "E5 低于下限时被钳到 min_limit")
ok(A.next_limit(100.0, MR, BUF, 200.0, 10.0) > 10.0, "E5 高于下限时正常计算")
ok(close(A.next_limit(100.0, MR, BUF, 55.0, 1.0), 100.0 + 10.0),
   "E5 梯度=1 时新增量就是 headroom")

# ------------------------------------------------------- E6 minRTT 重算触发
c = A.MinRttController(min_concurrency=3, trigger_windows=5)
fires = [c.observe(3) for _ in range(5)]
ok(fires[:4] == [False] * 4, "E6 前 4 个窗口不触发")
ok(fires[4] is True, "E6 第 5 个连续窗口触发")
ok(c.at_min_streak == 0, "E6 触发后连击清零")
# 负控:中间出现一次高值会打断连击
c2 = A.MinRttController(3, 5)
for lim in (3, 3, 3, 10, 3, 3):
    c2.observe(lim)
ok(c2.at_min_streak == 2, "E6 负控:高值打断后连击重新计数")
ok(c.observe(100) is False, "E6 负控:远高于下限时连击归零")
ok(A.DEFAULT_MIN_CONCURRENCY == 3, "E6 min_concurrency 缺省 3")
ok(A.MIN_RTT_TRIGGER_WINDOWS == 5, "E6 触发需 5 个连续窗口")

# ------------------------------------------------------- E7 jitter
import random
ok(A.all_hosts_aligned(20, 0.0, random.Random(42)) == 20,
   "E7 jitter=0 时 20 个 host 全部对齐")
ok(A.all_hosts_aligned(20, 50.0, random.Random(42)) < 20,
   "E7 jitter=50% 时不再全部对齐")
ok(A.all_hosts_aligned(20, 50.0, random.Random(42)) <=
   A.all_hosts_aligned(20, 10.0, random.Random(42)),
   "E7 jitter 越大对齐数越少")
st = A.jittered_start(60.0, 0.0, random.Random(1))
ok(close(st, 60.0), "E7 jitter=0 时起点不偏移")
try:
    A.jittered_start(60.0, -1.0, random.Random(1))
    ok(False, "E7 负 jitter 应抛错")
except ValueError:
    ok(True, "E7 负 jitter 应抛错")

# ------------------------------------------------------- E8 两种 trigger
ok(A.threshold_trigger(0.7, 0.7) == 0.0, "E8 恰好等于阈值不触发(严格 >)")
ok(A.threshold_trigger(0.71, 0.7) == 1.0, "E8 超过阈值触发")
ok(A.threshold_trigger(0.0, 0.7) == 0.0, "E8 低压不触发")
ok(A.scaled_trigger(0.5, 0.5, 0.9) == 0.0, "E8 scaled 在 scaling 处为 0")
ok(A.scaled_trigger(0.9, 0.5, 0.9) == 1.0, "E8 scaled 在 saturation 处为 1")
ok(close(A.scaled_trigger(0.7, 0.5, 0.9), 0.5), "E8 scaled 在中间等于 0.5")
ok(close(A.scaled_trigger(0.6, 0.5, 0.9), 0.25), "E8 scaled 线性插值 0.25")
vals = [A.scaled_trigger(p / 100.0, 0.5, 0.9) for p in range(0, 101)]
ok(all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1)), "E8 scaled 单调不减")
ok(A.threshold_trigger(0.69, 0.7) != A.scaled_trigger(0.69, 0.5, 0.9),
   "E8 同一压力下两种触发器取值不同")
try:
    A.scaled_trigger(0.5, 0.9, 0.5)
    ok(False, "E8 saturation <= scaling 应抛错")
except ValueError:
    ok(True, "E8 saturation <= scaling 应抛错")

# ------------------------------------------------------- E9 内存压力
ok(close(A.memory_pressure(8, 16), 0.5), "E9 用量一半 → 0.5")
ok(close(A.memory_pressure(16, 16), 1.0), "E9 打满 → 1.0")
ok(close(A.memory_pressure(8, None), 0.0), "E9 未设 limit → 0")
ok(close(A.memory_pressure(8, 0), 0.0), "E9 limit=0 → 0")
ok(close(A.memory_pressure(8, -1), 0.0), "E9 limit=-1(v1 无限制) → 0")

# ------------------------------------------------------- E10 端到端平衡点
def sample_rtt_fn(step, limit):
    return MR + max(0.0, (limit - 120.0)) * 0.5

traj10 = A.iterate(25.0, MR, BUF, sample_rtt_fn, 1.0, 60)
lim = traj10[-1]
srtt = sample_rtt_fn(0, lim)
g = A.gradient(MR, BUF, srtt)
ok(close(g * lim + A.headroom(lim), lim, 1e-3), "E10 收敛点满足 L = g·L + sqrt(L)")
ok(lim > 120.0, "E10 平衡点落在延迟开始上升的区间之上")
ok(close(traj10[-1], traj10[-2], 1e-6), "E10 末两步已基本不动")
# 从 25 起步时会**过冲**再阻尼收敛(实测 137.29 → 140.47 → 140.11 → 140.149),
# 这是 headroom 恒为正导致的,不是单调爬升 —— 首版断言写"单调"是错的。
ok(max(traj10) > lim, "E10 存在过冲(峰值高于稳态)")
ok(close(max(traj10) - lim, 0.3256, 1e-3), "E10 过冲幅度约 0.33")
ok(len(set(round(v, 6) for v in traj10[-5:])) == 1, "E10 末 5 步稳定在同一值")
ok(traj10.index(max(traj10)) < len(traj10) - 3, "E10 峰值出现在收敛之前")

print("PASS %d / FAIL %d" % (PASS, FAIL))
for n in FAILED:
    print("  FAILED:", n)
