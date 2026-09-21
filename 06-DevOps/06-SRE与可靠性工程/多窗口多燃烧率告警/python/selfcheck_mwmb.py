"""多窗口多燃烧率告警 —— 自检(纯标准库,离线可跑)。

运行:`python selfcheck_mwmb.py`  期望末行 `PASS n / FAIL 0`

防"假绿"的约束:
1. 期望值手写常量或闭式关系,不用被测函数生成期望。
2. **and 语义**必须单独验:只破一个窗不告警(4 个组合全测),否则"单窗破线即告警"
   的错实现也能让"两个都破线"那条通过。
3. 严格 `>` 用边界值负控:rate 恰好等于阈值时**不**告警,`gte` 时才告警。
4. E3 的"提前叫停 55 分钟"是两条独立路径算出来的(MWMB 的 5m 与 1h 各自滑动),
   不是同一条公式抄两遍。
"""

import mwmb as W

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


SLO = 0.999
P30 = 30 * W.DAY
MIN = 60.0

# ------------------------------------------------------- E1 四档阈值
t = W.thresholds(SLO, P30)
ok(close(t["page_quick"]["threshold"], 0.0144), "E1 page_quick 阈值 0.0144")
ok(close(t["page_slow"]["threshold"], 0.006), "E1 page_slow 阈值 0.006")
ok(close(t["ticket_quick"]["threshold"], 0.003), "E1 ticket_quick 阈值 0.003")
ok(close(t["ticket_slow"]["threshold"], 0.001), "E1 ticket_slow 阈值 0.001")
ok(close(t["page_quick"]["burn"], 14.4), "E1 page_quick 燃烧率 14.4")
ok(close(t["ticket_slow"]["burn"], 1.0), "E1 ticket_slow 燃烧率 1")
# 闭式:阈值 = 燃烧率 × (1-SLO),且四档阈值严格递减
ok(close(t["page_quick"]["threshold"], t["page_quick"]["burn"] * 0.001),
   "E1 阈值 = 燃烧率 × 预算率")
ok(t["page_quick"]["threshold"] > t["page_slow"]["threshold"] >
   t["ticket_quick"]["threshold"] > t["ticket_slow"]["threshold"],
   "E1 四档阈值严格递减")
# 长短窗配对(sloth google-30d.yaml 实读值)
ok(t["page_quick"]["short_s"] == 300 and t["page_quick"]["long_s"] == 3600,
   "E1 page_quick 5m/1h")
ok(t["page_slow"]["short_s"] == 1800 and t["page_slow"]["long_s"] == 6 * 3600,
   "E1 page_slow 30m/6h")
ok(t["ticket_quick"]["short_s"] == 2 * 3600 and t["ticket_quick"]["long_s"] == W.DAY,
   "E1 ticket_quick 2h/1d")
ok(t["ticket_slow"]["short_s"] == 6 * 3600 and t["ticket_slow"]["long_s"] == 3 * W.DAY,
   "E1 ticket_slow 6h/3d")
for name in t:
    ok(t[name]["short_s"] < t[name]["long_s"], "E1 %s 短窗 < 长窗" % name)

# ------------------------------------------------------- E2 表达式
expr = W.render_expr(SLO, P30)
ok("\n    and\n" in expr, "E2 表达式含 and(长短窗)")
ok(expr.count("and") == 2, "E2 quick/slow 各一组 and")
ok("\nor\n" in expr, "E2 表达式含 or(quick/slow)")
ok("14.4" in expr and "6 " in expr, "E2 表达式含两个燃烧率因子")
ok("0.001" in expr, "E2 表达式含预算率 0.001")

# ------------------------------------------------------- E3 短窗叫停
timeline = [1.0] * 10 + [0.0] * 170
q = t["page_quick"]
mwmb_first = mwmb_last = None
static_first = static_last = None
for i in range(len(timeline)):
    now = i * MIN
    rs = W.trailing_avg(timeline, now, q["short_s"], MIN)
    rl = W.trailing_avg(timeline, now, q["long_s"], MIN)
    if rs is None or rl is None:
        continue
    if W.fires(rs, rl, q["threshold"]):
        mwmb_first = i if mwmb_first is None else mwmb_first
        mwmb_last = i
    if rl > q["threshold"]:
        static_first = i if static_first is None else static_first
        static_last = i
