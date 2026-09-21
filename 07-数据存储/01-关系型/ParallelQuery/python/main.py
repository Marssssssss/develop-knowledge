# -*- coding: utf-8 -*-
"""并行查询: worker 数是怎么算出来的 —— 演示入口。"""

from workers import (compute_parallel_worker, log3_workers, parallel_plan_cost,
                     can_parallelize, MIN_TABLE_SCAN_PAGES)


def hr(t):
    print("\n== %s ==" % t)


def table_of_workers():
    hr("1. 表越大, worker 越多(阈值三倍三倍涨)")
    print("  页数        log3 结果   默认上限 2 后")
    for pages in (512, 1023, 1024, 3071, 3072, 9216, 27648, 100000, 1000000):
        raw = log3_workers(pages, MIN_TABLE_SCAN_PAGES)
        real, _ = compute_parallel_worker(heap_pages=pages)
        print("  %-10d %-11d %d" % (pages, raw, real))
    print("  注意: 默认 max_parallel_workers_per_gather=2, 所以 3072 页以上都只给 2")


def knobs():
    hr("2. 四个旋钮的影响")
    print("  1023 页小表, BASEREL          :", compute_parallel_worker(heap_pages=1023))
    print("  1023 页小表, 非 BASEREL(继承子表):",
          compute_parallel_worker(heap_pages=1023, is_baserel=False))
    print("  1023 页小表 + reloption=4     :",
          compute_parallel_worker(heap_pages=1023, rel_parallel_workers=4, max_workers=8))
    print("  100000 页, 上限 8             :",
          compute_parallel_worker(heap_pages=100000, max_workers=8))
    print("  100000 页, 上限 0(关并行)      :",
          compute_parallel_worker(heap_pages=100000, max_workers=0))


def cost():
    hr("3. 并行计划的代价(parallel_setup_cost=1000, parallel_tuple_cost=0.1)")
    serial = 216018.33
    for w in (0, 1, 2, 4):
        c = parallel_plan_cost(serial, 1.0, w)
        print("  workers=%d -> Gather 总代价 %.2f" % (w, c))
    print("  结论: 表不够大时 1000 的启动代价就足以让并行计划输给串行计划")


def safety():
    hr("4. 什么时候根本不会生成并行计划(文档 15.2)")
    cases = [
        ("普通只读查询", {}),
        ("UPDATE / INSERT ... SELECT", {"writes_data": True}),
        ("DECLARE CURSOR", {"cursor_or_suspendable": True}),
        ("调用自定义函数(默认 UNSAFE)", {"has_unsafe_function": True}),
        ("并行查询内再发一条 SQL", {"nested_in_parallel": True}),
    ]
    for label, kw in cases:
        okp, why = can_parallelize(**kw)
        print("  %-28s 可并行=%-5s %s" % (label, okp, "; ".join(why)))


if __name__ == "__main__":
    print("并行查询 worker 数(公式转写自 allpaths.c compute_parallel_worker)")
    table_of_workers()
    knobs()
    cost()
    safety()
