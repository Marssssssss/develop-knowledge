# -*- coding: utf-8 -*-
"""选择率模型与官方文档 69.1 全部五个算例对拍。"""

from selestim import (
    combine_independent, eqjoinsel_unique, rows, scale_cardinality,
    selectivity_eq_not_in_mcv, selectivity_range_unique,
    selectivity_range_with_mcv,
)

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


HIST_UNIQUE1 = [0, 993, 1997, 3050, 4040, 5036, 5957, 7057, 8029, 9016, 9995]
MCV_FREQS = [0.00333333] + [0.003] * 9


def main():
    print("1. 基数与 relpages 缩放")
    assert scale_cardinality(358, 10000, 358) == 10000
    assert abs(scale_cardinality(358, 10000, 716) - 20000) < 1e-9
    ok("pg_class 的 reltuples 按当前页数等比缩放——表膨胀后统计未更新时估算仍会修正")

    print("2. 范围选择率(唯一列,unique1 < 1000)")
    sel = selectivity_range_unique(HIST_UNIQUE1, 1000)
    assert abs(sel - 0.100697) < 1e-6
    assert round(rows(10000, sel)) == 1007          # 官方 EXPLAIN 显示 rows=1007
    ok(f"(1 + (1000-993)/(1997-993))/10 = {sel:.6f},rows=1007(与官方 EXPLAIN 一致)")

    assert abs(selectivity_range_unique(HIST_UNIQUE1, 50) - 0.005035) < 1e-6
    ok("值落在第一个桶:(0 + 50/993)/10 = 0.005035 → 50 行(连接例子的外侤基数)")

    print("3. 等值选择率(值不在 MCV,stringu1 = 'xxx')")
    sel_eq = selectivity_eq_not_in_mcv(MCV_FREQS, 676)
    assert abs(sel_eq - 0.0014559) < 1e-7
    assert round(rows(10000, sel_eq)) == 15
    ok("(1 - Σmcv)/(676 - 10) = 0.0014559 → 15 行(剩余群体均摊给其余 distinct 值)")

    print("4. 范围选择率(有 MCV,stringu1 < 'IAAAAA')")
    sel_m = selectivity_range_with_mcv(
        ["AAAAAA", "BAAAAA", "CAAAAA", "DAAAAA", "EAAAAA", "FAAAAA",
         "IAAAAA", "QAAAAA", "WAAAAA", "ZAAAAA"],          # 恰好前六 < 'IAAAAA'
        MCV_FREQS, None, lambda v: v < "IAAAAA", hist_frac_guess=0.298387)
    assert abs(sel_m - 0.307669) < 1e-6
    assert round(rows(10000, sel_m)) == 3077
    ok("MCV 精确部分 0.01833333 + 0.298387×0.96966667 = 0.307669 → 3077 行"
       "(直方图不含 MCV 占比,两个群体分开估再合并)")

    print("5. 多条件独立相乘")
    sel_and = combine_independent(0.100697, 0.0014559)
    assert abs(sel_and - 0.0001466) < 1e-7
    assert round(rows(10000, sel_and)) == 1
    ok("独立假设:0.100697 × 0.0014559 = 0.0001466 → 1 行(相关性强的列会放大误差)")

    print("6. 等值连接 eqjoinsel(双唯一列)")
    j_sel = eqjoinsel_unique(0, 0, 10000, 10000)
    assert abs(j_sel - 0.0001) < 1e-12
    outer = round(rows(10000, selectivity_range_unique(HIST_UNIQUE1, 50)))
    assert (outer * 10000) * j_sel == 50
    ok("连接选择率 = 1/max(10000,10000) = 0.0001;连接行数 = 笛卡尔积 × 选择率 = 50"
       "(用外层约束后基数 50 × 内层 10000,不是 50×1)")

    print("7. 误差传播:选择率平方级放大")
    s = 0.01
    assert combine_independent(s, s) == 0.0001
    ok("两个 1% 的条件相乘得 0.01%——独立假设下误差相乘放大,"
       "这正是多列相关时计划崩坏的数学根源(需多列统计缓解)")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
