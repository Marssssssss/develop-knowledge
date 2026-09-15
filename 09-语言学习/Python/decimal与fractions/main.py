# -*- coding: utf-8 -*-
"""数值精度模型:decimal(十进制精确算术)与 fractions(有理数精确算术)。

运行: python3 main.py
依据: 官方库文档 decimal / fractions(见 README 参考资料)。
"""

import math
import sys
from decimal import (
    Decimal, getcontext, setcontext, localcontext, Context,
    ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_DOWN, ROUND_UP, InvalidOperation,
)
from fractions import Fraction

PASS = []


def ok(name):
    PASS.append(name)
    print(f"[PASS] {name}")


def demo_why_not_float():
    """二进制浮点没有 0.1 的精确表示 → 经典误差。"""
    assert 1.1 + 2.2 != 3.3
    assert 0.1 + 0.2 != 0.3
    assert 0.1 + 0.1 + 0.1 - 0.3 != 0
    # decimal 用十进制系数+指数,精确表示
    assert Decimal("1.1") + Decimal("2.2") == Decimal("3.3")
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
    assert Decimal("0.1") + Decimal("0.1") + Decimal("0.1") - Decimal("0.3") == 0
    # 内部三段式:sign + 系数元组 + 指数 → Decimal((0,(3,1,4),-2)) == 3.14
    assert Decimal((0, (3, 1, 4), -2)) == Decimal("3.14")
    assert Decimal((1, (1, 4), 0)) == Decimal("-14")
    ok("构造:float 有 0.1 误差,decimal 三元组(符号,系数,指数)精确表示")


def demo_float_to_decimal():
    """Decimal(3.14) 无损暴露 float 的真实二进制值 → 永远用字符串构造。"""
    exact = Decimal(3.14)
    assert str(exact) == "3.140000000000000124344978758017532527446746826171875"
    assert Decimal("3.14") != exact               # 字符串构造才是"人类意义的 3.14"
    assert Decimal.from_float(0.1).as_tuple().exponent < -50
    ok("float 转换:无暴露二进制真值(50+ 位),字符串构造才精确")


def demo_significance():
    """尾随零有意义:1.30+1.20=2.50(保留显著性),但数值上 == 2.5。"""
    assert Decimal("1.30") + Decimal("1.20") == Decimal("2.50")
    assert str(Decimal("1.30") + Decimal("1.20")) == "2.50"
    assert Decimal("2.50") == Decimal("2.5")       # 数值相等
    # compare_total 按抽象表示比较:2.50 < 2.5
    assert Decimal("2.50").compare_total(Decimal("2.5")) == Decimal("-1")
    # 教科书式乘法:1.30*1.20 四位有效
    assert str(Decimal("1.30") * Decimal("1.20")) == "1.5600"
    assert str(Decimal("1.3") * Decimal("1.2")) == "1.56"
    ok("显著性:尾零保留(构造看输入);compare_total 按表示比较才区分 2.50/2.5")


def demo_context():
    """构造看输入、运算看上下文:prec 只在运算时生效。"""
    with localcontext() as ctx:
        ctx.prec = 6
        pi = Decimal("3.1415926535")              # 构造保留全部 11 位
        assert str(pi) == "3.1415926535"
        assert str(pi + Decimal("2.7182818285")) == "5.85987"   # 运算按 prec=6
        assert str(Decimal(1) / Decimal(7)) == "0.142857"        # 1/7 截到 6 位
        ctx.rounding = ROUND_UP                      # 远离零:5.859874...→5.85988
        assert str(pi + Decimal("2.7182818285")) == "5.85988"
    assert getcontext().prec == 28                 # 默认精度 28
    ok("context:构造不受 prec 影响,运算才舍入;默认 prec=28")


def demo_rounding_modes():
    """8 种舍入模式;默认 ROUND_HALF_EVEN(银行家舍入)。"""
    assert getcontext().rounding == ROUND_HALF_EVEN
    assert Decimal("2.5").quantize(Decimal("1")) == Decimal("2")     # 平局取偶
    assert Decimal("3.5").quantize(Decimal("1")) == Decimal("4")
    assert Decimal("2.5").quantize(Decimal("1"), rounding=ROUND_HALF_UP) == Decimal("3")
    assert Decimal("7.325").quantize(Decimal(".01"), rounding=ROUND_DOWN) == Decimal("7.32")
    ok("舍入:默认 HALF_EVEN(2.5→2,3.5→4);HALF_UP/DOWN 可选")


def demo_associativity():
    """精度不足破坏结合律(Knuth 示例):prec=8 时 (u+v)+w ≠ u+(v+w)。"""
    with localcontext() as ctx:
        ctx.prec = 8
        u, v, w = Decimal(11111113), Decimal(-11111111), Decimal("7.51111111")
        left, right = (u + v) + w, u + (v + w)
        assert left != right and (str(left), str(right)) == ("9.5111111", "10")
        ctx.prec = 20
        assert (u + v) + w == u + (v + w)          # 提高精度恢复恒等式
    ok("结合律:prec=8 时 (u+v)+w=9.5111111 而 u+(v+w)=10;prec=20 恢复相等")