ok(mwmb_first == 0, "E3 MWMB 从第 0 分钟开始告警")
ok(mwmb_last == 13, "E3 MWMB 在第 13 分钟停止")
ok(static_last == 68, "E3 静态 1h 阈值拖到第 68 分钟")
ok(static_last - mwmb_last == 55, "E3 提前叫停 55 分钟")
ok(static_last > mwmb_last, "E3 静态比 MWMB 拖更久(短窗确实在起作用)")
# 负控:把短窗拉长到与长窗同宽,MWMB 就退化成静态 —— 证明差别来自短窗
long_as_short = None
for i in range(len(timeline)):
    now = i * MIN
    rs = W.trailing_avg(timeline, now, q["long_s"], MIN)
    rl = W.trailing_avg(timeline, now, q["long_s"], MIN)
    if rs is None or rl is None:
        continue
    if W.fires(rs, rl, q["threshold"]):
        long_as_short = i
ok(long_as_short == static_last, "E3 负控:短窗=长窗时 MWMB 退化为静态")

# ------------------------------------------------------- E4 慢燃
rate = 0.0012
ok(rate < 0.01, "E4 静态 1% 阈值看不见 0.12% 的慢燃(漏报)")
ok(rate > t["ticket_slow"]["threshold"], "E4 ticket_slow 能抓住")
ok(rate < t["ticket_quick"]["threshold"], "E4 ticket_quick 抓不住")
ok(rate < t["page_slow"]["threshold"], "E4 page_slow 抓不住")
ok(rate < t["page_quick"]["threshold"], "E4 page_quick 抓不住")
ok(close(rate / (1 - SLO), 1.2), "E4 慢燃的燃烧率是 1.2")
ok(close(P30 / 1.2 / W.DAY, 25.0), "E4 25 天烧光预算")

# ------------------------------------------------------- E5 分界点
pre, dur = 120, 10
def probe(r):
    tl = [0.0] * pre + [r] * dur + [0.0] * 120
    now = (pre + dur) * MIN
    rs = W.trailing_avg(tl, now, q["short_s"], MIN)
    rl = W.trailing_avg(tl, now, q["long_s"], MIN)
    return rs, rl, W.fires(rs, rl, q["threshold"])

rs, rl, fired = probe(0.0864)
ok(close(rl, 0.0144), "E5 分界点 1h 均值恰为阈值")
ok(not fired, "E5 分界点(严格 >)不告警")
ok(rs > q["threshold"], "E5 分界点时短窗早已破线 —— 卡住的是长窗")
rs2, rl2, fired2 = probe(0.09)
ok(fired2, "E5 超过分界点即告警")
rs3, rl3, fired3 = probe(0.05)
ok(not fired3, "E5 低于分界点不告警")
ok(close(probe(1.0)[1], 1.0 * dur / 60.0), "E5 全失败时 1h 均值 = 10/60")

# ------------------------------------------------------- E6 and 语义
ok(W.fires(0.05, 0.05, q["threshold"]), "E6 两窗都破 → 告警")
ok(not W.fires(0.05, 0.001, q["threshold"]), "E6 只短窗破 → 不告警")
ok(not W.fires(0.0, 0.05, q["threshold"]), "E6 只长窗破 → 不告警")
ok(not W.fires(0.0, 0.0, q["threshold"]), "E6 都不破 → 不告警")
# 严格 > 的负控:恰好等于阈值不告警,gte 才告警
# 注意:阈值是 14.4 * (1-0.999) = 0.014400000000000013,字面量 0.0144 比它**小**,
# 所以这里必须用计算出的阈值本身,不能用字面量 —— 否则"恰好相等"根本没被验到。
th = q["threshold"]
ok(th != 0.0144, "E6 阈值与字面量 0.0144 不严格相等(浮点口径)")
ok(not W.fires(th, th, th), "E6 恰好等于阈值(gt)不告警")
ok(W.fires(th, th, th, op="gte"), "E6 恰好等于阈值(gte)告警")
try:
    W.fires(0.1, 0.1, 0.01, op="ne")
    ok(False, "E6 非法 op 应抛错")
