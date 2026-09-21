"""Utility AI 评分与选择自检：把 big-brain 的判定逐条变成断言。

运行：``python selfcheck_util.py``（当前目录 = python/）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    ChebyshevDistance, LinearEvaluator, PowerEvaluator, Score, SigmoidEvaluator,
    WeightedMeasure, WeightedProduct, WeightedSum, all_or_nothing, clamp,
    measured_scorer, pick_first_to_score, pick_highest, pick_highest_to_score,
    product_of_scorers, sum_of_scorers, winning_scorer,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAIL: " + label
    PASS += 1


def close(a, b, label, tol=1e-9):
    ok(abs(a - b) < tol, "%s (期望 %r，实得 %r)" % (label, b, a))


# ---------- E1 Linear ----------
lin = LinearEvaluator.new()
close(lin.evaluate(0.0), 0.0, "E1-1 恒等曲线 f(0)")
close(lin.evaluate(0.37), 0.37, "E1-2 恒等曲线 f(0.37)")
close(lin.evaluate(5.0), 1.0, "E1-3 超出上界被 clamp")
ranged = LinearEvaluator.new_ranged(0.0, 10.0)
close(ranged.evaluate(5.0), 0.5, "E1-4 new_ranged(0,10) 的 f(5)")
close(LinearEvaluator.new_inversed().evaluate(0.0), 1.0, "E1-5 new_inversed 的 f(0)")
close(LinearEvaluator.new_inversed().evaluate(1.0), 0.0, "E1-6 new_inversed 的 f(1)")

# ---------- E2 Power ----------
close(PowerEvaluator.new(2.0).evaluate(0.5), 0.25, "E2-1 power=2 的 f(0.5)")
close(PowerEvaluator.new(3.0).evaluate(0.5), 0.125, "E2-2 power=3 的 f(0.5)")
close(PowerEvaluator.new(2.0).evaluate(2.0), 1.0, "E2-3 输入先被 clamp 再取幂")

# ---------- E3 Sigmoid（默认 k=-0.5） ----------
sig = SigmoidEvaluator.new(-0.5)
close(sig.evaluate(0.5), 0.5, "E3-1 中点是 0.5")
close(sig.evaluate(0.0), 1.0, "E3-2 默认 k 下 f(0) 被 clamp 到 1（不是 0）")
close(sig.evaluate(1.0), 1.0, "E3-3 f(1) = 1")
close(sig.evaluate(0.25), 0.0, "E3-4 d=-0.25 处分母为 0 → -inf → clamp 到 0")
close(sig.evaluate(0.75), 0.875, "E3-5 f(0.75)：分母为 1，值 0.5*0.75+0.5")
# 另一个奇点在 d=+0.75（x=1.25），落在 clamp 后的输入区间之外 ⇒ 不可达
close(sig.evaluate(1.25), 1.0, "E3-5b 输入先被 clamp，第二个奇点够不到")
close(SigmoidEvaluator.new(0.0).evaluate(0.25), 0.25, "E3-6 k=0 时 Sigmoid 退化成线性")

# ---------- E4 Measures ----------
close(WeightedSum().calculate([(0.5, 1.0), (0.3, 2.0)]), 1.1, "E4-1 WeightedSum = 0.5*1 + 0.3*2")
close(WeightedProduct().calculate([(0.5, 1.0), (0.5, 1.0)]), 0.0, "E4-2 WeightedProduct 恒为 0（fold 初值 0）")
close(WeightedProduct().calculate([(0.9, 3.0)]), 0.0, "E4-3 单个输入也一样是 0")
close(ChebyshevDistance().calculate([(0.2, 1.0), (0.9, 1.0)]), 0.9, "E4-4 Chebyshev = 加权后的最大值")
close(ChebyshevDistance().calculate([(0.2, 5.0)]), 1.0, "E4-5 加权后超过 1 由上层 clamp")
close(WeightedMeasure().calculate([(0.6, 1.0), (0.8, 1.0)]), 0.5 ** 0.5,
      "E4-6 WeightedMeasure = sqrt((0.36+0.64)/2)")
close(WeightedMeasure().calculate([(0.6, 0.0), (0.8, 0.0)]), 0.0, "E4-7 权重和为 0 → 0")
ok(WeightedMeasure().calculate([(0.6, 1.0), (0.8, 1.0)]) > 0.7,
   "E4-8 二次平均恒不小于算术平均（0.7071 > 0.7）")

# ---------- E5 复合 Scorer ----------
close(all_or_nothing([0.9, 0.9], 0.8), 1.0, "E5-1 全部过阈值 → 求和后夹到 1")
close(all_or_nothing([0.8, 0.9], 0.8), 1.0, "E5-2 判据是 <，等于阈值放行")
close(all_or_nothing([0.79, 0.9], 0.8), 0.0, "E5-3 一个不过线 → 整体归零")
close(sum_of_scorers([0.3, 0.3], 0.8), 0.0, "E5-4 SumOfScorers 先求和再比阈值")
close(sum_of_scorers([0.5, 0.4], 0.8), 0.9, "E5-5 总和过线才保留")
close(product_of_scorers([0.5, 0.5], 0.0), 0.25, "E5-6 乘积 0.5*0.5")
close(product_of_scorers([0.5, 0.5], 0.0, use_compensation=True), 0.34375,
      "E5-7 补偿：mod=1-1/2=0.5，makeup=(1-0.25)*0.5，结果 0.25+0.09375")
close(product_of_scorers([1.0, 1.0], 0.0, use_compensation=True), 1.0,
      "E5-8 product==1 时不触发补偿（条件是 < 1.0）")
close(product_of_scorers([0.5, 0.5], 0.3), 0.0, "E5-9 乘积低于阈值 → 0")
close(winning_scorer([0.2, 0.7, 0.4], 0.5), 0.7, "E5-10 WinningScorer 取最大值")
close(winning_scorer([0.2, 0.5], 0.5), 0.5, "E5-11 最大值等于阈值时仍保留（严格 <）")
close(winning_scorer([0.2, 0.4], 0.5), 0.0, "E5-12 最大值低于阈值 → 0")
close(measured_scorer([(0.6, 1.0), (0.8, 1.0)], 0.5), 0.5 ** 0.5, "E5-13 MeasuredScorer 用默认 measure")
close(measured_scorer([(0.6, 1.0), (0.8, 1.0)], 0.8), 0.0, "E5-14 测度低于阈值 → 0")

# ---------- E6 Score 的值域 ----------
s = Score()
s.set(0.5)
close(s.get(), 0.5, "E6-1 set 正常值")
try:
    s.set(1.5)
    ok(False, "E6-2 set 越界应 panic")
except ValueError:
    PASS += 1
s.set_unchecked(2.0)
close(s.get(), 2.0, "E6-3 set_unchecked 允许越界（官方明确说会破坏可组合性）")

# ---------- E7 Pickers ----------
ok(pick_first_to_score([("a", 0.9), ("b", 0.5)], 0.8) == "a", "E7-1 FirstToScore 取第一个过线者")
ok(pick_first_to_score([("a", 0.5), ("b", 0.9)], 0.8) == "b", "E7-2 前一个不过线则往后找")
ok(pick_first_to_score([("a", 0.8), ("b", 0.9)], 0.8) == "a", "E7-3 判据是 >=（等于阈值也算过线）")
ok(pick_first_to_score([("a", 0.1)], 0.8) is None, "E7-4 无人过线 → None（落到 Thinker 的 otherwise）")

ok(pick_highest([("a", 0.5), ("b", 0.9), ("c", 0.3)]) == "b", "E8-1 Highest 取最高")
ok(pick_highest([("a", 0.7), ("b", 0.7)]) == "a", "E8-2 并列取第一个（`<=` 判据）")
ok(pick_highest([("a", 0.0), ("b", 0.0)]) is None, "E8-3 全 0 → None（要求 score > 0）")
ok(pick_highest([("a", 0.0), ("b", 0.1)]) == "b", "E8-4 0 分永远不会赢")

ok(pick_highest_to_score([("a", 0.9), ("b", 0.95)], 0.8) == "b", "E9-1 HighestToScore 取最高")
ok(pick_highest_to_score([("a", 0.8), ("b", 0.9)], 0.8) == "b", "E9-2 等于阈值不算过线，与 FirstToScore 相反")
ok(pick_highest_to_score([("a", 0.5)], 0.8) is None, "E9-3 无人过线 → None")

# ---------- E10 组合：两条 Consideration 的竞争 ----------
thirst = measured_scorer([(0.9, 1.0), (0.6, 1.0)], 0.5, WeightedSum())   # 1.5 → 夹到 1
danger = product_of_scorers([0.9, 0.9], 0.0)                              # 0.81
close(thirst, 1.0, "E10-1 加权和超过 1 后被夹住（两条高分的 consideration 会互相淹没）")
close(danger, 0.81, "E10-2 乘积型天然落在 0..1，不会溢出")
ok(pick_highest([("drink", thirst), ("flee", danger)]) == "drink", "E10-3 夹取后 thirst 胜出")
zeroed = sum_of_scorers([0.3, 0.3], 0.8)          # 0.6 < 0.8 ⇒ 被阈值打成 0
close(zeroed, 0.0, "E10-4a 低于阈值的 consideration 被归零，而不是保留 0.6")
ok(pick_highest([("drink", zeroed), ("flee", danger)]) == "flee",
   "E10-4b 归零后 Highest 只会在非零项里挑（0 分永不获胜）")
close(clamp(-1.0, 0.0, 1.0), 0.0, "E10-5 clamp 先夹上界再夹下界")

print("PASS =", PASS)
