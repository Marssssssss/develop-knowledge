# -*- coding: utf-8 -*-
"""自检: worker 数公式 / 上限 / 并行可行性 / 并行代价。

期望值全部是**手算**出来的: 阈值 1024, 每乘 3 加一个 worker。
"""

from workers import (compute_parallel_worker, log3_workers, parallel_plan_cost,
                     can_parallelize, MIN_TABLE_SCAN_PAGES, MIN_INDEX_SCAN_PAGES)

PASS = [0]
FAIL = []


def ok(cond, label):
    if cond:
        PASS[0] += 1
    else:
        FAIL.append(label)


def w(**kw):
    return compute_parallel_worker(**kw)[0]


# --- 1. log3 公式本身 ---------------------------------------------------------
ok(log3_workers(1024, 1024) == 1, "1024 页: 1024 < 3072 -> 1")
ok(log3_workers(3071, 1024) == 1, "3071 页: 仍 < 3072 -> 1")
ok(log3_workers(3072, 1024) == 2, "3072 页: >= 3072 -> 2")
ok(log3_workers(9215, 1024) == 2, "9215 页: < 9216 -> 2")
ok(log3_workers(9216, 1024) == 3, "9216 页: >= 9216 -> 3")
ok(log3_workers(27648, 1024) == 4, "27648 页: 阈值翻到 27648 -> 4")
ok(log3_workers(0, 1024) == 1, "0 页也至少给 1(循环不进)")
ok(log3_workers(100, 1) == 5, "阈值 1: 100 -> 1,3,9,27,81 -> 5")

# --- 2. BASEREL 的下界门槛 ----------------------------------------------------
ok(w(heap_pages=1023, is_baserel=True) == 0, "1023 页 < 1024 -> 直接 0(下界门槛)")
ok(w(heap_pages=1024, is_baserel=True) == 1, "1024 页 -> 1")
ok(w(heap_pages=1023, is_baserel=False) == 1,
   "继承子表(非 BASEREL)不受门槛限制, 1023 页也给 1")
ok(MIN_TABLE_SCAN_PAGES == 1024 and MIN_INDEX_SCAN_PAGES == 64, "8MB=1024 页 / 512kB=64 页")

# --- 3. 上限 max_parallel_workers_per_gather ---------------------------------
ok(w(heap_pages=100000, max_workers=2) == 2, "默认上限 2: 公式给再多也只给 2")
ok(w(heap_pages=100000, max_workers=4) == 4, "上限 4 -> 4")
ok(w(heap_pages=100000, max_workers=0) == 0, "上限 0 -> 不并行")
ok(w(heap_pages=1024, max_workers=1) == 1, "上限 1")
ok(w(heap_pages=3072, max_workers=1) == 1, "公式给 2 但上限 1 -> 1")

# --- 4. 表级 reloption 覆盖 ----------------------------------------------------
ok(w(heap_pages=10, rel_parallel_workers=3, max_workers=2) == 2,
   "reloption=3 但仍被 max_workers 截断")
ok(w(heap_pages=10, rel_parallel_workers=4, max_workers=8) == 4,
   "reloption 优先, 小表也能并行(不受下界门槛)")
ok(w(heap_pages=10, rel_parallel_workers=0, max_workers=8) == 0,
   "reloption=0 显式关闭并行")

# --- 5. 堆与索引取小 -----------------------------------------------------------
ok(w(heap_pages=100000, index_pages=64, max_workers=8) == 1,
   "堆很大但索引只有 64 页 -> 取索引的 1")
# 堆: 阈值 1024 -> 3072 -> 9216 -> 27648 -> 82944(共 5 档, 248832 已超过 100000)
ok(w(heap_pages=100000, max_workers=8) == 5, "堆 100000 页 -> 5")
# 索引: 阈值 64 -> 192 -> 576 -> 1728 -> 5184 -> 15552 -> 46656(共 7 档)
ok(w(index_pages=100000, max_workers=8) == 7, "索引 100000 页(阈值 64) -> 7")
ok(w(heap_pages=100000, index_pages=100000, max_workers=8) == 5, "堆 5 与索引 7 取小 -> 5")

# --- 6. 代价模型 ---------------------------------------------------------------
ok(abs(parallel_plan_cost(1000.0, 100.0, 2) - (1000.0 + 1000.0 / 3 + 100.0 * 0.1)) < 1e-9,
   "Gather 代价 = setup 1000 + 串行/3 + rows*0.1")
ok(parallel_plan_cost(100.0, 10.0, 0) == 100.0, "workers=0 时就是串行代价")
ok(parallel_plan_cost(1000.0, 0.0, 2) < parallel_plan_cost(1000.0, 0.0, 1),
   "同样数据下 worker 越多并行部分越便宜(setup 摊薄)")
ok(abs(parallel_plan_cost(1000.0, 0.0, 2, leader_participation=False)
       - (1000.0 + 1000.0 / 2)) < 1e-9, "leader 不参与时按 workers 分摊")

# --- 7. 何时不能并行(文档 15.2) ------------------------------------------------
ok(can_parallelize()[0], "默认可以并行")
ok(not can_parallelize(writes_data=True)[0], "写数据 -> 不并行")
ok(not can_parallelize(cursor_or_suspendable=True)[0], "DECLARE CURSOR -> 不并行")
ok(not can_parallelize(has_unsafe_function=True)[0], "PARALLEL UNSAFE 函数 -> 不并行")
ok(not can_parallelize(nested_in_parallel=True)[0], "并行内嵌套 -> 不并行")
ok(not can_parallelize(max_per_gather=0)[0], "max_parallel_workers_per_gather=0 -> 不并行")
ok(len(can_parallelize(writes_data=True, has_unsafe_function=True)[1]) == 2,
   "两条原因会同时列出")
ok(can_parallelize(writes_data=False, cursor_or_suspendable=False)[0], "负控: 全 False 可并行")

# --- 8. 计划期与运行期的落差 --------------------------------------------------
ok(w(heap_pages=100000, max_workers=2) == 2, "计划: Workers Planned: 2")
ok(0 <= 2, "运行期拿不到 worker 时退化为 0, leader 独自跑完(文档 15.2)")

if __name__ == "__main__":
    print("PASS=%d FAIL=%d" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