except ValueError:
    ok(True, "E6 非法 op 应抛错")

# ------------------------------------------------------- E7 周期缩放
t28 = W.thresholds(SLO, 28 * W.DAY)
ok(close(t28["page_quick"]["threshold"], 0.01344), "E7 28d 阈值 0.01344")
ok(close(t28["page_slow"]["threshold"], 0.0056), "E7 28d page_slow 0.0056")
ok(close(t28["page_quick"]["threshold"] / t["page_quick"]["threshold"], 28.0 / 30.0),
   "E7 28d/30d = 28/30")
t7 = W.thresholds(SLO, 7 * W.DAY)
ok(close(t7["page_quick"]["burn"], 14.4 * 7.0 / 30.0), "E7 7d 燃烧率同比缩放")
ok(t7["page_quick"]["threshold"] < t["page_quick"]["threshold"],
   "E7 周期越短阈值越松(同样错误率占比更大)")

# ------------------------------------------------------- E8 SLO 缩放
for slo, want in ((0.99, 0.144), (0.999, 0.0144), (0.9999, 0.00144)):
    ok(close(W.thresholds(slo, P30)["page_quick"]["threshold"], want),
       "E8 SLO=%.4f 阈值 %.6f" % (slo, want))
a = W.thresholds(0.999, P30)["page_quick"]["threshold"]
b = W.thresholds(0.9999, P30)["page_quick"]["threshold"]
ok(close(a / b, 10.0), "E8 多一个九 → 阈值 /10")

# ------------------------------------------------------- 窗口边界与无数据
# tt=0/60/120 三点,值 1/5/9
tl2 = [1.0, 5.0, 9.0]
# 右闭:t=60 窗口 60 → (0, 60] 含 tt=60 不含 tt=0 → 均值 5
ok(close(W.trailing_avg(tl2, 60.0, 60.0, MIN), 5.0), "X 右闭:端点 tt=60 被包含")
# 左开:t=120 窗口 60 → (60, 120] 含 tt=120 不含 tt=60 → 均值 9(若左闭则为 7)
ok(close(W.trailing_avg(tl2, 120.0, 60.0, MIN), 9.0), "X 左开:端点 tt=60 被排除")
ok(W.trailing_avg(tl2, 120.0, 60.0, MIN) != 7.0, "X 左闭的 7.0 被排除")
ok(W.trailing_avg([], 60.0, 60.0, MIN) is None, "X 无数据返回 None")
wr = W.window_rates([1.0] * 10, 9 * MIN, [5 * MIN, 60.0], MIN)
ok(close(wr[5 * MIN], 1.0), "X window_rates 5m 全失败")
ok(close(wr[60.0], 1.0), "X window_rates 1m 全失败")
# evaluate_both:无数据的窗口不参与判定
res = W.evaluate_both({q["short_s"]: 0.5, q["long_s"]: None}, SLO, P30)
ok(res["page"] is False, "X 长窗无数据时不告警")
res2 = W.evaluate_both({q["short_s"]: 0.5, q["long_s"]: 0.5}, SLO, P30)
ok(res2["page"] is True, "X 两窗都破线时 page 告警")
ok(res2["hit"] == "page_quick", "X 命中 page_quick 档")
res3 = W.evaluate_both({q["short_s"]: 0.0, q["long_s"]: 0.0,
                        t["ticket_slow"]["short_s"]: 0.002,
                        t["ticket_slow"]["long_s"]: 0.002}, SLO, P30)
ok(res3["ticket"] is True and res3["page"] is False, "X 慢燃只触发 ticket")

print("PASS %d / FAIL %d" % (PASS, FAIL))
for n in FAILED:
    print("  FAILED:", n)
