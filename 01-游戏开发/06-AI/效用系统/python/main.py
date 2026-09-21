"""big-brain（Bevy 的 Utility AI 库）评分与选择链路的 Python 转写。

逐行实读后转写的官方源码（zkat/big-brain，main 分支）：

- ``src/scorers.rs``   Score、FixedScore、AllOrNothing、SumOfScorers、ProductOfScorers、
                       WinningScorer、EvaluatingScorer、MeasuredScorer 的 system 实现
- ``src/measures.rs``  WeightedSum / WeightedProduct / ChebyshevDistance / WeightedMeasure
- ``src/evaluators.rs`` LinearEvaluator / PowerEvaluator / SigmoidEvaluator + clamp
- ``src/pickers.rs``   FirstToScore / Highest / HighestToScore
- ``src/choices.rs``   Choice::calculate

语言差异显式落地：
- Rust 的 ``f32`` 除以 0 得 ``inf``/-``inf`` → Python 直接除会抛 ``ZeroDivisionError``，
  这里用 ``_fdiv`` 显式模拟 IEEE 语义（Sigmoid 在默认 k=-0.5 下**真的会撞到除零**）；
- Rust 的 ``Score::set`` 越界 ``panic!`` → Python 抛 ``ValueError``；
- Rust ``fold(0f32, ...)`` 的初值语义原样保留（这正是 WeightedProduct 恒为 0 的原因）。
"""

import math


def _fdiv(num, den):
    """f32 除法：0 分母产生 inf/-inf 而不是抛异常。"""
    if den == 0.0:
        if num == 0.0:
            return float("nan")
        return math.copysign(float("inf"), num) * math.copysign(1.0, den)
    return num / den


def clamp(val, low, high):
    """evaluators.rs 里的 clamp：先夹上界再夹下界。"""
    v = high if val > high else val
    return low if v < low else v


def _clamp01(v):
    return clamp(v, 0.0, 1.0)


# ---------------------------------------------------------------- Score

class Score:
    """scorers.rs：值域 0.0..=1.0，set 越界 panic。"""

    def __init__(self, value=0.0):
        self.v = value

    def get(self):
        return self.v

    def set(self, value):
        if not (0.0 <= value <= 1.0):
            raise ValueError("Score value must be between 0.0 and 1.0")
        self.v = value

    def set_unchecked(self, value):
        self.v = value


# ---------------------------------------------------------------- Evaluators

class LinearEvaluator:
    """LinearEvaluator：new() = 恒等；new_ranged(min,max)；new_inversed() = new_ranged(1,0)。"""

    def __init__(self, xa=0.0, ya=0.0, xb=1.0, yb=1.0):
        self.xa, self.ya, self.xb, self.yb = xa, ya, xb, yb
        self.dy_over_dx = (yb - ya) / (xb - xa)

    @staticmethod
    def new():
        return LinearEvaluator(0.0, 0.0, 1.0, 1.0)

    @staticmethod
    def new_inversed():
        return LinearEvaluator.new_ranged(1.0, 0.0)

    @staticmethod
    def new_ranged(min_, max_):
        return LinearEvaluator(min_, 0.0, max_, 1.0)

    def evaluate(self, value):
        return clamp(self.ya + self.dy_over_dx * (value - self.xa), self.ya, self.yb)


class PowerEvaluator:
    """PowerEvaluator：power 被 clamp 到 0..=10000，默认 2。"""

    def __init__(self, power=2.0, xa=0.0, ya=0.0, xb=1.0, yb=1.0):
        self.power = clamp(power, 0.0, 10000.0)
        self.xa, self.ya, self.xb = xa, ya, xb
        self.dy = yb - ya

    @staticmethod
    def new(power):
        return PowerEvaluator(power, 0.0, 0.0, 1.0, 1.0)

    @staticmethod
    def new_ranged(power, min_, max_):
        return PowerEvaluator(power, min_, 0.0, max_, 1.0)

    def evaluate(self, value):
        cx = clamp(value, self.xa, self.xb)
        return self.dy * ((cx - self.xa) / (self.xb - self.xa)) ** self.power + self.ya


