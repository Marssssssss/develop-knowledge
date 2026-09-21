"""SLI 窗口与聚合口径 —— 自检(纯标准库,离线可跑)。

运行:`python selfcheck_window.py`  期望末行 `PASS n / FAIL 0`

防"假绿"的三条自我约束:
1. **期望值一律手写**(右列常量),不调用 `prom_window` 里任何函数去生成期望;
   E3/E4/E5/E6 的期望值都先用源码的分步公式手算过,再与实跑对照。
2. **每条"修正起作用"的断言都配一条负控**:计数器回绕那条配"无回绕时 counter
   与 gauge 必须相等";窗口长度那条配"rolling 恒定 / calendar 变长"互为对照。
3. **浮点一律给 1e-9 容差**,绝不写 `==`。
"""

import datetime as dt

import openslo as O
import prom_window as P

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


# ------------------------------------------------------- E1 左开右闭
sel = P.select_range([(0.0, 10.0), (30.0, 40.0), (60.0, 70.0)], 60.0, 60.0)
ok(len(sel) == 2, "E1 选中 2 点")
ok(not any(t == 0.0 for t, _ in sel), "E1 左边界 t=0 被排除")
ok(any(t == 60.0 for t, _ in sel), "E1 右边界 t=60 被包含")
# 窗口缩小到 30 后左边界变成 30,同样必须被排除
sel30 = P.select_range([(0.0, 10.0), (30.0, 40.0), (60.0, 70.0)], 60.0, 30.0)
ok(not any(t == 30.0 for t, _ in sel30), "E1 左边界随窗口移动后仍被排除")
ok(len(sel30) == 1 and sel30[0][0] == 60.0, "E1 只剩右端点")

# ------------------------------------------------------- E2 increase = rate * range
s2 = [(i * 15.0, 100.0 * i) for i in range(5)]
sel2 = P.select_range(s2, 60.0, 60.0)
ok(close(P.increase(sel2, 60.0, 60.0), P.rate(sel2, 60.0, 60.0) * 60, 1e-9),
   "E2 increase == rate*range")
ok(close(P.rate(sel2, 60.0, 60.0), 6.666666666666666), "E2 rate 数值")
ok(close(P.increase(sel2, 60.0, 60.0), 400.0), "E2 increase 数值")

# ------------------------------------------------------- E3 外推使整数变非整数
s3 = [(10.0, 100.0), (25.0, 101.0), (37.0, 102.0), (40.0, 103.0)]
sel3 = P.select_range(s3, 40.0, 30.0)
ok(len(sel3) == 3 and sel3[0][0] == 25.0, "E3 左开边界把 t=10 排除")
ok(close(P.increase(sel3, 40.0, 30.0), 2.5), "E3 increase=2.5(手算 2*(15+3.75)/15)")
ok(P.increase(sel3, 40.0, 30.0) != 2.0, "E3 不等于裸差值")
# 负控:等距采样时 factor 恰好整除,非整数现象消失 —— 说明 E3 的成因是外推不是算术
even = [(10.0, 100.0), (20.0, 101.0), (30.0, 102.0), (40.0, 103.0)]
ok(close(P.increase(P.select_range(even, 40.0, 30.0), 40.0, 30.0), 3.0),
   "E3 负控:等距时外推被整除掩盖")

# ------------------------------------------------------- E4 外推阈值 1.1
# (a) 首点距左边界 15 < 阈值 24.75 → 完全外推
a = P.increase(P.select_range([(15.0, 10.0), (45.0, 40.0), (60.0, 55.0)], 60.0, 60.0),
               60.0, 60.0)
ok(close(a, 60.0), "E4a 完全外推 increase=60")
ok(close(P.rate(P.select_range([(15.0, 10.0), (45.0, 40.0), (60.0, 55.0)], 60.0, 60.0),
                60.0, 60.0), 1.0), "E4a rate=1.0")
# (b) 首点距左边界 40 > 阈值 22 → 退化为 avg/2 = 10
b = P.increase(P.select_range([(40.0, 10.0), (60.0, 55.0)], 60.0, 60.0), 60.0, 60.0)
ok(close(b, 55.0), "E4b 半间隔外推 increase=55")
# 手算核对:裸差 45,avg=20,阈值 22,durToStart=40>=22 → 先取 avg/2=10;
# 但**计数器零点钳制**紧接着生效:durationToZero = 20*(10/45)=4.444 < 10 故取它。
# 闭式:零点钳制生效时 increase = 裸差 + 首点值 = 45 + 10 = 55(与 avg/2 无关)
ok(close(b, 45.0 + 10.0), "E4b 零点钳制下 increase = 裸差 + 首点值")
ok(b < 45.0 * 1.5, "E4b 结果小于「按 avg/2 外推」的 67.5 —— 零点钳制更保守")
# (c) 首点距左边界 20 < 阈值 22 → 完全外推,factor=(40+20+0)/40=1.5... 手算核对
c = P.increase(P.select_range([(20.0, 10.0), (40.0, 30.0), (60.0, 55.0)], 60.0, 60.0),
               60.0, 60.0)
