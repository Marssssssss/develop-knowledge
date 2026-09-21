"""时间序列特征构造:因果版 / 错位版 / 泄漏版,以及三处最经典的泄漏源。

因果性的判据只有一条,而且必须说准:**第 t 行的特征不得依赖 x[j] (j > t)**。
「只用历史」不等于「不碰当期」—— 在"用 t 期已知量预测 t+1"的设定下,x[t] 是
决策时刻的已知量,含它**不构成**未来依赖。把两件事混为一谈,就会写出
"把合法特征当泄漏删掉"或者"把真泄漏当对齐问题放过"两类相反的错误。

于是三档:
  * 因果 —— 窗口右端停在 t-1,严格只看过去。
  * 错位 —— 窗口右端落在 t,含当期。这是**对齐/命名**问题:特征的注释若写着
    "过去 5 期均值",而代码实际含了当期,文档与实现不符;是否算泄漏取决于标签
    的时点,不能一概而论。
  * 泄漏 —— 窗口伸到 t 之后(center=True 的右半边,或 shift(-1))。无论标签怎么
    定义都错,因为它在决策时刻读到了尚未发生的观测。

重叠标签是第三处泄漏,它不在特征侧而在标签侧:标签若定义为未来 h 步的统计量
(y[t] = mean(x[t+1..t+h])),训练集最后一行的标签就落在测试期里 —— 这时才轮到
`gap` 出场。三者中只有它和时间切分有关。
"""

from rolling import rolling_mean, shift


def lag(x, p):
    """纯滞后:p 期前的值。"""
    return shift(x, p)


def causal_rolling_mean(x, window, shift_by=1):
    """先滚动后平移:第 t 行拿到右端在 t-shift_by 的窗口均值。shift_by>=1 才是因果的。"""
    return shift(rolling_mean(x, window), shift_by)


def shifted_rolling_mean(x, window):
    """原样返回 rolling_mean —— closed='right' 故第 t 行含 x[t]。对齐差 1 格。"""
    return rolling_mean(x, window)


def leaky_rolling_mean_center(x, window):
    """center=True 的窗口横跨当期两侧,第 t 行含 x[t+1] —— 直接看到未来。"""
    return rolling_mean(x, window, center=True)


def leaky_rolling_mean_shift_neg(x, window):
    """shift(-1):把未来的窗口搬到现在。等价于"用 t+1 的统计量预测 t 的目标"。"""
    return shift(rolling_mean(x, window), -1)


def expanding_mean_causal(x, shift_by=1):
    from rolling import expanding_mean as em
    return shift(em(x), shift_by)


def build_matrix(series, specs):
    """series: 若干个等长 list;specs: [(源, 函数), ...]。返回 (行索引, 设计矩阵)。

    只保留所有列都非 None 的行 —— 这一步必须用**同一套**行掩码,否则因果版与
    泄漏版会因为 NaN 位置不同而可比性尽失。
    """
    cols = []
    for src, fn in specs:
        cols.append(fn(src))
    n = len(cols[0])
    rows = [i for i in range(n) if all(c[i] is not None for c in cols)]
    X = [[1.0] + [c[i] for c in cols] for i in rows]
    return rows, X


def standardize_fit(X, j):
    """第 j 列(不含截距)的均值/总体标准差。"""
    col = [row[j] for row in X]
    mu = sum(col) / len(col)
    sd = (sum((v - mu) ** 2 for v in col) / len(col)) ** 0.5
    return mu, sd


def standardize_apply(X, j, mu, sd):
    if sd == 0:
        return [row[:] for row in X]
    out = [row[:] for row in X]
    for r in out:
        r[j] = (r[j] - mu) / sd
    return out
