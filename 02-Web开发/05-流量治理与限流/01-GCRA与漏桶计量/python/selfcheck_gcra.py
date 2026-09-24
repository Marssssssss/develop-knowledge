"""01-GCRA与漏桶计量 自检。运行：python selfcheck_gcra.py"""

import random
import sys

from gcra import ContinuousLeakyBucket, FixedWindow, Gcra, measure_throughput

OK = []
FAIL = []


def ck(name, cond):
    (OK if cond else FAIL).append(name)


def near(a, b, tol=1e-9):
    return abs(a - b) <= tol


# ---- 1. 发射间隔与容限 ----------------------------------------------------
g = Gcra(rate=10.0, tau=0.0)
ck("T = 1/rate = 0.1s", near(g.T, 0.1))
ck("未收到请求时 next_allowed 为 None", g.next_allowed() is None)
ok, ra = g.arrive(0.0)
ck("首个请求必通过（TAT 以当前时间播种）", ok and near(g.tat, 0.1))
ok, ra = g.arrive(0.05)
ck("tau=0 时 0.05s 到达被拒", not ok)
ck("被拒给出的 retry_after = 0.05", near(ra, 0.05))
ok, ra = g.arrive(0.1)
ck("tau=0 时恰好一个 T 后通过（>= 口径）", ok and near(g.tat, 0.2))

# ---- 2. 规范原文的严格大于 vs 工程常用 >= ---------------------------------
gs = Gcra(rate=10.0, tau=0.0, strict=True)
gs.arrive(0.0)
ok_s, _ = gs.arrive(0.1)
ck("strict（I.371 原文 ta > TAT-tau）在边界上拒绝", not ok_s)
gn = Gcra(rate=10.0, tau=0.0, strict=False)
gn.arrive(0.0)
ok_n, _ = gn.arrive(0.1)
ck("非 strict 在边界上通过（两者结论相反）", ok_n)

# ---- 3. 突发容量与两种 tau 口径 -------------------------------------------
gi = Gcra(rate=10.0, tau=0.4)        # tau = 4T
ck("ITU 口径突发容量 = floor(tau/T)+1 = 5", gi.burst_capacity() == 5)
gb = Gcra(rate=10.0, tau=0.4, buffer_mode="brandur")
ck("Brandur 口径（扣 tau+T）多给一张 = 6", gb.burst_capacity() == 6)
ck("tau=0 时突发容量为 1（无任何突发额度）",
   Gcra(rate=10.0, tau=0.0).burst_capacity() == 1)

# ---- 4. 非一致信元不推进 TAT（拒绝不惩罚） --------------------------------
g = Gcra(rate=10.0, tau=0.0)
g.arrive(0.0)
for _ in range(50):
    ok, ra = g.arrive(0.02)
ck("连续 50 次被拒后 retry_after 仍是 0.08", near(ra, 0.08))
ck("被拒不推进 TAT（TAT 仍为 0.1）", near(g.tat, 0.1))
ok, ra = g.arrive(0.1)
ck("按 retry_after 等待后到达即通过", ok and near(ra, 0.0))

# ---- 5. 与连续状态漏桶逐事件对拍 ------------------------------------------
rng = random.Random(7)
mismatch = 0
for trial in range(300):
    rate = rng.choice([1.0, 2.5, 10.0])
    tau = rng.choice([0.0, 0.05, 0.3, 1.0])
    a = Gcra(rate, tau)
    b = ContinuousLeakyBucket(rate, tau)
    t = 0.0
    for _ in range(120):
        t += rng.random() * rng.choice([0.02, 0.2, 1.5])
        cost = rng.choice([1, 1, 1, 2])
        da, ra_a = a.arrive(t, cost)
        db, ra_b = b.arrive(t, cost)
        if bool(da) != bool(db):
            mismatch += 1
        if not da and not db and not near(ra_a, ra_b, 1e-9):
            mismatch += 1
ck("虚拟调度与连续状态漏桶在 300 组随机序列上逐事件等价", mismatch == 0)

# ---- 6. 稳态吞吐 ----------------------------------------------------------
for rate in (1.0, 5.0, 20.0):
    g = Gcra(rate, tau=0.0)
    n = measure_throughput(g, rate, horizon=10.0)
    ck("rate=%s 时 10s 稳态吞吐 %d 命中 %d" % (rate, n, rate * 10),
       abs(n - rate * 10) <= 1)

# ---- 7. 时间桶的边界双倍突发 ----------------------------------------------
fw = FixedWindow(limit=60, window=1.0)
first = sum(1 for _ in range(60) if fw.arrive(0.9))
second = sum(1 for _ in range(60) if fw.arrive(1.1))
ck("时间桶 t=0.9 放 60 次", first == 60)
ck("时间桶 t=1.1（新窗口）又放 60 次", second == 60)
g = Gcra(rate=60.0, tau=0.0)
burst = 0
while True:
    ok, _ = g.arrive(0.9)
    if not ok:
        break
    burst += 1
ok2, _ = g.arrive(1.1)
ck("GCRA(tau=0) 同时刻只放 1 次", burst == 1)
ck("GCRA 在 t=1.1 也只放 1 次（0.2s 内共 2 次，无翻倍）", ok2)

# ---- 8. 时钟回拨与不攒信用 ------------------------------------------------
g = Gcra(rate=1.0, tau=20.0)
g.arrive(10.0)
ck("大容限下回拨到 t=5 仍被判一致", g.arrive(5.0)[0])
ck("回拨后 TAT = max(TAT, ta) + T = 12（不倒退到 6）", near(g.tat, 12.0))
g = Gcra(rate=10.0, tau=0.4)
g.arrive(0.0)
n_idle = 0
while g.arrive(1000.0)[0]:
    n_idle += 1
ck("闲置 1000s 后突发额度不增长（<=5）", n_idle <= 5)

if FAIL:
    print("FAILED %d:" % len(FAIL))
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("gcra selfcheck OK: %d assertions" % len(OK))
