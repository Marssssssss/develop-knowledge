# -*- coding: utf-8 -*-
"""autovacuum 与事务 ID 回绕 —— 演示入口。"""

from autovac import needs_vacanalyze, thresholds, unfrozen_ratio
import xid


def hr(t):
    print("\n== %s ==" % t)


def scenario_threshold():
    hr("1. 阈值随表增长, 但被 1 亿封顶")
    print("  行数        vacthresh        anlthresh")
    for n in (0, 100, 10000, 10 ** 6, 10 ** 8, 10 ** 9):
        vt, vit, at = thresholds(n)
        print("  %-11d %-16.0f %.0f" % (n, vt, at))
    print("  10 亿行时 0.2*N = 2 亿 > 100,000,000 -> 被 autovacuum_vacuum_max_threshold 封顶")


def scenario_scale():
    hr("2. 同一份死元组, 小表触发大表不触发")
    for n in (100, 10000, 10 ** 6):
        d, a, w, sc, th = needs_vacanalyze(n, dead_tuples=2000)
        print("  %-8d 行: 阈值 %-10.0f 死元组 2000 -> vacuum=%-5s score=%.2f"
              % (n, th[0], d, sc["vac"]))
    print("  这就是为什么大表要**单独调** autovacuum_vacuum_scale_factor")


def scenario_insert():
    hr("3. 纯插入表: 阈值按未冻结页面缩放")
    for pages, frozen in ((0, 0), (1000, 0), (1000, 500), (1000, 1000)):
        vt, vit, at = thresholds(100000, relpages=pages, relallfrozen=frozen)
        print("  pages=%-5d allfrozen=%-5d 未冻结比例=%.2f -> insert 阈值=%.0f"
              % (pages, frozen, unfrozen_ratio(pages, frozen), vit))
    print("  全冻结的静态表几乎不因插入被 vacuum(只靠防回绕那条通路)")


def scenario_xid():
    hr("4. 事务 ID 回绕的四条防线(oldest_datfrozenxid=1000)")
    L = xid.limits(1000)
    for k in ("vac", "warn", "stop", "wrap"):
        print("  %-5s = %-12d 剩余 %.4f%%" % (k, L[k], xid.remaining_pct(L[k], L["wrap"])))
    print("  区间判定:", {k: xid.classify(L[k], L) for k in ("vac", "warn", "stop", "wrap")})
    print("  过了 stop 之后: ERROR 'database is not accepting commands that assign new")
    print("  transaction IDs to avoid wraparound data loss'")


def scenario_freeze():
    hr("5. 静态表被强制 vacuum 的间隔")
    print("  间隔 ≈ autovacuum_freeze_max_age - vacuum_freeze_min_age =",
          xid.freeze_interval(), "个事务")
    print("  想拉长间隔: 调大 freeze_max_age 或调小 freeze_min_age(文档原话)")


if __name__ == "__main__":
    print("autovacuum 阈值与事务 ID 回绕(源码 + 官方文档逐条转写)")
    scenario_threshold()
    scenario_scale()
    scenario_insert()
    scenario_xid()
    scenario_freeze()
