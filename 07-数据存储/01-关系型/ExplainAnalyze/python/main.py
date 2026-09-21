# -*- coding: utf-8 -*-
"""EXPLAIN ANALYZE 读法 —— 演示入口。

数字取自 PostgreSQL 18 官方文档《Using EXPLAIN》14.1 的示例输出。
"""

from planmodel import Node, seq_scan_cost


def hr(t):
    print("\n== %s ==" % t)


def nested_loop_example():
    hr("1. 文档示例: Nested Loop + Bitmap + Index Scan")
    top = Node("Nested Loop", 4.65, 118.50, 10, 488,
               extra={"Buffers": "shared hit=36 read=6"})
    heap = Node("Bitmap Heap Scan on tenk1 t1", 4.36, 39.38, 10, 244,
                extra={"Recheck Cond": "(unique1 < 10)", "Heap Blocks": "exact=10"})
    bidx = Node("Bitmap Index Scan on tenk1_unique1", 0.00, 4.36, 10, 0,
                extra={"Index Cond": "(unique1 < 10)", "Index Searches": "1"})
    heap.children = [bidx]
    idx = Node("Index Scan using tenk2_unique2 on tenk2 t2", 0.29, 7.90, 1, 244,
               extra={"Index Cond": "(unique2 = t1.unique2)", "Index Searches": "10"})
    top.children = [heap, idx]

    top.run(10, first_ms=0.017, last_ms=0.051)
    heap.run(10, first_ms=0.009, last_ms=0.017)
    bidx.run(10, first_ms=0.004, last_ms=0.004)
    for _ in range(10):                     # 每个外行触发一次
        idx.run(1, first_ms=0.003, last_ms=0.003)

    print(top.render())
    print("\n  Index Scan 的 actual rows=%.2f 是**每次执行**的值, loops=%d" % (
        idx.actual_rows(), idx.nloops))
    print("  真实总行数 = rows × loops = %.0f" % idx.total_rows())
    print("  总耗时 = actual time(last) × loops = %.3f ms(文档原句: 0.030 ms)" % idx.total_time()[1])


def filter_example():
    hr("2. Rows Removed by Filter 与估算误差")
    s = Node("Seq Scan on tenk1", 0.00, 470.00, 7000, 244,
             extra={"Filter": "(ten < 7)", "Buffers": "shared hit=345"})
    s.run(7000, scanned=10000, first_ms=0.030, last_ms=1.995, removed=3000)
    print(s.render())
    print("  估算 cost=470 = 445(全表) + 10000 × cpu_operator_cost(0.0025)")
    print("  seq_scan_cost(345, 10000) =", seq_scan_cost(345, 10000))
    bad = Node("Seq Scan on polygon_tbl", 0.00, 1.09, 1, 85)
    bad.run(0, scanned=7, first_ms=0.023, last_ms=0.023, removed=7)
    print("\n" + bad.render())
    print("  估算 1 行、实测 0 行 —— 分母为 0, 这类节点要看 Rows Removed 而不是比值")


def caveats():
    hr("3. 三个看起来像 bug 的读数陷阱")
    band = Node("BitmapAnd", 0.0, 9.44, 40, 0)
    band.run(40, first_ms=0.001, last_ms=0.002)
    print("  BitmapAnd 的 actual rows 恒为 0:", band.actual_rows(),
          "(但估算 rows=40 是真实的)")
    lim = Node("Limit", 0.29, 14.33, 2, 244)
    c = Node("Index Scan", 0.29, 70.50, 10, 244)
    lim.children = [c]
    lim.run(2, first_ms=0.051, last_ms=0.071)
    c.run(2, first_ms=0.051, last_ms=0.070)
    print("  LIMIT 短路: 子节点估算 10 行、实测 2 行, 不是估算错误")
    mj = Node("Merge Join", 0.0, 100.0, 10, 16)
    mi = Node("Seq Scan(内层)", 0.0, 50.0, 100, 8)
    mj.children = [mi]
    mj.run(10, first_ms=0.1, last_ms=0.9)
    for _ in range(3):
        mi.run(100, first_ms=0.01, last_ms=0.2)
    print("  merge join 外层重复键: 内层报 %d 行, 但关系只有 100 行(回溯重扫被重复计数)"
          % mi.total_rows())


if __name__ == "__main__":
    print("EXPLAIN ANALYZE 读数模型(PostgreSQL 18 官方文档口径)")
    nested_loop_example()
    filter_example()
    caveats()
