# -*- coding: utf-8 -*-
"""PostgreSQL 选择率与基数估计模型(公式与算例对照官方 69.1 Row Estimation Examples)。

五条口径:
  1. 基数:pg_class 的 relpages/reltuples,按当前页数线性缩放
  2. 范围(无 MCV 的唯一列):桶内线性插值
  3. 等值且值不在 MCV:剩余群体均摊给其余 distinct 值
  4. 范围(有 MCV):MCV 部分精确 + 直方图部分 × 直方图占比
  5. 多条件独立 → 选择率相乘;等值连接 eqjoinsel(唯一列)按 max(行数) 均摊
"""


def scale_cardinality(relpages, reltuples, current_pages):
    """规划器取当前真实页数(廉价),与 relpages 不一致时按比例缩放 reltuples。"""
    if current_pages == relpages:
        return reltuples
    return reltuples * current_pages / relpages


def selectivity_range_unique(histogram, value):
    """唯一列无 MCV:值所在桶按线性分布取分数,加前面整桶,除以桶数。"""
    n = len(histogram) - 1
    for i in range(n):
        lo, hi = histogram[i], histogram[i + 1]
        if lo <= value <= hi:
            whole = i                                  # 前面 i 个整桶
            frac = (value - lo) / (hi - lo) if hi > lo else 0.0
            return (whole + frac) / n
    return 1.0 if value > histogram[-1] else 0.0


def selectivity_eq_not_in_mcv(mcv_freqs, n_distinct, null_frac=0.0):
    """值不在 MCV:非 MCV 群体均摊给 (n_distinct - n_mcv) 个其余值。"""
    if n_distinct < 0:                                  # -1 = 全唯一
        return None
    return (1 - null_frac - sum(mcv_freqs)) / (n_distinct - len(mcv_freqs))


def selectivity_range_with_mcv(mcv_vals, mcv_freqs, histogram, predicate, hist_frac_guess):
    """MCV 部分逐值精确判定;直方图部分用调用方给的桶内估计算例。"""
    mcv_sel = sum(f for v, f in zip(mcv_vals, mcv_freqs) if predicate(v))
    hist_fraction = 1.0 - sum(mcv_freqs)                # (无 null 时)
    return mcv_sel + hist_frac_guess * hist_fraction


def combine_independent(*selectivities):
    """规划器假设多条件独立:选择率相乘。"""
    r = 1.0
    for s in selectivities:
        r *= s
    return r


def eqjoinsel_unique(null_frac1, null_frac2, num_rows1, num_rows2):
    """两列都全唯一且无 MCV:(1-nf1)(1-nf2) / max(n1,n2)。"""
    return (1 - null_frac1) * (1 - null_frac2) / max(num_rows1, num_rows2)


def rows(table_cardinality, selectivity):
    return table_cardinality * selectivity