def demo_nan_and_comparison():
    """NaN:== 恒 False;序比较触发 InvalidOperation(默认在 trap 里)。"""
    nan = Decimal("NaN")
    assert (nan == nan) is False
    assert (nan != nan) is True
    try:
        nan < Decimal(1)
        raise AssertionError("NaN 序比较应触发 InvalidOperation")
    except InvalidOperation:
        pass
    # 严格合规用 compare():涉 NaN 返回 Decimal('NaN')
    assert nan.compare(Decimal(1)).is_nan()
    ok("NaN:== 恒 False;< 触发 InvalidOperation;compare() 返回 NaN")


def demo_type_rules():
    """跨类型算术:Decimal+float 报 TypeError;比较允许;//与% 向零截断。"""
    try:
        Decimal("1.5") + 0.5
        raise AssertionError("Decimal + float 应报 TypeError")
    except TypeError:
        pass
    assert Decimal("3.5") == 3.5                  # 比较允许混类型
    # % 与 //:Decimal 向零截断,与 int 的"向下取整"不同
    assert -7 % 4 == 1 and Decimal(-7) % Decimal(4) == Decimal("-3")
    assert -7 // 4 == -2 and Decimal(-7) // Decimal(4) == Decimal("-1")
    # 恒等式 x == (x//y)*y + x%y 对 Decimal 也成立
    q, r = divmod(Decimal(-7), Decimal(4))
    assert q * Decimal(4) + r == Decimal(-7)
    ok("类型:混算 float 报 TypeError;Decimal 的 % 符号随被除数,// 向零")


def demo_fraction_basics():
    """Fraction:分子/分母自动约分到最简;分母恒正。"""
    assert Fraction(16, -10) == Fraction(-8, 5)
    assert Fraction(16, -10).numerator == -8 and Fraction(16, -10).denominator == 5
    assert Fraction(123) == Fraction(123, 1)
    assert Fraction(6, 4) == Fraction(3, 2) == 1.5
    assert Fraction("3/7") == Fraction(3, 7)
    assert Fraction(" -3/7 ") == Fraction(-3, 7)
    assert Fraction("1.414213") == Fraction(1414213, 1000000)
    assert Fraction("-.125") == Fraction(-1, 8)
    assert Fraction("7e-6") == Fraction(7, 1000000)
    # 精确有理数算术:1/3+1/6 恰好 1/2
    assert Fraction(1, 3) + Fraction(1, 6) == Fraction(1, 2)
    assert Fraction(1, 3) * 3 == 1
    ok("Fraction 构造:自动约分、分母为正、字符串/科学计数法解析")


def demo_fraction_from_float():
    """Fraction(1.1) 是 float 真值的精确有理数,不是 11/10;limit_denominator 可恢复。"""
    assert Fraction(2.25) == Fraction(9, 4)
    f = Fraction(1.1)
    assert f != Fraction(11, 10)
    assert f.numerator == 2476979795053773 and f.denominator == 2251799813685248
    # Decimal 构造则精确(十进制文本 → 十进制数)
    assert Fraction(Decimal("1.1")) == Fraction(11, 10)
    # limit_denominator:找回"人类意图"的分数
    assert f.limit_denominator() == Fraction(11, 10)
    assert Fraction("3.1415926535897932").limit_denominator(1000) == Fraction(355, 113)
    assert Fraction(math.cos(math.pi / 3)).limit_denominator() == Fraction(1, 2)
    ok("float 精度语义:F(1.1)=2476979795053773/2251799813685248;limit_denominator 恢复")


def demo_fraction_arithmetics():
    """有理数精确性:无精度损失的连分数迭代。"""
    # 斐波那契连分数逼近黄金比例:精确有理数收敛(误差 ~1/F(n)²,需 ~30 步到 1e-12)
    phi = Fraction(1, 1)
    for _ in range(30):
        phi = 1 + 1 / phi
    assert abs(float(phi) - (1 + math.sqrt(5)) / 2) < 1e-12
    # 与 float 混算 → 返回 float(不报错,与 Decimal 的 TypeError 不同!)
    assert isinstance(Fraction(1, 3) + 0.5, float)
    val = Fraction(1, 3) + 0.5
    assert isinstance(val, float)
    assert abs(val - Fraction(5, 6)) < 1e-15     # 混算落回 float:5/6 不再精确
    assert math.floor(Fraction(355, 113)) == 3
    assert math.ceil(Fraction(355, 113)) == 4
    assert round(Fraction(7, 2)) == 4               # round half to even:7/2→4
    assert round(Fraction(5, 2)) == 2
    ok("混算:F(1,3)+0.5→float(与 Decimal 不同);round 取 half-to-even")


def main():
    print(f"Python {sys.version.split()[0]}\n")
    demo_why_not_float()
    demo_float_to_decimal()
    demo_significance()
    demo_context()
    demo_rounding_modes()
    demo_associativity()
    demo_nan_and_comparison()
    demo_type_rules()
    demo_fraction_basics()
    demo_fraction_from_float()
    demo_fraction_arithmetics()
    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
