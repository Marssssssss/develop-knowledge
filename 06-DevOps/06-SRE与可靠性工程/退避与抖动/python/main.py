"""退避与抖动 —— 实验台。运行:`python main.py`"""

import random

import backoff as BK
import contention as C

SEP = "=" * 74
BASE, CAP = 5.0, 2000.0


def hdr(name, desc):
    print("\n" + SEP)
    print(name + " — " + desc)
    print(SEP)


def e1():
    hdr("E1", "expo(n) = min(cap, 2^n * base):第 9 次尝试撞到 cap")
    b = BK.make("expo", BASE, CAP)
    for n in range(0, 11):
        v = b.backoff(n)
        print("  n=%-3d expo=%-10.1f %s" % (n, v, "(已封顶)" if v == CAP else ""))


def e2():
    hdr("E2", "四种策略在同一 n 下的取值区间(各 20000 次采样)")
    rng = random.Random(7)
    for name in ("expo", "equal", "full", "decorr"):
        lo, hi, tot = None, None, 0.0
        for _ in range(20000):
            v = BK.make(name, BASE, CAP, rng).backoff(3)  # n=3 → expo=40
            lo = v if lo is None else min(lo, v)
            hi = v if hi is None else max(hi, v)
            tot += v
        print("  %-8s n=3: 最小 %8.3f  最大 %8.3f  均值 %8.3f" % (name, lo, hi, tot / 20000))


def e3():
    hdr("E3", "Decorr 是有状态的:同一个实例上 n 完全不起作用")
    rng = random.Random(11)
    b = BK.make("decorr", BASE, CAP, rng)
    seq = [b.backoff(n) for n in (0, 1, 2, 3, 4, 5)]
    print("  连续调用(内部状态推进):", " ".join("%.3f" % v for v in seq))
    # 对照:同样的 rng 与初始状态,传不同的 n 必须得到同一个值
    r1 = random.Random(11)
    v_a = BK.make("decorr", BASE, CAP, r1).backoff(0)
    r2 = random.Random(11)
    v_b = BK.make("decorr", BASE, CAP, r2).backoff(99)
    print("  同种子同状态下 backoff(0)  = %.6f" % v_a)
    print("  同种子同状态下 backoff(99) = %.6f" % v_b)
    print("  两者是否相等:", v_a == v_b, " → n 被忽略")


def e4():
    hdr("E4", "Decorr 不是单调的:下界恒为 base,可以突然掉回 5")
    rng = random.Random(3)
    b = BK.make("decorr", BASE, CAP, rng)
    drops = 0
    prev = None
    seq = []
    for _ in range(30):
        v = b.backoff(0)
        seq.append(v)
        if prev is not None and v < prev:
            drops += 1
        prev = v
    print("  前 12 次:", " ".join("%.2f" % v for v in seq[:12]))
    print("  30 次里下降的次数:", drops, "(指数退避永远不下降)")


def e5():
    hdr("E5", "OCC 竞争:不做退避时工作量随 N² 增长")
    print("%-8s %-10s %-12s %-12s" % ("N", "调用次数", "calls/N", "calls/N²"))
    for n in (10, 30, 50, 80, 100):
        calls, tm = C.simulate(n, "none")
        print("%-8d %-10d %-12.2f %-12.3f" % (n, calls, calls / n, calls / float(n * n)))


def e6():
    hdr("E6", "100 个客户端时,各策略的工作量(种子 20260921)")
    print("%-10s %-12s %-14s %-12s" % ("策略", "调用次数", "相对 none", "完成时间ms"))
    base_calls = None
    for name in BK.ALL:
        calls, tm = C.simulate(100, name)
        if base_calls is None:
            base_calls = calls
        print("%-10s %-12d %-14s %-12.1f"
              % (name, calls, "%.2f%%" % (100.0 * calls / base_calls), tm))


def e7():
    hdr("E7", "工作量随 N 的曲线(每种策略 5 个规模)")
    print("%-8s %s" % ("N", "  ".join("%-9s" % n for n in BK.ALL)))
    for n in (10, 30, 50, 80, 100):
        row = [C.simulate(n, name)[0] for name in BK.ALL]
        print("%-8d %s" % (n, "  ".join("%-9d" % v for v in row)))


def e8():
    hdr("E8", "同一策略换 8 个随机种子:看结论是否稳定")
    print("%-10s %-24s %-12s" % ("策略", "8 个种子的调用次数范围", "相对幅度"))
    for name in ("none", "full", "decorr"):
        vals = [C.simulate(100, name, seed=s)[0] for s in range(8)]
        lo, hi = min(vals), max(vals)
        print("%-10s %-24s %-12s"
              % (name, "%d ~ %d" % (lo, hi),
                 "%.1f%%" % (100.0 * (hi - lo) / max(1, lo))))


if __name__ == "__main__":
    e1()
    e2()
    e3()
    e4()
    e5()
    e6()
    e7()
    e8()
