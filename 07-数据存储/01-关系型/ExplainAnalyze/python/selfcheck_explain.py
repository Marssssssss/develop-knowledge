# -*- coding: utf-8 -*-
"""自检: EXPLAIN ANALYZE 的读数口径。

每个断言都对得上官方文档《Using EXPLAIN》里的一句原话或一个实际数字。
"""

from planmodel import Node, seq_scan_cost

PASS = [0]
FAIL = []


def ok(cond, label):
    if cond:
        PASS[0] += 1
    else:
        FAIL.append(label)


# --- 1. cost 估算: 文档 14.1.1 的算式 -----------------------------------------
ok(abs(seq_scan_cost(345, 10000) - 445.0) < 1e-9,
   "tenk1: 345*1.0 + 10000*0.01 = 445(文档原值)")
ok(abs(seq_scan_cost(1, 10) - 1.1) < 1e-9, "1 页 10 行 -> 1.10")
# 带 WHERE 的版本: 445 + 10000 * cpu_operator_cost(0.0025) = 470
ok(abs(seq_scan_cost(345, 10000) + 10000 * 0.0025 - 470.0) < 1e-9,
   "加 Filter 后 445 + 10000*0.0025 = 470(文档原值)")

# --- 2. actual rows / loops: 每次执行的平均值 --------------------------------
inner = Node("Index Scan", est_startup=0.29, est_total=7.90, est_rows=1, est_width=244)
for _ in range(10):                 # 文档: loops=10, actual rows=1.00
    inner.run(1, first_ms=0.003, last_ms=0.003)
ok(inner.nloops == 10, "loops 累计为 10")
ok(abs(inner.actual_rows() - 1.00) < 1e-9, "actual rows 是每次执行的均值 = 1.00")
ok(abs(inner.total_rows() - 10.0) < 1e-9, "真实总行数 = rows × loops = 10")
f, l = inner.actual_time()
ok(abs(f - 0.003) < 1e-9 and abs(l - 0.003) < 1e-9, "actual time 也是均值 0.003..0.003")
tf, tl = inner.total_time()
ok(abs(tl - 0.030) < 1e-9, "文档: 该节点总耗时 0.030 ms(0.003 × 10)")

# --- 3. 多行/多 loop 时均值与总和分离 -----------------------------------------
n = Node("Seq Scan", est_rows=600, est_width=8)
n.run(600, first_ms=0.01, last_ms=0.5)
n.run(0, first_ms=0.01, last_ms=0.3)
ok(n.nloops == 2, "两次 loop")
ok(abs(n.actual_rows() - 300.0) < 1e-9, "一次 600 行一次 0 行 -> 均值 300")
ok(abs(n.total_rows() - 600.0) < 1e-9, "总行数仍是 600")
ok(abs(n.actual_time()[1] - 0.4) < 1e-9, "last 取两次之和 / 2 = 0.4")

# --- 4. 估算误差: est / actual(真实总行数) -----------------------------------
under = Node("Seq Scan", est_rows=1, est_width=8)
under.run(7000, first_ms=0.03, last_ms=1.995)
ok(abs(under.est_error() - 1.0 / 7000.0) < 1e-12, "低估 7000 倍 -> est_error = 1/7000")
ok(abs(1.0 / under.est_error() - 7000.0) < 1e-6, "反过来是低估 7000 倍")
over = Node("Seq Scan", est_rows=7000, est_width=8)
over.run(7, first_ms=0.001, last_ms=0.002)
ok(abs(over.est_error() - 1000.0) < 1e-6, "高估 1000 倍 -> est_error = 1000")
ok(Node("Seq Scan", est_rows=0).est_error() is None, "估算为 0 时不做比值")
ok(Node("Seq Scan", est_rows=5).est_error() is None, "未执行(loops=0)时不做比值")

# --- 5. Rows Removed by Filter 只在拒绝了行时出现 ------------------------------
clean = Node("Seq Scan", est_rows=7000, est_width=244)
clean.run(7000, scanned=7000, removed=0)
dirty = Node("Seq Scan", est_rows=7000, est_width=244)
dirty.run(7000, scanned=10000, removed=3000)
ok("Rows Removed by Filter" not in clean.render(), "一行没拒 -> 不出现该行")
ok("Rows Removed by Filter: 3000" in dirty.render(), "拒了 3000 行 -> 出现(文档 ten<7 例)")
ok(dirty.scanned == 10000 and dirty.rows_removed == 3000, "扫描 10000 输出 7000 拒 3000")

# --- 6. BitmapAnd / BitmapOr 的 actual rows 恒为 0 ----------------------------
band = Node("BitmapAnd", est_rows=40, est_width=0)
band.run(40, first_ms=0.001, last_ms=0.002)
ok(band.actual_rows() == 0.0, "BitmapAnd 的 actual rows 恒 0(文档 Caveats)")
ok(band.total_rows() == 0.0, "即使乘 loops 也还是 0")
bor = Node("BitmapOr", est_rows=0, est_width=0)
bor.run(17, first_ms=0.001, last_ms=0.002)
ok(bor.actual_rows() == 0.0, "BitmapOr 同理")
seq = Node("Seq Scan", est_rows=40, est_width=8)
seq.run(17, first_ms=0.001, last_ms=0.002)
ok(abs(seq.actual_rows() - 17.0) < 1e-9, "普通节点不受影响(负控)")

# --- 7. LIMIT 短路: 估算按跑到底显示, 实测只有 2 行 ---------------------------
limit = Node("Limit", est_startup=0.29, est_total=14.33, est_rows=2, est_width=244)
child = Node("Index Scan", est_startup=0.29, est_total=70.50, est_rows=10, est_width=244)
limit.children = [child]
limit.run(2, first_ms=0.051, last_ms=0.071)
child.run(2, first_ms=0.051, last_ms=0.070)
ok(abs(limit.actual_rows() - 2.0) < 1e-9, "Limit 实测 2 行")
ok(child.est_rows == 10 and abs(child.actual_rows() - 2.0) < 1e-9,
   "子节点估算按跑到底显示 10, 实测 2 —— 不是估算错误")
ok(abs(child.est_error() - 5.0) < 1e-9, "这个 5 倍落差是展示口径差异, 不能当估算问题")

# --- 8. merge join 重复键: 内层 actual rows 被虚增 ----------------------------
inner_rel = 100                      # 内层关系真实 100 行
mj = Node("Merge Join", est_rows=10, est_width=16)
minner = Node("Seq Scan", est_rows=100, est_width=8)
mj.children = [minner]
mj.run(10, first_ms=0.1, last_ms=0.9)
for _ in range(3):                   # 外层同一键值重复 3 次 -> 内层被回溯重扫
    minner.run(inner_rel, first_ms=0.01, last_ms=0.2)
ok(abs(minner.total_rows() - 300.0) < 1e-9,
   "外层 3 个重复键 -> 内层报 300 行(实际关系只有 100 行)")
ok(minner.total_rows() > inner_rel, "文档: 内层 actual rows 可能显著大于关系真实行数")
ok(abs(minner.actual_rows() - 100.0) < 1e-9, "但每次 loop 仍然报 100")

# --- 9. never executed 与渲染 --------------------------------------------------
ne = Node("Seq Scan", est_rows=10, est_width=8)
ok("never executed" in ne.render(), "loops=0 时渲染为 (never executed)")
ne.run(3, first_ms=0.001, last_ms=0.002)
ok("never executed" not in ne.render(), "执行过就不再标注(负控)")
ok("rows=3.00 loops=1" in ne.render(), "单次执行的渲染")

if __name__ == "__main__":
    print("PASS=%d FAIL=%d" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
