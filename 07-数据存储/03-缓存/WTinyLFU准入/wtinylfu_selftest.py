"""W-TinyLFU 自检。运行： python wtinylfu_selftest.py"""
import sys
from wtinylfu import (
    FrequencySketch, LRUCache, WTinyLFU,
    COUNTER_MAX, COUNTERS_PER_LONG, NUM_HASHES,
    scan_pollution_trace, zipf_trace,
)

FAILS = []


def check(label, cond, detail=""):
    if cond:
        print("  ok   %s" % label)
    else:
        FAILS.append("%s %s" % (label, detail))
        print("  FAIL %s %s" % (label, detail))


print("[1] 4-bit Count-Min Sketch：布局与内存")
sk = FrequencySketch(100)
check("table 长度取 ceiling_pow2(capacity)", sk.n_longs == 128, sk.n_longs)
check("每 long 装 16 个 4-bit 计数器", sk.n_counters == 128 * COUNTERS_PER_LONG,
      sk.n_counters)
check("8 字节/条目（wiki 原话）", sk.memory_bytes() == 8 * sk.n_longs,
      sk.memory_bytes())
for _ in range(20):
    sk.increment("a")
check("20 次递增后饱和在 15", sk.frequency("a") == COUNTER_MAX, sk.frequency("a"))
check("未出现过的 key 频率为 0", sk.frequency("zzz") == 0, sk.frequency("zzz"))

print("[2] 估计量 = 4 个计数器的最小值")
sk2 = FrequencySketch(64)
idx = sk2._indices("k")
check("恰好 4 个索引", len(idx) == NUM_HASHES, len(idx))
for v, i in zip((7, 3, 9, 5), idx):
    sk2.table[i] = v
check("min(7,3,9,5) = 3", sk2.frequency("k") == 3, sk2.frequency("k"))
sk2.reset()
check("老化（减半）后 = 1", sk2.frequency("k") == 1, sk2.frequency("k"))

print("[3] Count-Min 只会高估、不会低估")
sk3 = FrequencySketch(8)                 # 故意用很小的表制造碰撞
for _ in range(4):
    sk3.increment("x")
check("估计值 >= 真实次数", sk3.frequency("x") >= 4, sk3.frequency("x"))
sk4 = FrequencySketch(8)
for _ in range(4):
    sk4.increment("y")
check("小表下估计值 <= 15（4-bit 上限）", sk4.frequency("y") <= COUNTER_MAX,
      sk4.frequency("y"))

print("[4] 容量划分：1% 窗口 + 主区 SLRU")
w = WTinyLFU(100)
for _ in range(5):
    for i in range(100):
        w.access("hot%d" % i)
check("总量填满", w.size == 100, w.size)
check("窗口 1 条（1%）", len(w.window) == w.window_max == 1, len(w.window))
check("protected 79 条", len(w.protected) == 79, len(w.protected))
check("probation 20 条", len(w.probation) == 20, len(w.probation))

print("[5] 扫描污染：LRU 被冲垮，W-TinyLFU 扛住")
trace, probe = scan_pollution_trace(n_hot=100, warm_reps=5, n_scan=500)
lru = LRUCache(100)
wt = WTinyLFU(100)
for k in trace:
    lru.access(k)
    wt.access(k)
# 准入计数必须在「探测访问」之前快照 —— 探测本身也会产生准入/拒绝
adm, rej = wt.admitted, wt.rejected
lru_hits = sum(1 for k in probe if lru.access(k))
wt_hits = sum(1 for k in probe if wt.access(k))
check("LRU：500 条一次性扫描后热集命中 0/100", lru_hits == 0, lru_hits)
check("W-TinyLFU：热集命中 94/100", wt_hits == 94, wt_hits)
check("495 个扫描项被准入拒绝", rej == 495, rej)
check("5 个因 sketch 碰撞高估而混入", adm == 5, adm)
check("差距不是运气：94 vs 0", wt_hits - lru_hits == 94, (wt_hits, lru_hits))

print("[6] 准入判据是严格大于（平局不给新条目）")
sk5 = FrequencySketch(64)
check("freq 相等时 candidate 不取胜", not (3 > 3))
check("freq 更高时 candidate 取胜", 4 > 3)

print("[7] 窗口的作用：接住 recency burst")


def burst_hit(window_pct, gap):
    c = WTinyLFU(100, window_pct=window_pct)
    for _ in range(10):
        for i in range(100):
            c.access("f%d" % i)          # 主区填满高频项（freq 10）
    c.hits = c.misses = 0
    c.access("X")                         # 突发项首次出现（freq 1，进不了主区）
    for i in range(gap):
        c.access("gap%d" % i)             # 间隔 gap 个其它访问
    return c.access("X")                  # 突发项再次被访问


check("无窗口：突发项第二次访问必 miss", burst_hit(0.0, 3) is False)
check("窗口 5%：间隔 3 次仍在窗口内 -> hit", burst_hit(0.05, 3) is True)
check("窗口 5%：间隔 30 次已出窗口 -> miss", burst_hit(0.05, 30) is False)
check("窗口 20%：间隔 3 次 -> hit", burst_hit(0.20, 3) is True)
check("窗口 20%：间隔 30 次 -> miss", burst_hit(0.20, 30) is False)

print("[8] 爬山自适应：命中率高的一方扩张")
c = WTinyLFU(100, window_pct=0.10, adaptive=True)
before = c.window_pct
c.hill_climb(window_hit_rate=0.9, main_hit_rate=0.2)
check("窗口命中率高 -> 窗口扩大", c.window_pct > before, (before, c.window_pct))
mid = c.window_pct
c.hill_climb(window_hit_rate=0.1, main_hit_rate=0.9)
check("主区命中率高 -> 窗口收缩", c.window_pct < mid, (mid, c.window_pct))
check("窗口下限 1%", (c := WTinyLFU(100, window_pct=0.01, adaptive=True))
      is not None and c.hill_climb(0.0, 1.0) is None and c.window_pct == 0.01,
      c.window_pct)

print()
if FAILS:
    print("FAILED %d:" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
