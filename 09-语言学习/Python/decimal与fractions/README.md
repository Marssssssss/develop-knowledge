# 数值精度模型:`decimal` 与 `fractions`

## 简介

- 二进制浮点 `float` 无法精确表示 `0.1` 这类十进制小数 → `1.1+2.2 != 3.3`、
  `0.1+0.2 != 0.3`(本 demo 实测复现)。
- `decimal`:十进制精确算术,数值 = **符号 + 系数 + 指数**(`significand × 10^exponent`),
  配 context 控制精度(默认 28 位)与 8 种舍入模式——会计/货币场景的标准答案。
- `fractions`:**精确有理数**(`分子/分母`,自动约分,分母恒正),做分数运算零损失。
- 三者关系:`float`(快、有误差)< `Fraction`(精确无理不可能、有理精确)>
  `Decimal`(十进制语义可控)。

## 原理详解

### 1. decimal 的三元组表示

```python
Decimal((0, (3, 1, 4), -2)) == Decimal("3.14")   # 符号 0,系数 314,指数 -2
Decimal((1, (1, 4), 0))   == Decimal("-14")
```

- 为保留**有效位**,系数不截尾零:`1.30 + 1.20 = 2.50`(尾零保留以指示显著性);
  乘法"教科书式"全位数:`1.30 * 1.20 = 1.5600`。
- 数值上 `2.50 == 2.5` 成立;`compare_total` 按抽象**表示**全序比较才区分(实测 `-1`)。
- 特殊值:`Infinity`/`-Infinity`/`NaN`,且区分 `-0` 与 `+0`(IEEE 854 系谱)。

### 2. "构造看输入、运算看上下文"

> 官方原文:"Context precision and rounding only come into play during arithmetic operations."

- `Decimal("3.1415926535")` 构造保留全部 11 位(prec 无关);
  加法才按 context 舍入:prec=6 时 `3.1415926535 + 2.7182818285 = 5.85987`,
  改 ROUND_UP(远离零)→ `5.85988`(实测)。
- 8 种舍入:CEILING / DOWN / FLOOR / HALF_DOWN / **HALF_EVEN(默认,银行家舍入)** /
  HALF_UP / UP / 05UP;默认 traps 含 `InvalidOperation`。
- `getcontext()`/`setcontext()` 按线程独立;`localcontext()` 临时切换惯用法:
  ```python
  with localcontext() as ctx:
      ctx.prec = 42
      s = calculate()
  s = +s                       # 一元 + 把结果舍回默认精度
  ```

### 3. 精度不足破坏结合律(Knuth 官方示例)

prec=8 时:`(11111113 + (-11111111)) + 7.51111111 = 9.5111111`,
但 `11111113 + ((-11111111) + 7.51111111) = 10`——两个结果不相等;
prec 提到 20 恢复恒等式(本 demo 实测)。教训:**舍入不是可结合的**。

### 4. 类型边界与运算符差异

- 跨类型**算术**:`Decimal + float` → `TypeError`(`Decimal` 与 `Fraction` 同样不行);
  **比较**允许混类型;`FloatOperation` trap 可把混比较也拦下(仅 `==` 静默)。
- `%` 与 `//`:Decimal 结果**符号随被除数、商向零截断**(IEEE 754-1985 语义),
  与 int 的"结果随除数 / 向下取整"不同:`Decimal(-7) % Decimal(4) == -3`、
  `Decimal(-7) // Decimal(4) == -1`(int 对应 `-7%4==1`、`-7//4==-2`);
  恒等式 `x == (x//y)*y + x%y` 仍成立。
- NaN:`==` 恒 False、`!=` 恒 True;序比较触发 `InvalidOperation`(默认在 trap 里);
  严格合规用 `compare()`(涉 NaN 返回 `Decimal('NaN')`)。

### 5. Fraction:精确有理数

- `Fraction(16, -10) == Fraction(-8, 5)`:构造即约分,分母保证为正;
  字符串解析:`'3/7'`、`' -3/7 '`、`'1.414213'`、`'-.125'`、`'7e-6'` 均可(官方示例)。
