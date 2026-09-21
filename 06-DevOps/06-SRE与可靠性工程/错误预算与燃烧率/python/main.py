"""错误预算与燃烧率 —— 实验台。运行:`python main.py`"""

import budget as B
import method as M

SEP = "=" * 74
DAY = B.DAY


def hdr(name, desc):
    print("\n" + SEP)
    print(name + " — " + desc)
    print(SEP)


# Google 在 SRE workbook 给的默认档位(sloth 源码注释 + 其 windows YAML 实读)
GOOGLE = [
    ("page_quick", 2.0, 5 * 60.0, 1 * 3600.0),
    ("page_slow", 5.0, 30 * 60.0, 6 * 3600.0),
    ("ticket_quick", 10.0, 2 * 3600.0, 1 * DAY),
    ("ticket_slow", 10.0, 6 * 3600.0, 3 * DAY),
]


def e1():
    hdr("E1", "错误预算 = 1 - SLO:每多一个九,预算少一个数量级")
    print("%-10s %-16s %-16s %-16s" % ("SLO", "预算(30d)", "预算(28d)", "预算(7d)"))
    for slo in (0.99, 0.999, 0.9999, 0.99999):
        row = [B.budget_minutes(slo, p * DAY) for p in (30, 28, 7)]
        print("%-10s %-16.4f %-16.4f %-16.4f"
              % ("%.5f" % slo, row[0], row[1], row[2]))
    print("\n99.9%% / 30 天 = %.2f 分钟(经典数字)" % B.budget_minutes(0.999, 30 * DAY))


def e2():
    hdr("E2", "burn rate = 错误率 / 错误预算率;1 表示恰好按 SLO 的速度花")
    for er in (0.0001, 0.001, 0.005, 0.0144, 0.05, 0.1):
        print("错误率 %-8.4f -> burn rate %10.4f  (SLO=99.9%%)"
              % (er, B.burn_rate(er, 0.999)))


def e3():
    hdr("E3", "Sloth getBurnRateFactor:各档位需要的燃烧率(30d 与 28d 不同!)")
    for period_days in (30, 28):
        period = period_days * DAY
        print("\nSLO 周期 %dd:" % period_days)
        print("  %-14s %-8s %-8s %-10s" % ("档位", "预算%", "长窗", "燃烧率"))
        for name, pct, short, long_ in GOOGLE:
            w = B.Window(pct, short, long_)
            print("  %-14s %-8.1f %-8s %-10.4f"
                  % (name, pct, _fmt_dur(long_), w.speed(period)))


def _fmt_dur(s):
    if s >= DAY:
        return "%gd" % (s / DAY)
    if s >= 3600:
        return "%gh" % (s / 3600)
    return "%gm" % (s / 60)


def e4():
    hdr("E4", "交叉验证:由燃烧率反推出的错误率,在长窗内恰好花掉 pct% 预算")
    period = 30 * DAY
    slo = 0.999
    print("%-14s %-10s %-14s %-14s" % ("档位", "燃烧率", "反推错误率", "长窗内耗预算"))
    for name, pct, short, long_ in GOOGLE:
        br = B.burn_rate_factor(pct, period, long_)
        er = br * B.error_budget(slo)
        consumed = B.budget_consumed(er, slo, long_, period)
        print("%-14s %-10.4f %-14.6f %-14.6f" % (name, br, er, consumed))
    print("\n最后一列应等于「预算%% / 100」—— 两条独立公式必须合上。")


def e5():
    hdr("E5", "烧光整期预算的时间 = 周期 / 燃烧率")
    period = 30 * DAY
    for name, pct, short, long_ in GOOGLE:
        br = B.burn_rate_factor(pct, period, long_)
        tte = B.time_to_exhaustion(br, period)
        print("%-14s 燃烧率 %8.4f -> TTE %8.2f 小时 (%.3f 天)"
              % (name, br, tte / 3600.0, tte / DAY))


def e6():
    hdr("E6", "燃烧率与 SLO 无关,但阈值与 SLO 有关(告警表达式里要乘预算率)")
    period = 30 * DAY
    br = B.burn_rate_factor(2.0, period, 3600.0)
    print("page_quick 的燃烧率(与 SLO 无关)      : %.4f" % br)
    for slo in (0.99, 0.999, 0.9999):
        print("  SLO=%.4f 时告警阈值 = br*(1-SLO) = %.6f" % (slo, br * B.error_budget(slo)))


def e7():
    hdr("E7", "三种 budgetingMethod 在流量不均时差到离谱")
    # 12 个切片:11 个切片各 2 个请求(其中 1 个失败 1 个),1 个切片 1000 个请求全成功
    good = [1] * 11 + [1000]
    total = [2] * 11 + [1000]
    res = M.all_methods(good, total, 0.99)
    print("切片 good/total:", list(zip(good, total)))
    for k, v in res.items():
        print("  %-18s %.6f" % (k, v))
    print("\nOccurrences 只看事件总量(1011/1022),把 11 个烂切片稀释掉了;")
    print("Timeslices 等权计数切片,11/12 都不过线故只有 1/12;")
    print("RatioTimeslices 取切片比值的等权平均,落在两者之间。")


def e8():
    hdr("E8", "负控:什么条件下三种口径才会收敛?—— 不是「流量均匀」")
    print("(a) 每个切片的比值只取 0 或 1(99 个切片全好、1 个切片全坏)时:")
    # 99 个切片 100/100 全成功,1 个切片 0/100 全失败
    g = [100] * 99 + [0]
    t = [100] * 100
    same = M.all_methods(g, t, 0.99)
    for k, v in same.items():
        print("    %-18s %.6f" % (k, v))
    print("    → 三者重合于 0.99:二值化不损失信息的唯一情形")
    print("(a2) 但把同样的 99% 摊到每个切片(每个切片 99/100),Timeslices 立刻跳到:")
    flat = M.all_methods([99] * 100, [100] * 100, 0.99)
    print("    timeslices = %.4f(每个切片都过线,整期就是 1.0)" % flat["timeslices"])

    print("\n(b) 流量**均匀**、但比值在 target 附近抖动(98/99/100/97/99):")
    good = [98, 99, 100, 97, 99]
    total = [100] * 5
    res = M.all_methods(good, total, 0.99)
    for k, v in res.items():
        print("    %-18s %.6f" % (k, v))

    print("\n结论:(b) 的流量是完全均匀的,但 Timeslices 仍掉到 %.4f —— 因为它先做"
          % res["timeslices"])
    print("      「过线/不过线」的二值判定再计数。三种口径收敛的**唯一**条件是")
    print("      (a) 那种「切片比值只取 0 或 1」;(a2) 说明同样是 99%%,均匀摊开")
    print("      与集中在一个切片上,在 Timeslices 口径下分别是 1.0 与 0.99。")


def e9():
    hdr("E9", "timeSliceTarget 只影响 Timeslices,不影响另外两种")
    good = [1] * 11 + [1000]
    total = [2] * 11 + [1000]
    print("%-10s %-14s %-18s %-18s" % ("target", "Timeslices", "Occurrences", "RatioTimeslices"))
    for t in (0.5, 0.9, 0.99, 1.0):
        print("%-10.2f %-14.6f %-18.6f %-18.6f"
              % (t, M.timeslices(good, total, t),
                 M.occurrences(good, total), M.ratio_timeslices(good, total)))


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