class SigmoidEvaluator:
    """SigmoidEvaluator：k 被 clamp 到 (-0.99999, 0.99999)，默认 -0.5。

    注意官方 ``two_over_dx = (2.0 / (xb - ya)).abs()`` 用的是 **ya 不是 xa**；
    公开的 new/new_ranged 都传 ya=0，所以这个偏差暂时不可达（见 README 注意事项）。
    """

    def __init__(self, k=-0.5, xa=0.0, ya=0.0, xb=1.0, yb=1.0):
        self.k = clamp(k, -0.99999, 0.99999)
        self.xa, self.xb, self.ya, self.yb = xa, xb, ya, yb
        self.two_over_dx = abs(2.0 / (xb - ya))
        self.x_mean = (xa + xb) / 2.0
        self.y_mean = (ya + yb) / 2.0
        self.dy_over_two = (yb - ya) / 2.0
        self.one_minus_k = 1.0 - self.k

    @staticmethod
    def new(k):
        return SigmoidEvaluator(k, 0.0, 0.0, 1.0, 1.0)

    @staticmethod
    def new_ranged(k, min_, max_):
        return SigmoidEvaluator(k, min_, 0.0, max_, 1.0)

    def evaluate(self, x):
        d = clamp(x, self.xa, self.xb) - self.x_mean
        numerator = self.two_over_dx * d * self.one_minus_k
        denominator = self.k * abs(1.0 - 2.0 * (self.two_over_dx * d)) + 1.0
        return clamp(self.dy_over_two * _fdiv(numerator, denominator) + self.y_mean,
                     self.ya, self.yb)


# ---------------------------------------------------------------- Measures

class WeightedSum:
    def calculate(self, scores):
        return sum(s * w for s, w in scores)


class WeightedProduct:
    """官方实现：fold 初值是 0.0 ⇒ 结果恒为 0（见 README 注意事项）。"""

    def calculate(self, scores):
        acc = 0.0
        for s, w in scores:
            acc = acc * s * w
        return acc


class ChebyshevDistance:
    def calculate(self, scores):
        best = 0.0
        for s, w in scores:
            best = max(s * w, best)
        return best


class WeightedMeasure:
    """默认 measure：加权二次平均 sqrt(Σ (w/Σw) * s²)。"""

    def calculate(self, scores):
        wsum = sum(w for _s, w in scores)
        if wsum == 0.0:
            return 0.0
        return sum(w / wsum * s ** 2.0 for s, w in scores) ** (1.0 / 2.0)


# ---------------------------------------------------------------- 复合 Scorer

def all_or_nothing(children, threshold):
    """任一孩子低于阈值 ⇒ 整体 0；否则求和后夹到 1。判据是 ``<``（相等放行）。"""
    total = 0.0
    for s in children:
        if s < threshold:
            return 0.0
        total += s
    return _clamp01(total)


def sum_of_scorers(children, threshold):
    total = sum(children)
    if total < threshold:
        return 0.0
    return _clamp01(total)


def product_of_scorers(children, threshold, use_compensation=False):
    product = 1.0
    n = 0
    for s in children:
        product *= s
        n += 1
    if use_compensation and product < 1.0:
        mod_factor = 1.0 - 1.0 / n
        makeup = (1.0 - product) * mod_factor
        product += makeup * product
    if product < threshold:
        return 0.0
    return _clamp01(product)


def winning_scorer(children, threshold):
    if not children:
        return 0.0
    best = max(children)
    return 0.0 if best < threshold else _clamp01(best)


def measured_scorer(children_with_weight, threshold, measure=None):
    measure = measure or WeightedMeasure()
    value = measure.calculate(children_with_weight)
    if value < threshold:
        return 0.0
    return _clamp01(value)


# ---------------------------------------------------------------- Pickers

def pick_first_to_score(choices, threshold):
    """``value >= threshold`` 的第一个（非严格）。choices: [(label, score), ...]"""
    for label, value in choices:
        if value >= threshold:
            return label
    return None


def pick_highest(choices):
    """严格大于当前最大值，且必须 > 0；并列取第一个。"""
    best_label, best_score = None, 0.0
    for label, score in choices:
        if score <= best_score or score <= 0.0:
            continue
        best_score, best_label = score, label
    return best_label


def pick_highest_to_score(choices, threshold):
    """严格大于阈值，再取最高；并列取第一个。"""
    best_label, best_score = None, 0.0
    for label, score in choices:
        if score <= threshold or score <= best_score:
            continue
        best_score, best_label = score, label
    return best_label