- **float 构造 = 二进制真值的精确有理化**:`Fraction(1.1)` 是
  `2476979795053773/2251799813685248`(≠ 11/10);`Fraction(Decimal('1.1'))` 才是 11/10。
  `Fraction(2.25) == 9/4` 因为 2.25 恰有精确二进制表示。
- **`limit_denominator(max_denominator)`**:找分母 ≤ N 的最接近分数——
  `Fraction('3.1415926535897932').limit_denominator(1000) == 355/113`(祖率)、
  `Fraction(cos(pi/3)).limit_denominator() == 1/2`(官方示例,本 demo 实测)。
- 混算语义与 Decimal 相反:`Fraction(1,3) + 0.5` 合法,返回 **float**(5/6 不再精确)。
- `math.floor/ceil/round` 遵循分数语义;`round` 平局取偶:`round(Fraction(7,2))==4`、
  `round(Fraction(5,2))==2`。

## 对比 / 选型

| 维度 | float | Decimal | Fraction |
| --- | --- | --- | --- |
| 表示 | 二进制 53 位尾数 | 十进制 系数×10^exp | 有理数 p/q |
| 0.1 精确 | ✗ | ✓(字符串构造) | ✓ |
| 精度控制 | 无(硬件) | context prec + 舍入 | 无限精确 |
| 速度 | 最快 | 慢(数量级) | 最慢(数论运算) |
| 典型用途 | 科学计算 | 货币/会计 | 精确分数/符号计算 |

## 环境准备

- OS:任意(纯标准库)
- Python:3.x;实测 3.13.14
- 依赖:无

## 运行方式

```bash
python3 main.py
```

## 关键代码片段

```python
assert Decimal(3.14).as_tuple()            # 无损暴露 float 二进制真值
assert str(Decimal(3.14)) == "3.140000000000000124344978758017532527446746826171875"

with localcontext() as ctx:                # 临时精度
    ctx.prec = 8
    assert (u + v) + w != u + (v + w)      # Knuth:舍入不可结合

assert Fraction(1.1).limit_denominator() == Fraction(11, 10)
```

## 性能与边界

- `Decimal` 的 C 实现(`_decimal`)远快于纯 Python 版,但相对 float 仍有数量级差距;
  `Fraction` 的约分(gcd)随位深增长,连分数迭代 30 步即到 1e-12(本 demo 实测)。
- 精度上限:Decimal 的 `MAX_PREC`(C 版约 999999999999999999 上下,依平台);
  Fraction 无上限,但位数爆炸时运算成本同步爆炸。
- 定点应用:加减与整数乘自动保持小数位;除法/非整数乘后要 `quantize()` 收尾。

## 注意事项与常见坑

- **坑:`Decimal(3.14)`** —— 把 float 的二进制误差原样带进来(50+ 位十进制);
  **永远用字符串构造** `Decimal("3.14")`。
- **坑:默认舍入 HALF_EVEN** —— `2.5 → 2`、`3.5 → 4`,与直觉的"四舍五入"不同;
  要传统行为用 `ROUND_HALF_UP`。
- **坑:上下文里 `ROUND_UP` 不是"四舍五入"** —— 是**远离零**(up = away from zero)。
- **坑:`Decimal + float` 不报语法错,运行期 TypeError** —— 静静写在表达式里,
  到测试才炸;比较运算倒是允许的,容易让人误以为算术也行。
- **坑:`Fraction(1.1)` 期望 11/10** —— 它是 float 真值的精确有理化;
  用 `Fraction(Decimal('1.1'))` 或 `limit_denominator()` 恢复"人类意图"。
- **坑:连分数/迭代收敛步数想当然** —— 斐波那契逼近误差 ~1/F(n)²,20 步只有 3.7e-9,
  到 1e-12 需约 30 步(本 demo 实测)。

## 参考资料(实际阅读过的权威来源)

- [decimal — Decimal fixed point and floating point arithmetic(官方库文档)](https://docs.python.org/3/library/decimal.html) —
  三元组构造、context/8 种舍入、Knuth 结合律示例、NaN 比较与 `%`/`//` 语义原文。
- [fractions — Rational numbers(官方库文档)](https://docs.python.org/3/library/fractions.html) —
  构造规则与官方示例数值、`limit_denominator` 语义、float/Decimal 构造差异原文。
