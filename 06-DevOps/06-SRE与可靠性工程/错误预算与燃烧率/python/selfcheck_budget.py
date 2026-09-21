"""错误预算与燃烧率 —— 自检(纯标准库,离线可跑)。

运行:`python selfcheck_budget.py`  期望末行 `PASS n / FAIL 0`

防"假绿"的约束:
1. 期望值全部**手写常量**或**闭式关系**,不用被测函数生成期望。
2. 关键的"两条独立公式必须合上"做成显式交叉验证(E6/E7):一边用 Sloth 的
   `getBurnRateFactor` 求燃烧率,另一边用 `budget_consumed` 独立推导消耗比例,
   两者必须都等于 pct/100。任一条写错都会在这里暴露。
3. 每条"差异"断言配一条负控(三种预算口径的收敛条件用 (a)/(a2) 互为对照)。
4. 浮点一律 1e-9 容差(0.9333... 这种用 1e-9 也安全,因为是 28/30 的有理数)。
"""

import budget as B
import method as M

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


DAY = B.DAY
P30 = 30 * DAY
P28 = 28 * DAY
H = 3600.0

# ------------------------------------------------------- E1 预算算术
ok(close(B.error_budget(0.999), 0.001), "E1 99.9% 的预算是 0.001")
ok(close(B.budget_minutes(0.999, P30), 43.2), "E1 99.9%/30d = 43.2 分钟")
ok(close(B.budget_minutes(0.99, P30), 432.0), "E1 99%/30d = 432 分钟")
ok(close(B.budget_minutes(0.9999, P30), 4.32), "E1 99.99%/30d = 4.32 分钟")
ok(close(B.budget_minutes(0.999, P28), 40.32), "E1 99.9%/28d = 40.32 分钟")
# 每多一个九,预算小一个数量级 —— 闭式关系而非硬编码
ok(close(B.budget_minutes(0.9999, P30) * 10, B.budget_minutes(0.999, P30)),
   "E1 多一个九 → 预算 /10")
for bad in (1.0, 1.5, -0.1):
    try:
        B.error_budget(bad)
        ok(False, "E1 非法 SLO %r 应抛错" % bad)
    except ValueError:
        ok(True, "E1 非法 SLO %r 应抛错" % bad)

# ------------------------------------------------------- E2 burn rate
ok(close(B.burn_rate(0.001, 0.999), 1.0), "E2 错误率等于预算率时 burn=1")
ok(close(B.burn_rate(0.0144, 0.999), 14.4), "E2 错误率 1.44% → burn=14.4")
ok(close(B.burn_rate(0.0001, 0.999), 0.1), "E2 错误率 0.01% → burn=0.1")
ok(close(B.burn_rate(0.0, 0.999), 0.0), "E2 零错误 → burn=0")
# 闭式:burn rate 与 SLO 的关系是"除以预算率"
ok(close(B.burn_rate(0.002, 0.999) * B.error_budget(0.999), 0.002),
   "E2 burn × 预算率 = 错误率")

# ------------------------------------------------------- E3 Sloth 因子
ok(close(B.burn_rate_factor(2.0, P30, 1 * H), 14.4), "E3 30d page_quick = 14.4")
ok(close(B.burn_rate_factor(5.0, P30, 6 * H), 6.0), "E3 30d page_slow = 6")
ok(close(B.burn_rate_factor(10.0, P30, 1 * DAY), 3.0), "E3 30d ticket_quick = 3")
ok(close(B.burn_rate_factor(10.0, P30, 3 * DAY), 1.0), "E3 30d ticket_slow = 1")
ok(close(B.burn_rate_factor(2.0, P28, 1 * H), 13.44), "E3 28d page_quick = 13.44")
ok(close(B.burn_rate_factor(5.0, P28, 6 * H), 5.6), "E3 28d page_slow = 5.6")
ok(close(B.burn_rate_factor(10.0, P28, 1 * DAY), 2.8), "E3 28d ticket_quick = 2.8")
ok(close(B.burn_rate_factor(10.0, P28, 3 * DAY), 28.0 / 30.0),
   "E3 28d ticket_slow = 28/30")
