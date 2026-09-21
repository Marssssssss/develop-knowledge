"""SLI 窗口与聚合口径 —— 实验台(python/main.py)。

对应 `prom_window.py`(Prometheus `extrapolatedRate` 转写)与 `openslo.py`
(OpenSLO v1 窗口与 ratioMetric 口径)。所有数字来自**实跑**,不是推算。

运行:`python main.py`
"""

import datetime as dt

import openslo as O
import prom_window as P

SEP = "=" * 74


def sec(t):
    """把 'YYYY-MM-DD HH:MM:SS' 的墙上时间转成距 EPOCH 的秒(float)。"""
    return dt.datetime.strptime(t, "%Y-%m-%d %H:%M:%S").timestamp()


def hdr(name, desc):
    print("\n" + SEP)
    print(name + " — " + desc)
    print(SEP)


# ---------------------------------------------------------------- E1 边界

def e1():
    hdr("E1", "区间向量是左开右闭 (ts-range, ts]")
    # 每 30 秒一个点,求值时刻 60,窗口 60 秒。左边界 0、右边界 60 各放一个点。
    series = [(0.0, 10.0), (30.0, 40.0), (60.0, 70.0)]
    sel = P.select_range(series, 60.0, 60.0)
    print("series      :", series)
    print("select [60s] :", sel)
    print("左边界 t=0 是否被选中 :", any(t == 0.0 for t, _ in sel))
    print("右边界 t=60 是否被选中:", any(t == 60.0 for t, _ in sel))
    print("选中点数    :", len(sel))


# ---------------------------------------------------------------- E2 rate/increase

def e2():
    hdr("E2", "increase 就是 rate 乘窗口秒数(源码里同一条 factor,只差 /range)")
    series = [(i * 15.0, 100.0 * i) for i in range(5)]  # 0,15,30,45,60
    sel = P.select_range(series, 60.0, 60.0)
    r = P.rate(sel, 60.0, 60.0)
    inc = P.increase(sel, 60.0, 60.0)
    print("rate     :", r)
    print("increase :", inc)
    print("rate*60  :", r * 60)
    print("差值     :", abs(inc - r * 60))


# ---------------------------------------------------------------- E3 外推非整数

def e3():
    hdr("E3", "increase 会外推到整段区间,故整数计数器也能给出非整数结果")
    # 计数器每次 +1(整数增量),但采样点在 25/37/40,平均间隔 7.5s。
    # 首点距左边界 15s > 阈值 8.25s,故左端退化为 avg/2 = 3.75s 的外推。
    series = [(10.0, 100.0), (25.0, 101.0), (37.0, 102.0), (40.0, 103.0)]
    sel = P.select_range(series, 40.0, 30.0)  # (10, 40]
    inc = P.increase(sel, 40.0, 30.0)
    print("选中点    :", sel)
    print("裸差值    :", sel[-1][1] - sel[0][1], "(整数)")
    print("increase  :", inc, "(非整数 —— 外推所致)")
    print("手工核对  : 2 * (15 + 3.75 + 0) / 15 = 2.5")

    # 反例:采样等距且贴住边界时 factor 恰好为整数,外推**看不出来**。
    even = [(10.0, 100.0), (20.0, 101.0), (30.0, 102.0), (40.0, 103.0)]
    sel2 = P.select_range(even, 40.0, 30.0)
    print("等距对照  : 选中=%s increase=%s(恰好整数,外推被整除掩盖)"
          % (sel2, P.increase(sel2, 40.0, 30.0)))


# ---------------------------------------------------------------- E4 外推阈值

def e4():
    hdr("E4", "extrapolationThreshold = 平均间隔 * 1.1;超了就退化为半间隔")
    cases = [
        ("贴着左右边界(15/45/60)", [(15.0, 10.0), (45.0, 40.0), (60.0, 55.0)], 60.0),
        ("首点远离左边界(40/60) ", [(40.0, 10.0), (60.0, 55.0)], 60.0),
        ("三点密集中部(20/40/60)", [(20.0, 10.0), (40.0, 30.0), (60.0, 55.0)], 60.0),
    ]
    for name, series, ts in cases:
        sel = P.select_range(series, ts, 60.0)
        first_t = sel[0][0]
        last_t = sel[-1][0]
        avg = (last_t - first_t) / (len(sel) - 1)
        d_start = first_t - (ts - 60.0)
        print("\n%s  选中=%s" % (name, sel))
        print("  avg=%.4f  threshold=%.4f  durationToStart=%.4f  -> %s"
              % (avg, avg * 1.1, d_start,
                 "完全外推" if d_start < avg * 1.1 else "退化为 avg/2"))
        print("  increase=%.6f  rate=%.6f"
              % (P.increase(sel, ts, 60.0), P.rate(sel, ts, 60.0)))


# ---------------------------------------------------------------- E5 计数器回绕

