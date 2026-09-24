"""演示入口：三种计量器在同一条到达序列上的行为差异。"""

from gcra import ContinuousLeakyBucket, FixedWindow, Gcra

ARRI = [0.00, 0.01, 0.02, 0.30, 0.31, 0.32, 0.33, 1.00, 1.01]


def main():
    fw = FixedWindow(limit=3, window=1.0)
    gc = Gcra(rate=3.0, tau=0.0)
    lb = ContinuousLeakyBucket(rate=3.0, tau_limit=0.0)
    print("%-8s %-14s %-14s %s" % ("t", "FixedWindow", "GCRA", "LeakyBucket"))
    for t in ARRI:
        a = fw.arrive(t)
        b, rab = gc.arrive(t)
        c, rac = lb.arrive(t)
        print("%-8.2f %-14s %-14s %s" % (
            t, "pass" if a else "reject",
            "pass" if b else "reject(%.2f)" % rab,
            "pass" if c else "reject(%.2f)" % rac,
        ))
    print("\n突发容量 GCRA(3qps, tau=0.9s) =", Gcra(3.0, 0.9).burst_capacity())


if __name__ == "__main__":
    main()
