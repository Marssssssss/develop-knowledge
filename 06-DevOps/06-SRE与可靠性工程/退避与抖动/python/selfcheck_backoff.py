"""退避与抖动 —— 自检(纯标准库,离线可跑)。

运行:`python selfcheck_backoff.py`  期望末行 `PASS n / FAIL 0`

防"假绿"的约束:
1. 期望值手写常量或闭式关系;E5 的 `N(N+1)/2` 是**手推的闭式**,不是跑出来的。
2. **分辨力负控**:E6 有一条"连续随机下四种策略会给出同一个数字"的记录 —— 那是
   第一版模型的缺陷(已用时间槽离散化修掉)。自检里显式断言"三种 jitter 的调用
   次数**互不相等**",否则模型退化成常数也能全绿。
3. 随机量一律做**多种子**断言,不靠单个种子下结论(E7 跑 8 个种子)。
4. 有状态(Decorr)与无状态(expo/equal/full)分别用"换 n 结果不变/变"来验。
"""

import random

import backoff as BK
import contention as C

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


BASE, CAP = 5.0, 2000.0

# ------------------------------------------------------- E1 expo 与封顶
b = BK.make("expo", BASE, CAP)
want = [5, 10, 20, 40, 80, 160, 320, 640, 1280, 2000, 2000]
for n, w in enumerate(want):
    ok(close(b.backoff(n), float(w)), "E1 expo(%d) = %d" % (n, w))
ok(b.backoff(9) == CAP and b.backoff(10) == CAP, "E1 n>=9 后恒为 cap")
ok(BK.make("expo", BASE, CAP).expo(100) == CAP, "E1 极大的 n 仍封顶")

# ------------------------------------------------------- E2 取值区间
rng = random.Random(7)
stats = {}
for name in ("expo", "equal", "full", "decorr"):
    lo, hi, tot = None, None, 0.0
    for _ in range(20000):
        v = BK.make(name, BASE, CAP, rng).backoff(3)  # n=3 → expo=40
        lo = v if lo is None else min(lo, v)
        hi = v if hi is None else max(hi, v)
        tot += v
    stats[name] = (lo, hi, tot / 20000.0)

ok(close(stats["expo"][0], 40.0) and close(stats["expo"][1], 40.0),
   "E2 纯指数退避是确定值 40")
ok(stats["equal"][0] >= 20.0 - 1e-9, "E2 Equal Jitter 下界是 v/2 = 20")
ok(stats["equal"][1] < 40.0, "E2 Equal Jitter 上界小于 v = 40")
ok(stats["full"][0] < 1.0, "E2 Full Jitter 可以睡到接近 0")
ok(stats["full"][1] < 40.0, "E2 Full Jitter 上界小于 v")
ok(stats["decorr"][0] >= BASE - 1e-9, "E2 Decorr 下界恒为 base = 5")
ok(stats["decorr"][1] <= BASE * 3 + 1e-9, "E2 Decorr 首步上界是 base*3 = 15")
ok(close(stats["expo"][2], 40.0, 1e-6), "E2 expo 均值 40")
ok(close(stats["equal"][2], 30.0, 0.5), "E2 equal 均值约 30(3v/4)")
ok(close(stats["full"][2], 20.0, 0.5), "E2 full 均值约 20(v/2)")
ok(stats["full"][2] < stats["equal"][2] < stats["expo"][2], "E2 均值 full < equal < expo")

# ------------------------------------------------------- E3 有状态 vs 无状态
r1 = random.Random(11)
v_a = BK.make("decorr", BASE, CAP, r1).backoff(0)
r2 = random.Random(11)
v_b = BK.make("decorr", BASE, CAP, r2).backoff(99)
ok(close(v_a, v_b), "E3 Decorr 忽略 n(同种子同状态结果相同)")
# 负控:无状态策略换 n 必须变
for name in ("full", "equal"):
    x = BK.make(name, BASE, CAP, random.Random(5)).backoff(1)
    y = BK.make(name, BASE, CAP, random.Random(5)).backoff(6)
    ok(x != y, "E3 负控:%s 换 n 结果会变" % name)
# Decorr 的状态确实在推进:同一实例连续调用不相等
inst = BK.make("decorr", BASE, CAP, random.Random(11))
seq = [inst.backoff(0) for _ in range(6)]
ok(len(set(seq)) == 6, "E3 Decorr 连续调用值各不相同(状态在走)")

# ------------------------------------------------------- E4 单调性
inst = BK.make("decorr", BASE, CAP, random.Random(3))
prev, drops = None, 0
for _ in range(30):
    v = inst.backoff(0)
    if prev is not None and v < prev:
        drops += 1
    prev = v