ok(close(c, 67.5), "E4c increase=67.5")
ok(close(c, 45.0 * 1.5), "E4c 与手算一致")

# ------------------------------------------------------- E5 计数器回绕
rst = [(0.0, 100.0), (30.0, 130.0), (45.0, 10.0), (60.0, 60.0)]
selr = P.select_range(rst, 60.0, 60.0)
proper = P.increase(selr, 60.0, 60.0, is_counter=True)
naive = P.increase(selr, 60.0, 60.0, is_counter=False)
ok(close(proper, 75.0), "E5 counter 修正后 = 75")
ok(close(naive, -87.5), "E5 不修正 = -87.5")
ok(proper > 0 > naive, "E5 修正把负值翻正")
ok(close(proper, 60.0 * 1.25), "E5 裸和 60 乘 factor 1.25")
# 负控:没有回绕时两种口径必须完全相等
cln = [(0.0, 100.0), (30.0, 130.0), (45.0, 145.0), (60.0, 160.0)]
selc = P.select_range(cln, 60.0, 60.0)
ok(close(P.increase(selc, 60.0, 60.0, True),
         P.increase(selc, 60.0, 60.0, False)), "E5 负控:无回绕时两口径一致")
ok(close(P.increase(selc, 60.0, 60.0, True), 37.5), "E5 负控数值 37.5")

# ------------------------------------------------------- E6 rate vs irate
s6 = [(0.0, 0.0), (10.0, 10.0), (20.0, 20.0), (58.0, 30.0), (60.0, 200.0)]
sel6 = P.select_range(s6, 60.0, 60.0)
ok(close(P.rate(sel6, 60.0, 60.0), 3.8), "E6 rate=3.8")
ok(close(P.irate(sel6), 85.0), "E6 irate=85")
ok(P.irate(sel6) > P.rate(sel6, 60.0, 60.0), "E6 尾部突增时 irate 高于 rate")
# 负控:恒定速率序列下 irate 与 rate 应当相等(否则 irate 实现不是"最后两点斜率")
flat = [(0.0, 0.0), (15.0, 15.0), (30.0, 30.0), (45.0, 45.0), (60.0, 60.0)]
ok(close(P.irate(P.select_range(flat, 60.0, 60.0)), 1.0), "E6 负控:恒速时 irate=1")
ok(close(P.rate(P.select_range(flat, 60.0, 60.0), 60.0, 60.0), 1.0),
   "E6 负控:恒速时 rate=1")

# ------------------------------------------------------- E7 窗口长度
start = O.parse_start("2026-01-01 00:00:00")
f_lo, f_hi = O.calendar_window(dt.datetime(2026, 2, 10), start, O.Duration("1M"))
j_lo, j_hi = O.calendar_window(dt.datetime(2026, 7, 10), start, O.Duration("1M"))
ok((f_hi - f_lo).days == 28, "E7 calendar 1M 在 2 月 = 28 天")
ok((j_hi - j_lo).days == 31, "E7 calendar 1M 在 7 月 = 31 天")
ok((j_hi - j_lo).days != (f_hi - f_lo).days, "E7 calendar 长度不恒定")
r_lo, r_hi = O.rolling_window(dt.datetime(2026, 7, 10).timestamp(), O.Duration("30d"))
ok(round((r_hi - r_lo) / 86400) == 30, "E7 rolling 30d 恒定 30 天")
for probe in (dt.datetime(2026, 3, 5), dt.datetime(2026, 11, 25)):
    lo, hi = O.rolling_window(probe.timestamp(), O.Duration("30d"))
    ok(round((hi - lo) / 86400) == 30, "E7 rolling 在 %s 仍 30 天" % probe.date())
ok(O.Duration("1Q").months == 3 and O.Duration("1Y").months == 12,
   "E7 Q=3 月 Y=12 月")
