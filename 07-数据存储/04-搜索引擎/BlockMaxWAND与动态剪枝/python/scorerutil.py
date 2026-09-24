"""MathUtil 与 ScorerUtil：浮点误差上界与竞争分过滤。

忠实转写自 apache/lucene@main：
  core/src/java/org/apache/lucene/util/MathUtil.java
  core/src/java/org/apache/lucene/search/ScorerUtil.java

这里的代码全是「纯算术」，不涉及倒排表，单独成文件便于逐条断言。
"""

import math
import struct

K1 = 1.2
B = 0.75


def f32(x):
    """Java 的 (float) 窄化：按单精度就近舍入。"""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def ulp_f32(x):
    """Math.ulp(float)：单精度下 x 的末位单位。

    不能直接用 `math.ulp(float(x))` —— 那是 **double** 的 ulp（约 4.4e-16），
    比 float 的 ulp（约 2.4e-7）小 9 个数量级，会让 minRequiredScore 的收敛循环
    空转十万次也退不出。这里按 IEEE-754 binary32 的位模式加一来取。
    """
    a = abs(f32(x))
    if a == 0.0:
        return 1.401298464324817e-45  # Float.MIN_VALUE
    if math.isinf(a):
        return math.inf
    bits = struct.unpack("<I", struct.pack("<f", a))[0]
    nxt = struct.unpack("<f", struct.pack("<I", bits + 1))[0]
    return nxt - a


def unsigned_min(a, b):
    return min(a, b)


# --------------------------------------------------------------------------
# MathUtil
# --------------------------------------------------------------------------

def sum_relative_error_bound(num_values):
    if num_values <= 1:
        return 0.0
    u = math.ldexp(1.0, -52)
    return (num_values - 1) * u


def sum_upper_bound(s, num_values):
    if num_values <= 2:
        return s
    b = sum_relative_error_bound(num_values)
    return (1.0 + 2 * b) * s


# --------------------------------------------------------------------------
# ScorerUtil
# --------------------------------------------------------------------------

def min_required_score(max_remaining_score, min_competitive_score, num_scorers):
    """ScorerUtil.minRequiredScore：把浮点误差也算进去的「本子句至少要多少分」。

    循环里减的是 **float 的 ulp**（源码注释明确说明：要的是 float ulp 以便更快收敛，
    不是 double ulp）。
    """
    mrs = min_competitive_score - max_remaining_score
    subtraction = ulp_f32(min_competitive_score)
    guard = 0
    while (mrs > 0
           and f32(sum_upper_bound(mrs + max_remaining_score, num_scorers))
           >= min_competitive_score):
        mrs -= subtraction
        guard += 1
        if guard > 100000:
            break
    return mrs


def filter_competitive_hits(docs, scores, max_remaining_score,
                            min_competitive_score, num_scorers):
    """ScorerUtil.filterCompetitiveHits：把「加上剩余上限也够不着」的命中丢掉。"""
    need = min_required_score(max_remaining_score, min_competitive_score,
                              num_scorers)
    if need <= 0:
        return list(docs), list(scores)
    out_d, out_s = [], []
    for d, s in zip(docs, scores):
        if s >= need:
            out_d.append(d)
            out_s.append(s)
    return out_d, out_s


def apply_optional_clause(docs, scores, clause_docs, clause_scores):
    """ScorerUtil.applyOptionalClause：命中加分，未命中保留。"""
    pos = {d: s for d, s in zip(clause_docs, clause_scores)}
    return list(docs), [s + pos.get(d, 0.0) for d, s in zip(docs, scores)]


def apply_required_clause(docs, scores, clause_docs, clause_scores):
    """ScorerUtil.applyRequiredClause：未命中的直接从缓冲区里剔除。"""
    pos = {d: s for d, s in zip(clause_docs, clause_scores)}
    out_d, out_s = [], []
    for d, s in zip(docs, scores):
        if d in pos:
            out_d.append(d)
            out_s.append(s + pos[d])
    return out_d, out_s
