"""扩展律自检：三式的数值对照、恒等式、退化关系与 UVA/Cornell/hpc101 的原文数值。"""

import math
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from scaling import (  # noqa: E402
    amdahl,
    amdahl_as_usl,
    amdahl_limit,
    amdahl_serial,
    efficiency,
    gustafson,
    gustafson_alt,
    gustafson_cornell_forms,
    gustafson_serial,
    parallel_fraction_for,
    serial_time_share,
    usl_capacity,
    weak_efficiency_limit,
)

FAILED = []
N = 0


def ck(cond, msg):
    global N
    N += 1
    if not cond:
        FAILED.append(msg)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


# -------------------------------------------------- 1. Amdahl 的基本性质
ck(close(amdahl(0.9, 1), 1.0), "N=1 时 S=1")
ck(close(amdahl(1.0, 8), 8.0), "全并行 P=1 -> S=N")
ck(close(amdahl(0.0, 8), 1.0), "全串行 P=0 -> S=1")
# hpc101 的例：90% 可并行，N→∞ 上限 10x
ck(close(amdahl_limit(0.9), 10.0), "P=0.9 上限 10x")
ck(close(amdahl(0.9, 10 ** 7), 10.0, tol=1e-5), "N=1e7 逼近 10x")
# 串行比例写法与可并行比例写法必须一致
for f in (0.0, 0.01, 0.05, 0.1, 0.5):
    for n in (1, 2, 10, 100, 1000):
        ck(close(amdahl_serial(f, n), amdahl(1.0 - f, n)),
           f"f={f},N={n} 两种 Amdahl 写法一致")
# 单调性：N 增加 S 增加但有天花板
prev = 0.0
for n in (1, 2, 4, 8, 16, 64, 256, 1024):
    s = amdahl(0.9, n)
    ck(s > prev, f"P=0.9 时 S 随 N 递增（N={n}）")
    ck(s < 10.0, f"P=0.9 时 S 不超过 10（N={n}）")
    prev = s
# 效率随 N 单调下降
prev = 2.0
for n in (1, 2, 4, 8, 16, 64):
    e = efficiency(amdahl(0.9, n), n)
    ck(e <= prev, f"Amdahl 效率随 N 递减（N={n}）")
    prev = e
ck(close(efficiency(amdahl(0.9, 1), 1), 1.0), "N=1 效率为 1")

# ------------------------------------------------- 2. Gustafson 的基本性质
ck(close(gustafson(0.9, 1), 1.0), "N=1 时 S=1")
ck(close(gustafson(1.0, 8), 8.0), "P=1 -> S=N")
ck(close(gustafson(0.0, 8), 1.0), "P=0 -> S=1")
# hpc101 的例：P=0.9, N=100 -> 90.1
ck(close(gustafson(0.9, 100), 90.1), "P=0.9,N=100 -> 90.1")
# 无上限：随 N 线性
ck(close(gustafson(0.9, 1000), 900.1), "P=0.9,N=1000 -> 900.1")
ck(close(gustafson(0.95, 1000), 950.05), "P=0.95,N=1000 -> 950.05")

# 三种写法恒等（hpc101 / Cornell 各一套）
for P in (0.5, 0.9, 0.95, 0.99):
    for n in (1, 2, 10, 100, 1000):
        ck(close(gustafson(P, n), gustafson_alt(P, n)),
           f"Gustafson 两写法一致（P={P},N={n}）")
        # 换成串行比例口径的写法也必须给出同一个数（这是最易写反的一处）
        ck(close(gustafson_serial(1.0 - P, n), gustafson(P, n)),
           f"串行比例口径的 Gustafson 一致（P={P},N={n}）")
        f1, f2, f3 = gustafson_cornell_forms(1.0 - P, n)
        ck(close(f1, f2) and close(f1, f3),
           f"Cornell 三写法一致（P={P},N={n}）")
        ck(close(f1, gustafson(P, n)), f"Cornell 写法 == hpc101 写法（P={P},N={n}）")

# 弱扩展效率极限 ε → 1 − f
for f in (0.01, 0.05, 0.1, 0.3):
    big = efficiency(gustafson(1.0 - f, 10 ** 6), 10 ** 6)
    ck(abs(big - weak_efficiency_limit(f)) < 1e-5,
       f"f={f} 弱扩展效率收敛到 {weak_efficiency_limit(f)}")