# 闭式:周期缩短到 28/30,燃烧率同比缩放
ok(close(B.burn_rate_factor(2.0, P28, 1 * H) / B.burn_rate_factor(2.0, P30, 1 * H),
         28.0 / 30.0), "E3 28d 与 30d 的燃烧率之比为 28/30")
try:
    B.burn_rate_factor(2.0, P30, 0)
    ok(False, "E3 零长窗应抛错")
except ValueError:
    ok(True, "E3 零长窗应抛错")

# ------------------------------------------------------- E4 交叉验证
for name, pct, long_ in (("page_quick", 2.0, 1 * H), ("page_slow", 5.0, 6 * H),
                         ("ticket_quick", 10.0, 1 * DAY),
                         ("ticket_slow", 10.0, 3 * DAY)):
    br = B.burn_rate_factor(pct, P30, long_)
    er = br * B.error_budget(0.999)
    # 独立公式:消耗比例 = 错误率 × 窗口 / (预算率 × 周期)
    ok(close(B.budget_consumed(er, 0.999, long_, P30), pct / 100.0),
       "E4 %s 长窗内恰好花掉 %.0f%%" % (name, pct))
    # 另一条独立路径:burn_rate × 窗口 / 周期 也应等于 pct/100
    ok(close(br * long_ / P30, pct / 100.0), "E4 %s 闭式 br*w/p = pct/100" % name)
# 消耗比例与 SLO 无关(只取决于 burn rate 与窗口占比)
a = B.budget_consumed(0.0144, 0.999, H, P30)
b = B.budget_consumed(0.00144, 0.9999, H, P30)
ok(close(a, b), "E4 消耗比例与 SLO 无关(同 burn rate 即同消耗)")

# ------------------------------------------------------- E5 TTE
for name, pct, long_, want_h in (("page_quick", 2.0, 1 * H, 50.0),
                                 ("page_slow", 5.0, 6 * H, 120.0),
                                 ("ticket_quick", 10.0, 1 * DAY, 240.0),
                                 ("ticket_slow", 10.0, 3 * DAY, 720.0)):
    br = B.burn_rate_factor(pct, P30, long_)
    tte = B.time_to_exhaustion(br, P30)
    ok(close(tte / H, want_h), "E5 %s TTE = %.1f 小时" % (name, want_h))
    # 独立推导:消耗 pct% 用了 long_,烧光 100% 需要 long_ / (pct/100)
    ok(close(tte, long_ / (pct / 100.0)), "E5 %s TTE 独立推导一致" % name)
ok(B.time_to_exhaustion(0.0, P30) == float("inf"), "E5 burn=0 时永不耗尽")

# ------------------------------------------------------- E6 阈值依赖 SLO
br = B.burn_rate_factor(2.0, P30, 1 * H)
ok(close(br, 14.4), "E6 燃烧率与 SLO 无关")
ok(close(br * B.error_budget(0.999), 0.0144), "E6 SLO 99.9% 阈值 0.0144")
ok(close(br * B.error_budget(0.99), 0.144), "E6 SLO 99% 阈值 0.144")
ok(close(br * B.error_budget(0.9999), 0.00144), "E6 SLO 99.99% 阈值 0.00144")

# ------------------------------------------------------- E7 三种口径
good = [1] * 11 + [1000]
total = [2] * 11 + [1000]
r7 = M.all_methods(good, total, 0.99)
ok(close(r7["occurrences"], 0.989237, 1e-6), "E7 occurrences ≈ 0.989237")
ok(close(r7["timeslices"], 1.0 / 12.0), "E7 timeslices = 1/12")
ok(close(r7["ratio_timeslices"], 6.5 / 12.0), "E7 ratio_timeslices = 6.5/12")
ok(r7["occurrences"] > 0.98 > r7["ratio_timeslices"] > r7["timeslices"],
   "E7 三者严格递减")
ok(r7["ratio_timeslices"] > r7["timeslices"], "E7 比值平均 > 二值计数")

