"""过载保护与自适应并发 —— 实验台。运行:`python main.py`"""

import random

import adaptive as A

SEP = "=" * 74
MIN_RTT = 50.0        # ms
BUFFER = 10.0         # buffer_pct = 10


def hdr(name, desc):
    print("\n" + SEP)
    print(name + " — " + desc)
    print(SEP)


def e1():
    hdr("E1", "gradient = (minRTT + B) / sampleRTT,B = minRTT × buffer_pct")
    print("minRTT=%.1fms  buffer=%g%%  →  B=%.1fms" % (MIN_RTT, BUFFER,
                                                       A.buffer_value(MIN_RTT, BUFFER)))
    print("%-12s %-12s %s" % ("sampleRTT", "gradient", "含义"))
    for srtt in (25.0, 50.0, 55.0, 60.0, 100.0, 200.0):
        g = A.gradient(MIN_RTT, BUFFER, srtt)
        tag = "梯度>1,限流放宽" if g > 1 else ("梯度≈1" if abs(g - 1) < 1e-9 else "梯度<1,限流收紧")
        print("%-12.1f %-12.6f %s" % (srtt, g, tag))


def e2():
    hdr("E2", "headroom = sqrt(limit):并发越大,向上试探的步长越大")
    print("%-10s %-12s %-12s" % ("limit", "headroom", "headroom/limit"))
    for lim in (1, 3, 25, 100, 400, 1600):
        h = A.headroom(lim)
        print("%-10d %-12.4f %-12.4f" % (lim, h, h / lim))


def e3():
    hdr("E3", "梯度>1 时并发无限增长 —— headroom 不会让它收敛")
    traj = A.iterate(25.0, MIN_RTT, BUFFER, lambda s, l: 50.0, 3.0, 12)
    print("sampleRTT 恒等于 minRTT(梯度 = 1.1),起始 limit=25:")
    print("  " + " → ".join("%.1f" % v for v in traj[:10]))
    print("  第 12 步: %.1f(仍在增长,不存在稳态)" % traj[-1])


def e4():
    hdr("E4", "梯度<1 时收敛到闭式稳态 L = 1/(1−g)²")
    for srtt in (60.0, 80.0, 100.0, 150.0):
        g = A.gradient(MIN_RTT, BUFFER, srtt)
        fp = A.fixed_point(g)
        traj = A.iterate(25.0, MIN_RTT, BUFFER, lambda s, l: srtt, 1.0, 400)
        print("sampleRTT=%-6.1f g=%.6f  闭式稳态=%-10.4f 迭代 400 步=%-10.4f 差=%.2e"
              % (srtt, g, fp, traj[-1], abs(fp - traj[-1])))


def e5():
    hdr("E5", "负控:buffer=0 时,sampleRTT 略高于 minRTT 就会持续收紧")
    for buf in (0.0, 10.0, 30.0):
        g0 = A.gradient(MIN_RTT, buf, 55.0)     # 比 minRTT 高 10%
        g1 = A.gradient(MIN_RTT, buf, 50.0)     # 恰好等于 minRTT
        print("buffer=%-5.1f%%  sampleRTT=55 → g=%.6f ; sampleRTT=50 → g=%.6f"
              % (buf, g0, g1))
    print("\nbuffer=0 且 sampleRTT>minRTT 时 g<1,并发会一路压到下限;")
    print("buffer 的作用就是给采样延迟留一点正常抖动空间。")


def e6():
    hdr("E6", "minRTT 重算:连续 5 个窗口处在最小并发才触发")
    c = A.MinRttController(min_concurrency=3, trigger_windows=5)
    seq = [3, 3, 3, 3, 3, 3, 10, 3, 3, 3, 3, 3]
    out = []
    for lim in seq:
        out.append((lim, c.observe(lim)))
    for i, (lim, fired) in enumerate(out):
        print("  窗口 %-3d limit=%-4d -> %s" % (i, lim, "触发重算" if fired else "-"))
    print("\n注意:第 4 个窗口(索引 4)就凑满 5 连击并清零;中间出现 10 会把连击打断。")


def e7():
    hdr("E7", "jitter 的作用:不让整个集群同时进入 minRTT 窗口")
    for jp in (0.0, 1.0, 10.0, 50.0):
        rng = random.Random(42)
        aligned = A.all_hosts_aligned(20, jp, rng)
        print("  jitter=%-5.1f%%  20 个 host 中起点几乎同时(±5%%)的有 %2d 个"
              % (jp, aligned))


def e8():
    hdr("E8", "过载管理器:threshold 是方波,scaled 是斜坡")
    print("%-10s %-14s %-14s" % ("pressure", "threshold(.7)", "scaled(.5,.9)"))
    for p in (0.0, 0.3, 0.5, 0.6, 0.7, 0.71, 0.8, 0.9, 1.0):
        print("%-10.2f %-14.1f %-14.6f"
              % (p, A.threshold_trigger(p, 0.7), A.scaled_trigger(p, 0.5, 0.9)))


def e9():
    hdr("E9", "cgroup 内存压力:没配 limit 时压力恒为 0")
    for usage, limit, note in ((8, 16, "用了 50%"), (16, 16, "打满"),
                               (8, None, "未设 limit(v1 -1 / v2 max)")):
        print("  usage=%-4s limit=%-6s -> pressure=%.4f   %s"
              % (usage, limit, A.memory_pressure(usage, limit), note))
    print("\n这条坑很实在:忘了给 cgroup 设内存上限,过载保护会永远认为压力为 0。")


def e10():
    hdr("E10", "端到端:延迟随并发上升时,控制器自己找到平衡点")
    def sample_rtt_fn(step, limit):
        # 并发超过 120 之后,排队让延迟线性上升
        return MIN_RTT + max(0.0, (limit - 120.0)) * 0.5
    traj = A.iterate(25.0, MIN_RTT, BUFFER, sample_rtt_fn, 1.0, 60)
    print("延迟模型:sampleRTT = %.0f + max(0, limit−120) × 0.5" % MIN_RTT)
    print("  前 8 步: " + " → ".join("%.1f" % v for v in traj[:8]))
    print("  末 5 步: " + " → ".join("%.1f" % v for v in traj[-5:]))
    lim = traj[-1]
    print("  收敛处 limit=%.2f  sampleRTT=%.2f  gradient=%.6f"
          % (lim, sample_rtt_fn(0, lim), A.gradient(MIN_RTT, BUFFER, sample_rtt_fn(0, lim))))
    print("  峰值 %.4f 出现在第 %d 步 —— headroom 恒为正,故会**过冲**后阻尼收敛"
          % (max(traj), traj.index(max(traj))))


if __name__ == "__main__":
    e1()
    e2()
    e3()
    e4()
    e5()
    e6()
    e7()
    e8()
    e9()
    e10()
