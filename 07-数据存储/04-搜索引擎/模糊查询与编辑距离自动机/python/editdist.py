"""经典 Levenshtein 与 Damerau-Levenshtein（最优串对齐 OSA）距离。

转写自 Lucene 文档与 LevenshteinAutomata 的语义约定（`withTranspositions`）：
  * withTranspositions = false → 经典 Levenshtein：插入 / 删除 / 替换，各算 1
  * withTranspositions = true  → Damerau-Levenshtein 的 **OSA** 变体：额外允许
    相邻两字符交换算 1，但**一个字符只能参与一次交换**（这是 OSA 与真正的
    Damerau-Levenshtein 的区别，Lucene 用的是前者）

本文件只做「距离」这件事，用来和自动机的判定对拍。
"""


def levenshtein(a, b):
    """经典编辑距离（插入/删除/替换）。a、b 为码点序列。"""
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            # 朴素三选一：删除 / 插入 / 替换
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[m]


def damerau_osa(a, b):
    """OSA 变体：允许相邻交换算 1，但一个字符只能参与一次交换。"""
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    # d[i][j]，保留三行即可判断 a[i-2] 与 b[j-1] 的交换
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[n][m]


def distance(a, b, transpositions):
    """按 Lucene 的 transpositions 开关选一个。"""
    return damerau_osa(a, b) if transpositions else levenshtein(a, b)