def e5():
    hdr("E5", "计数器回绕:后点小于前点则把前点整个值加回来")
    # 45s 时进程重启,计数器归零后一路涨到 60。窗口内真的发生了一次回绕。
    series = [(0.0, 100.0), (30.0, 130.0), (45.0, 10.0), (60.0, 60.0)]
    sel = P.select_range(series, 60.0, 60.0)  # (0, 60]
    proper = P.increase(sel, 60.0, 60.0, is_counter=True)
    naive = P.increase(sel, 60.0, 60.0, is_counter=False)
    print("选中点        :", sel)
    print("裸差值        :", sel[-1][1] - sel[0][1], "(负数 —— 不修正就是错的)")
    print("按 counter 修正:", proper,
          "  ← 裸和 60(=10+50),外推因子 1.25,故 75")
    print("按 gauge 不修正:", naive)
    print("两者之差      :", proper - naive)

    # 负控:没有回绕的序列,两种口径必须**完全一致**,否则修正逻辑本身写错了。
    clean = [(0.0, 100.0), (30.0, 130.0), (45.0, 145.0), (60.0, 160.0)]
    sel2 = P.select_range(clean, 60.0, 60.0)
    a = P.increase(sel2, 60.0, 60.0, is_counter=True)
    b = P.increase(sel2, 60.0, 60.0, is_counter=False)
    print("\n负控(无回绕) : counter=%s gauge=%s 差=%s" % (a, b, a - b))


# ---------------------------------------------------------------- E6 irate

def e6():
    hdr("E6", "irate 只看最后两点且不外推 —— 与 rate 不是一回事")
    # 前 50 秒很慢,最后 2 秒突然爆发
    series = [(0.0, 0.0), (10.0, 10.0), (20.0, 20.0), (58.0, 30.0), (60.0, 200.0)]
    sel = P.select_range(series, 60.0, 60.0)
    print("选中点  :", sel)
    print("rate    : %.4f /s" % P.rate(sel, 60.0, 60.0))
    print("irate   : %.4f /s" % P.irate(sel))
    print("比值    : %.4f" % (P.irate(sel) / P.rate(sel, 60.0, 60.0)))


# ---------------------------------------------------------------- E7 窗口长度

def e7():
    hdr("E7", "rolling 长度恒定,calendar 长度随日历变(1M 在 2 月 28 天、7 月 31 天)")
    start = O.parse_start("2026-01-01 00:00:00")
    for label, now in (("2 月", dt.datetime(2026, 2, 10)),
                       ("7 月", dt.datetime(2026, 7, 10))):
        lo, hi = O.calendar_window(now, start, O.Duration("1M"))
        print("calendar 1M @%s : %s ~ %s  长度 %d 天"
              % (label, lo.date(), hi.date(), (hi - lo).days))
    now = dt.datetime(2026, 7, 10)
    lo, hi = O.rolling_window(now.timestamp(), O.Duration("30d"))
    print("rolling 30d @7月 : 长度 %d 天(恒定)" % round((hi - lo) / 86400))


# ---------------------------------------------------------------- E8 窗口选型的后果

def e8():
    """一次 2 小时全站故障之后,两种窗口的 SLI 恢复轨迹完全不同。"""
    hdr("E8", "同一段故障:rolling 30d 拖 30 天,calendar 1M 跨月即清零")
    base = dt.datetime(2026, 3, 1, 0, 0, 0)
    start = O.parse_start("2026-03-01 00:00:00")
    outage_lo = dt.datetime(2026, 3, 20, 10, 0, 0)
    outage_hi = dt.datetime(2026, 3, 20, 12, 0, 0)  # 2 小时全失败
    rows = []
    for day in (20, 21, 25, 31, 32, 40, 45, 50):
        now = base + dt.timedelta(days=day)
        lo_r, hi_r = O.rolling_window(now.timestamp(), O.Duration("30d"))
        lo_c, hi_c = O.calendar_window(now, start, O.Duration("1M"))
        good = bad = 0
        t = lo_r
        while t < hi_r:
            m = dt.datetime.fromtimestamp(t)
            if outage_lo <= m < outage_hi:
                bad += 1
            else:
                good += 1
            t += 3600
        # calendar 窗口:只统计窗口内的小时(窗口起点可能晚于 rolling 起点)
        cg = cb = 0
        t = lo_c.timestamp()
        while t < hi_c.timestamp() and t <= now.timestamp():
            m = dt.datetime.fromtimestamp(t)
            if outage_lo <= m < outage_hi:
                cb += 1
            else:
                cg += 1
            t += 3600
        rows.append((now.date(), good / (good + bad), cg / (cg + cb)))
    print("%-12s %-14s %-14s" % ("日期", "rolling 30d", "calendar 1M"))
    for d, a, b in rows:
        print("%-12s %.6f      %.6f" % (d, a, b))
    print("\n结论:4/1 之后 calendar 的故障小时整体掉出窗口,SLI 立刻回到 1.0;")
    print("      rolling 要等 30 天整,故障才会滑出 (now-30d, now]。")


# ---------------------------------------------------------------- E9 rawType

def e9():
    hdr("E9", "rawType 是 success 还是 failure,决定要不要取补")
    raw = 0.003  # 存的是 0.003
    print("存 0.003 按 success 读 :", O.sli_from_raw(raw, "success"))
    print("存 0.003 按 failure 读 :", O.sli_from_raw(raw, "failure"))
    print("两者之差                :",
          O.sli_from_raw(raw, "failure") - O.sli_from_raw(raw, "success"))
    print("即:把 failure 当 success 用,99.7% 的 SLI 会被读成 0.3%。")


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