ok(O.Duration("2w").seconds == 2 * 604800, "E7 w=604800 秒")
ok(O.Duration("30d").seconds == 30 * 86400, "E7 d=86400 秒")
ok(O.Duration("1M").is_calendar and O.Duration("1M").months == 1,
   "E7 M 是日历单位")
ok(not O.Duration("30d").is_calendar, "E7 d 是固定时长")

# ------------------------------------------------------- E8 rolling 与 calendar 的恢复
def sli_at(now, kind):
    base = dt.datetime(2026, 3, 1)
    start_w = O.parse_start("2026-03-01 00:00:00")
    if kind == "roll":
        lo, hi = O.rolling_window(now.timestamp(), O.Duration("30d"))
    else:
        lo, hi = O.calendar_window(now, start_w, O.Duration("1M"))
        lo, hi = lo.timestamp(), hi.timestamp()
    hi = min(hi, now.timestamp())
    out_lo = dt.datetime(2026, 3, 20, 10).timestamp()
    out_hi = dt.datetime(2026, 3, 20, 12).timestamp()
    good = bad = 0
    t = lo
    while t < hi:
        if out_lo <= t < out_hi:
            bad += 1
        else:
            good += 1
        t += 3600
    return good / (good + bad)


# 取 06:00 而不是整天零点:calendar 窗口在 4/1 00:00 才刚开,窗口内 0 小时无定义
apr1 = dt.datetime(2026, 4, 1, 6, 0, 0)
apr15 = dt.datetime(2026, 4, 15, 6, 0, 0)
apr20 = dt.datetime(2026, 4, 20, 6, 0, 0)
ok(close(sli_at(apr1, "cal"), 1.0), "E8 calendar 在 4/1 已完全恢复")
ok(sli_at(apr1, "roll") < 1.0, "E8 rolling 在 4/1 尚未恢复")
ok(close(sli_at(apr1, "roll"), 718 / 720), "E8 rolling 4/1 = 718/720")
ok(sli_at(apr15, "roll") < 1.0, "E8 rolling 在 4/15 仍未恢复")
ok(close(sli_at(apr20, "roll"), 1.0), "E8 rolling 到 4/20 才恢复")
ok(sli_at(apr20, "roll") > sli_at(apr15, "roll"), "E8 rolling 单调恢复")

# ------------------------------------------------------- E9 rawType
ok(close(O.sli_from_raw(0.003, "success"), 0.003), "E9 success 不取补")
ok(close(O.sli_from_raw(0.003, "failure"), 0.997), "E9 failure 取补")
ok(close(O.sli_from_raw(0.003, "success") + O.sli_from_raw(0.003, "failure"), 1.0),
   "E9 两种读法之和为 1")
ok(close(O.sli_from_good(997, 1000), O.sli_from_bad(3, 1000)),
   "E9 good/total 与 bad/total 一致")
ok(O.sli_from_good(0, 0) is None and O.sli_from_bad(0, 0) is None,
   "E9 总量为 0 时无定义")
try:
    O.sli_from_raw(0.1, "ratio")
    ok(False, "E9 非法 rawType 应抛错")
except ValueError:
    ok(True, "E9 非法 rawType 应抛错")

# ------------------------------------------------------- 边界与异常
ok(P.increase([], 60.0, 60.0) is None, "X 空序列返回 None")
ok(P.increase([(60.0, 5.0)], 60.0, 60.0) is None, "X 单样本无外推返回 None")
ok(close(P.increase([(30.0, 5.0), (60.0, 5.0)], 60.0, 60.0), 0.0),
   "X 常量计数器 increase=0")
ok(P.irate([(1.0, 1.0)]) is None, "X irate 不足两点返回 None")
for bad_text in ("", "5", "0d", "10x"):
    try:
        O.Duration(bad_text)
        ok(False, "X 非法 duration %r 应抛错" % bad_text)
    except ValueError:
        ok(True, "X 非法 duration %r 应抛错" % bad_text)
try:
    O.rolling_window(0.0, O.Duration("1M"))
    ok(False, "X rolling 不接受日历单位")
except ValueError:
    ok(True, "X rolling 不接受日历单位")
try:
    O.calendar_window(dt.datetime(2026, 1, 1), start, O.Duration("30d"))
    ok(False, "X calendar 不接受固定时长")
except ValueError:
    ok(True, "X calendar 不接受固定时长")
ok(O._add_months(dt.datetime(2026, 1, 31), 1) == dt.datetime(2026, 2, 28),
   "X 月末钳制 1/31 + 1M = 2/28")

print("PASS %d / FAIL %d" % (PASS, FAIL))
for n in FAILED:
    print("  FAILED:", n)