# ------------------------------------ 3. 同一个程序，两个问题的答案差多少
# P=0.95：Amdahl 说上限 20x；Gustafson 说 N=1000 时 950.5x
ck(close(amdahl_limit(0.95), 20.0), "P=0.95 Amdahl 上限 20x")
ck(close(gustafson(0.95, 1000), 950.05), "P=0.95 Gustafson N=1000 -> 950.05")
ck(gustafson(0.95, 1000) > amdahl_limit(0.95) * 40,
   "同一 P 下两式结果可以差一个数量级以上")

# ------------------------------- 4. USL 退化为 Amdahl（Gunther 的定理）
for f in (0.0, 0.01, 0.05, 0.1, 0.25, 0.5):
    for n in (1, 2, 3, 8, 16, 64, 256, 1024, 4096):
        ck(close(usl_capacity(n, alpha=f, beta=0.0, gamma=1.0),
                 amdahl_serial(f, n)),
           f"β=0,γ=1 时 USL == Amdahl（f={f},N={n}）")
# USL 的渐近线 1/α 就是 Amdahl 天花板
for f in (0.05, 0.1, 0.2):
    ck(close(usl_capacity(10 ** 8, alpha=f, beta=0.0, gamma=1.0), 1.0 / f,
             tol=1e-6), f"USL 渐近 1/α（f={f}）")
    ck(close(1.0 / f, amdahl_limit(1.0 - f)), f"1/α == Amdahl 上限（f={f}）")
# α=β=0 时 USL 是理想直线；β>0 时会出现倒退（与 USL demo 的结论衔接）
for n in (1, 10, 100):
    ck(close(usl_capacity(n, alpha=0.0, beta=0.0, gamma=1.0), n),
       f"α=β=0 时 C({n}) = N")
ck(usl_capacity(200, alpha=0.03, beta=0.0002, gamma=1.0) <
   usl_capacity(100, alpha=0.03, beta=0.0002, gamma=1.0), "β>0 时出现倒退")
ck(usl_capacity(200, alpha=0.03, beta=0.0, gamma=1.0) >
   usl_capacity(100, alpha=0.03, beta=0.0, gamma=1.0), "β=0 时永不倒退")
ck(close(amdahl_as_usl(0.1, 64), amdahl(0.9, 64)), "amdahl_as_usl 便捷函数一致")

# ------------------------------------------------------ 5. UVA 的量化结论
# "要扩展到 ~100 进程，并行比例必须接近 99%"
p99 = amdahl(0.99, 100)
ck(abs(p99 - 50.25) < 0.05, f"P=0.99,N=100 -> {p99:.2f}（效率 {p99/100:.2%}）")
ck(efficiency(p99, 100) > 0.5, "99% 并行时 100 进程效率刚过半")
p95 = amdahl(0.95, 100)
ck(efficiency(p95, 100) < 0.2, f"95% 并行时 100 进程效率 {efficiency(p95,100):.3f} < 20%")
# 反解：想要 N=100 上拿到 50x，需要多少可并行比例
need = parallel_fraction_for(50.0, 100)
ck(close(need, 0.98995, tol=1e-4), f"100 进程 50x 需要 P≈{need:.5f}")
ck(close(amdahl(need, 100), 50.0, tol=1e-6), "反解代回得到目标加速比")
ck(parallel_fraction_for(90.0, 100) > need, "目标越高需要的并行比例越高")

# 串行部分在 wallclock 中的占比随 N 上升（Cornell 的"串行最终主导"）
prev = -1.0
for n in (1, 2, 4, 16, 64, 256):
    sh = serial_time_share(0.9, n)
    ck(sh > prev, f"串行占比随 N 上升（N={n}）")
    prev = sh
ck(close(serial_time_share(0.9, 1), 0.1), "N=1 时串行占 10%")

# ------------------------------------------------------------ 6. 输入校验
for bad in ((1.5, 10), (-0.1, 10)):
    try:
        amdahl(*bad)
        ck(False, f"P={bad[0]} 应报错")
    except ValueError:
        ck(True, f"P={bad[0]} 报 ValueError")
try:
    amdahl(0.9, 0)
    ck(False, "N=0 应报错")
except ValueError:
    ck(True, "N=0 报 ValueError")
try:
    gustafson(0.9, -2)
    ck(False, "负 N 应报错")
except ValueError:
    ck(True, "负 N 报 ValueError")
ck(math.isinf(amdahl_limit(1.0)), "P=1 时上限为无穷")
try:
    usl_capacity(10, alpha=-0.5, beta=0.0, gamma=1.0)
    ck(False, "负 α 使分母为负应报错")
except ValueError:
    ck(True, "USL 分母非正报 ValueError")

print(f"assertions: {N}, failed: {len(FAILED)}")
for f in FAILED:
    print("FAILED:", f)
sys.exit(1 if FAILED else 0)