ok(drops > 0, "E4 Decorr 会下降(非单调)")
e_inst = BK.make("expo", BASE, CAP)
vals = [e_inst.backoff(n) for n in range(9)]
ok(all(vals[i] < vals[i + 1] for i in range(len(vals) - 1)), "E4 负控:指数退避严格递增")
ok(BK.make("none", BASE, CAP).backoff(7) == 0.0, "E4 NoBackoff 恒为 0")

# ------------------------------------------------------- E5 闭式:N(N+1)/2
for n in (10, 30, 50, 80, 100):
    calls, _ = C.simulate(n, "none")
    ok(calls == n * (n + 1) // 2, "E5 N=%d 时调用次数 = N(N+1)/2 = %d"
       % (n, n * (n + 1) // 2))
ok(C.simulate(100, "none")[0] == 5050, "E5 N=100 → 5050")
ok(C.simulate(10, "none")[0] == 55, "E5 N=10 → 55")
# 工作量随 N² 增长:把 N 翻倍,calls 约翻 4 倍
c50, _ = C.simulate(50, "none")
c100, _ = C.simulate(100, "none")
ok(3.5 < c100 / float(c50) < 4.5, "E5 N 翻倍时工作量约 4 倍(N²)")

# ------------------------------------------------------- E6 各策略对比
res = {n: C.simulate(100, n)[0] for n in BK.ALL}
res_t = {n: C.simulate(100, n)[1] for n in BK.ALL}
ok(res["none"] == 5050 and res["expo"] == 5050, "E6 无 jitter 的两档工作量相同")
ok(res_t["expo"] > res_t["none"] * 10, "E6 纯指数退避耗时远超不退避")
ok(len(set(res[n] for n in ("equal", "full", "decorr"))) == 3,
   "E6 分辨力:三种 jitter 的调用次数互不相等")
ok(res["equal"] > res["full"], "E6 Equal Jitter 比 Full Jitter 多做")
ok(res["full"] > res["decorr"], "E6 Full Jitter 比 Decorr 多做")
ok(res["full"] * 5 < res["none"], "E6 Full Jitter 工作量不到 none 的 1/5")
for n in ("equal", "full", "decorr"):
    ok(res[n] < res["none"], "E6 %s 工作量小于 none" % n)

# ------------------------------------------------------- E7 多种子稳定性
rows = []
for s in range(8):
    rows.append({n: C.simulate(100, n, seed=s)[0] for n in BK.ALL})
ok(all(r["equal"] > r["full"] for r in rows), "E7 8 个种子下 equal > full 恒成立")
ok(all(r["full"] > r["decorr"] for r in rows), "E7 8 个种子下 full > decorr 恒成立")
ok(all(r["full"] * 5 < r["none"] for r in rows), "E7 8 个种子下 jitter 显著优于 none")
# 负控:确定型策略跨种子零方差
ok(len(set(r["none"] for r in rows)) == 1, "E7 负控:none 跨种子结果恒定")
ok(len(set(r["full"] for r in rows)) > 1, "E7 full 跨种子有波动(确实是随机的)")
spread = max(r["full"] for r in rows) - min(r["full"] for r in rows)
ok(spread * 4 < sum(r["full"] for r in rows) / 8.0, "E7 full 的跨种子波动小于均值的 1/4")

# ------------------------------------------------------- E8 时间槽的必要性
# 负控:把 slot 设得极小(等同不离散),三种 jitter 会退化成同一个数字
same = {n: C.simulate(100, n, slot=1e-9)[0] for n in ("equal", "full", "decorr")}
ok(len(set(same.values())) == 1, "E8 负控:不离散时三种 jitter 结果完全相同")
ok(list(same.values())[0] == 199, "E8 负控:不离散时恰好 2N-1 = 199")
ok(res["full"] != 199, "E8 加时间槽后不再是 199(模型有分辨力)")

# ------------------------------------------------------- 边界与异常
try:
    BK.make("nope")
    ok(False, "X 未知策略应抛错")
except ValueError:
    ok(True, "X 未知策略应抛错")
try:
    BK.Backoff(5, 2000).backoff(1)
    ok(False, "X 基类 backoff 未实现应抛错")
except NotImplementedError:
    ok(True, "X 基类 backoff 未实现应抛错")
ok(close(BK.make("expo", BASE, CAP).backoff(0), BASE), "X n=0 时退避等于 base")
# slot 向上取整:重试时刻必须是 slot 的整数倍
_, tm = C.simulate(20, "full", slot=2.0)
ok(close(tm % 2.0, 0.0, 1e-6) or close(tm % 2.0, 2.0, 1e-6),
   "X slot=2 时完成时刻落在槽边界上")

print("PASS %d / FAIL %d" % (PASS, FAIL))
for n in FAILED:
    print("  FAILED:", n)