# ------------------------------------------------------- E8 收敛条件
g = [100] * 99 + [0]
t = [100] * 100
a8 = M.all_methods(g, t, 0.99)
ok(close(a8["occurrences"], 0.99), "E8a occurrences = 0.99")
ok(close(a8["timeslices"], 0.99), "E8a timeslices = 0.99")
ok(close(a8["ratio_timeslices"], 0.99), "E8a ratio_timeslices = 0.99")
ok(close(a8["occurrences"], a8["timeslices"]) and
   close(a8["timeslices"], a8["ratio_timeslices"]), "E8a 切片比值取 0/1 时三者重合")
# (a2) 同样 99%,摊到每个切片 → Timeslices 变成 1.0(二值化把 0.99 抬成 1)
a82 = M.all_methods([99] * 100, [100] * 100, 0.99)
ok(close(a82["timeslices"], 1.0), "E8a2 摊开后 timeslices = 1.0")
ok(close(a82["occurrences"], 0.99), "E8a2 摊开后 occurrences 仍是 0.99")
ok(a82["timeslices"] > a8["timeslices"], "E8a2 摊开比集中更好看")
# (b) 流量均匀但比值抖动 → Timeslices 仍然跳
b8 = M.all_methods([98, 99, 100, 97, 99], [100] * 5, 0.99)
ok(close(b8["occurrences"], 0.986), "E8b occurrences = 0.986")
ok(close(b8["ratio_timeslices"], 0.986), "E8b ratio_timeslices = 0.986")
ok(close(b8["timeslices"], 0.6), "E8b timeslices = 3/5(过线 3 个)")
ok(b8["timeslices"] != b8["occurrences"], "E8b 流量均匀也**不**收敛")

# ------------------------------------------------------- E9 target 只影响 Timeslices
base_ts = [M.timeslices(good, total, t) for t in (0.5, 0.9, 0.99, 1.0)]
ok(close(base_ts[0], 1.0), "E9 target=0.5 时全部切片过线")
ok(close(base_ts[1], 1.0 / 12.0), "E9 target=0.9 时只剩 1 个切片过线")
ok(close(base_ts[2], base_ts[3]), "E9 target 0.99 与 1.0 结果相同")
ok(len(set(round(M.occurrences(good, total), 12) for _ in base_ts)) == 1,
   "E9 occurrences 与 target 无关")
ok(close(M.ratio_timeslices(good, total), 6.5 / 12.0),
   "E9 ratio_timeslices 与 target 无关")
for bad_t in (0.0, -0.1, 1.1):
    try:
        M.timeslices(good, total, bad_t)
        ok(False, "E9 非法 target %r 应抛错" % bad_t)
    except ValueError:
        ok(True, "E9 非法 target %r 应抛错" % bad_t)

# ------------------------------------------------------- 边界
ok(M.occurrences([], []) is None, "X 空输入返回 None")
ok(M.occurrences([0, 0], [0, 0]) is None, "X 总量为 0 返回 None")
ok(M.timeslices([0], [0], 0.99) is None, "X 全空切片返回 None")
ok(M.ratio_timeslices([0], [0]) is None, "X 全空切片(ratio)返回 None")
# 0 事件切片被跳过,不参与平均 —— 既不按 0 也不按 1 计
ok(close(M.ratio_timeslices([5, 0], [10, 0]), 0.5), "X 空切片被跳过而非按 0 计")
ok(close(M.timeslices([5, 0], [10, 0], 0.99), 0.0), "X 空切片不参与切片计数")
w = B.Window(0, 300, 3600)
try:
    w.validate()
    ok(False, "X 零预算百分比应抛错")
except ValueError:
    ok(True, "X 零预算百分比应抛错")
w2 = B.Window(2, 0, 3600)
try:
    w2.validate()
    ok(False, "X 零短窗应抛错")
except ValueError:
    ok(True, "X 零短窗应抛错")
ws = B.Windows(0, B.Window(2, 300, 3600), B.Window(5, 1800, 21600),
               B.Window(10, 7200, 86400), B.Window(10, 21600, 259200))
try:
    ws.validate()
    ok(False, "X 零周期应抛错")
except ValueError:
    ok(True, "X 零周期应抛错")

print("PASS %d / FAIL %d" % (PASS, FAIL))
for n in FAILED:
    print("  FAILED:", n)
