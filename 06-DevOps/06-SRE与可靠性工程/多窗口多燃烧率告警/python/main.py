"""多窗口多燃烧率告警 —— 实验台。运行:`python main.py`"""

import mwmb as W

SEP = "=" * 74
SLO = 0.999
PERIOD = 30 * W.DAY
MIN = 60.0
WINDOWS = [5 * MIN, 30 * MIN, 2 * 3600.0, 6 * 3600.0,
           1 * 3600.0, 1 * W.DAY, 3 * W.DAY]


def hdr(name, desc):
    print("\n" + SEP)
    print(name + " — " + desc)
    print(SEP)


def e1():
    hdr("E1", "四档阈值:燃烧率 × 错误预算率(SLO 99.9%)")
    t = W.thresholds(SLO, PERIOD)
    print("%-14s %-6s %-6s %-8s %-14s" % ("档位", "预算%", "长窗", "燃烧率", "错误率阈值"))
    for name, spec in sorted(t.items()):
        print("%-14s %-6.0f %-6s %-8.4f %-14.6f"
              % (name, spec["pct"], _fmt(spec["long_s"]), spec["burn"],
                 spec["threshold"]))
    print("\n短窗:%s" % ", ".join("%s=%s" % (n, _fmt(s["short_s"]))
                                for n, s in sorted(t.items())))


def _fmt(s):
    if s >= W.DAY:
        return "%gd" % (s / W.DAY)
    if s >= 3600:
        return "%gh" % (s / 3600)
    return "%gm" % (s / 60)


def e2():
    hdr("E2", "照 mwmbAlertTpl 渲染出的告警表达式")
    print(W.render_expr(SLO, PERIOD))


def e3():
    hdr("E3", "短窗口的作用:故障已恢复,长窗还在破线 —— 短窗负责叫停")
    # 0..10 分钟 100% 失败,之后完全恢复;时间线按 1 分钟一格,共 3 小时
    timeline = [1.0] * 10 + [0.0] * 170
    t = W.thresholds(SLO, PERIOD)
    quick = t["page_quick"]
    mwmb_first = mwmb_last = None
    static_first = static_last = None
    for i in range(len(timeline)):
        now = i * MIN
        rs = W.trailing_avg(timeline, now, quick["short_s"], MIN)
        rl = W.trailing_avg(timeline, now, quick["long_s"], MIN)
        if rs is None or rl is None:
            continue
        if W.fires(rs, rl, quick["threshold"]):
            if mwmb_first is None:
                mwmb_first = i
            mwmb_last = i
        if rl > quick["threshold"]:          # 只看长窗的"静态阈值"告警
            if static_first is None:
                static_first = i
            static_last = i
    print("故障区间            : 第 0 ~ 9 分钟(共 10 分钟 100%% 失败)")
    print("MWMB(5m 且 1h)   : 第 %s ~ %s 分钟告警" % (mwmb_first, mwmb_last))
    print("只看 1h 长窗的静态阈值: 第 %s ~ %s 分钟告警" % (static_first, static_last))
    print("告警时长差          : %d 分钟(MWMB 提前叫停)" % (static_last - mwmb_last))


def e4():
    hdr("E4", "慢燃:静态阈值完全看不见,MWMB 的 ticket 档能抓住")
    # 5 天持续 0.12% 错误率
    rate = 0.0012
    t = W.thresholds(SLO, PERIOD)
    print("持续错误率          : %.4f  (burn rate = %.4f)"
          % (rate, rate / (1 - SLO)))
    print("静态阈值 1%%         : %s" % ("告警" if rate > 0.01 else "不告警(漏报)"))
    print("静态阈值 0.1%%       : %s" % ("告警" if rate > 0.001 else "不告警"))
    for name in ("page_quick", "page_slow", "ticket_quick", "ticket_slow"):
        spec = t[name]
        print("  %-14s 阈值 %.6f  短窗破线=%-5s 长窗破线=%-5s -> %s"
              % (name, spec["threshold"],
                 rate > spec["threshold"], rate > spec["threshold"],
                 "告警" if rate > spec["threshold"] else "不告警"))
    br = rate / (1 - SLO)
    print("\n该燃烧率下烧光整期预算需 %.1f 天" % (PERIOD / br / W.DAY))


def e5():
    hdr("E5", "10 分钟故障要烧到多狠才 page?—— 阈值就是「1 小时花掉 2% 预算」")
    t = W.thresholds(SLO, PERIOD)
    q = t["page_quick"]
    # 故障前后各有 120 分钟健康期,保证 5m 与 1h 窗口都被填满(否则窗口被截断
    # 会让均值失真 —— 这是第一版写错的地方)
    pre = 120
    dur = 10
    print("故障 10 分钟,前后各 %d 分钟健康。取值时刻 = 故障结束点。" % pre)
    print("%-10s %-10s %-10s %-14s %s" % ("故障错误率", "5m 均值", "1h 均值",
                                          "1h 内耗预算", "判定"))
    for r in (0.02, 0.05, 0.08, 0.0864, 0.09, 0.2, 1.0):
        tl = [0.0] * pre + [r] * dur + [0.0] * 120
        now = (pre + dur) * MIN
        rs = W.trailing_avg(tl, now, q["short_s"], MIN)
        rl = W.trailing_avg(tl, now, q["long_s"], MIN)
        consumed = rl * q["long_s"] / ((1 - SLO) * PERIOD)
        print("%-10.4f %-10.6f %-10.6f %-14s %s"
              % (r, rs, rl, "%.4f%%" % (consumed * 100),
                 "page" if W.fires(rs, rl, q["threshold"]) else "不告警"))
    print("\n阈值 %.6f = 燃烧率 %.1f × 预算率 %.4f,对应「1 小时内花掉 %.0f%% 预算」。"
          % (q["threshold"], q["burn"], 1 - SLO, q["pct"]))
    print("分界点:10/60 × r > %.6f  →  r > %.6f"
          % (q["threshold"], q["threshold"] * 6))


def e6():
    hdr("E6", "短窗 or 长窗:只破一个窗不告警(and 的语义)")
    t = W.thresholds(SLO, PERIOD)
    q = t["page_quick"]
    cases = [
        ("只短窗破线(刚发生故障)", 0.05, 0.001),
        ("只长窗破线(故障已恢复)", 0.0, 0.05),
        ("两个都破线", 0.05, 0.05),
        ("两个都没破线", 0.0, 0.0),
    ]
    for name, rs, rl in cases:
        print("%-24s short=%.4f long=%.4f -> %s"
              % (name, rs, rl, "告警" if W.fires(rs, rl, q["threshold"]) else "不告警"))


def e7():
    hdr("E7", "周期改成 28d,阈值整体缩放 28/30 —— 照抄 30d 数字会放宽告警")
    for period_days in (30, 28, 7):
        t = W.thresholds(SLO, period_days * W.DAY)
        print("%2dd: page_quick 燃烧率 %8.4f  阈值 %.6f"
              % (period_days, t["page_quick"]["burn"], t["page_quick"]["threshold"]))


def e8():
    hdr("E8", "SLO 越紧,同一个燃烧率对应的错误率阈值越小")
    for slo in (0.99, 0.999, 0.9999):
        t = W.thresholds(slo, PERIOD)
        print("SLO=%.4f  page_quick 阈值 %.6f  ticket_slow 阈值 %.6f"
              % (slo, t["page_quick"]["threshold"], t["ticket_slow"]["threshold"]))


if __name__ == "__main__":
    e1()
    e2()
    e3()
    e4()
    e5()
    e6()
    e7()
    e8()
