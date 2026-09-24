"""03-令牌桶与预热限流 自检。运行：python selfcheck_guava_ratelimiter.py"""

import sys

from guava_ratelimiter import LONG_MAX, SmoothBursty, SmoothWarmingUp, saturated_add

OK = []
FAIL = []


def ck(name, cond):
    (OK if cond else FAIL).append(name)


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ---- 1. 稳定间隔与突发容量（SmoothBursty） --------------------------------
b = SmoothBursty()
b.set_rate(5.0, 0)
ck("stableInterval = 1e6/qps = 200000us", near(b.stable_interval_micros, 200_000.0, 1e-9))
ck("默认 maxBurstSeconds=1.0 ⇒ maxPermits = qps = 5", near(b.max_permits, 5.0))
ck("初态 storedPermits = 0.0（源码 initial state 分支）", b.stored_permits == 0.0)
ck("初态 nextFreeTicketMicros = 0", b.next_free_ticket_micros == 0)

# ---- 2. 首次 acquire 免费（nextFreeTicketMicros 初始 0 在过去） ------------
b = SmoothBursty()
b.set_rate(5.0, 0)
ck("第一次 acquire(1) 返回 0（moment=0 在过去）", b.acquire(1, 0) == 0.0)
ck("第一次 acquire 已把 nextFreeTicket 推到 200000", b.next_free_ticket_micros == 200_000)
ck("紧接着第二次 acquire(1) 要等 0.2s", near(b.acquire(1, 0), 0.2))

# ---- 3. 闲置累积突发额度，且被 maxPermits 钳住 ----------------------------
b = SmoothBursty()
b.set_rate(5.0, 0)
b.acquire(1, 0)                                  # next_free = 200000
b.resync(1_000_000)                              # 闲置到 1s
ck("闲置 0.8s ⇒ 累积 4 张（(1e6-2e5)/2e5）", near(b.stored_permits, 4.0))
ck("累积量同时被 resync 钳到 maxPermits 以下", b.stored_permits <= b.max_permits)
b.resync(10_000_000)                             # 再闲置 9s
ck("长时间闲置后 storedPermits 不超 maxPermits=5", near(b.stored_permits, 5.0))

# ---- 4. acquire(大数)：返回值取的是「旧的」nextFreeTicket ------------------
b = SmoothBursty()
b.set_rate(5.0, 0)
ck("首次 acquire(15) 返回 0（moment 取的是初始 nextFreeTicket=0）",
   b.acquire(15, 0) == 0.0)
ck("但 nextFreeTicket 已被推后 3e6us（源码注释：15 fresh permits 需要 3 秒）",
   b.next_free_ticket_micros == 3_000_000)
ck("紧接着再来一次 acquire(15) 要等 3.0s", near(b.acquire(15, 0), 3.0))

b = SmoothBursty()
b.set_rate(5.0, 0)
b.resync(1_000_000)                              # 累积到满桶 5 张
ck("有 5 张存储时 acquire(15) 的 moment 仍在 1e6 ⇒ 返回 0", b.acquire(15, 1_000_000) == 0.0)
ck("但只把 nextFreeTicket 推后 2.0s（借掉 5 张存储，只剩 10 张新令牌）",
   b.next_free_ticket_micros == 3_000_000)

# ---- 5. tryAcquire 不触发 resync ------------------------------------------
b = SmoothBursty()
b.set_rate(5.0, 0)
b.acquire(1, 0)                                  # next_free = 200000
ck("timeout=0 且 nextFreeTicket 在未来 ⇒ 拒", not b.try_acquire(1, 0, 0))
ck("timeout 足够大 ⇒ 收", b.try_acquire(1, 200_000, 0))
b2 = SmoothBursty()
b2.set_rate(5.0, 0)
b2.reserve_earliest_available(1, 0)
ck("闲置很久但没 resync 时 nextFreeTicket 仍停在 1e6 之前 ⇒ 判据用的是陈旧值",
   b2.next_free_ticket_micros == 200_000 and b2.can_acquire(0, 0) is False)

# ---- 6. SmoothWarmingUp 的三个派生量 --------------------------------------
w = SmoothWarmingUp(warmup_micros=1_000_000, cold_factor=3.0)
w.set_rate(10.0, 0)
ck("coldFactor 默认 3.0（RateLimiter.create 传入）", near(w.cold_factor, 3.0))
ck("stableInterval = 100000us", near(w.stable_interval_micros, 100_000.0, 1e-9))
ck("coldInterval = 3 * stableInterval = 300000us", near(w.cold_interval_micros, 300_000.0, 1e-9))
ck("thresholdPermits = 0.5*warmup/stable = 5.0", near(w.threshold_permits, 5.0))
ck("maxPermits = threshold + 2*warmup/(stable+cold) = 10.0", near(w.max_permits, 10.0))
ck("slope = (cold-stable)/(max-threshold) = 40000", near(w.slope, 40_000.0, 1e-9))
ck("coolDownInterval = warmup/maxPermits = 100000us",
   near(w.cool_down_interval_micros(), 100_000.0, 1e-9))

# ---- 7. 两条面积恒等式（源码注释里的推导） --------------------------------
ck("maxPermits→threshold 的梯形面积 = warmupPeriod = 1e6us",
   near(0.5 * (w.stable_interval_micros + w.cold_interval_micros)
        * (w.max_permits - w.threshold_permits), 1_000_000.0, 1e-6))
ck("threshold→0 的面积 = warmupPeriod/2 = 5e5us",
   near(w.threshold_permits * w.stable_interval_micros, 500_000.0, 1e-6))
ck("一次取光满桶 10 张耗时 1.5s（= warmup + warmup/2）",
   w.stored_permits_to_wait_time(10.0, 10.0) == 1_500_000)
ck("只取左半段 5 张耗时 0.5s", w.stored_permits_to_wait_time(5.0, 5.0) == 500_000)
ck("permitsToTime(0) = stableInterval", near(w.permits_to_time(0.0), 100_000.0, 1e-9))
ck("permitsToTime(5) = coldInterval", near(w.permits_to_time(5.0), 300_000.0, 1e-9))

# ---- 8. 冷初态：Bursty 与 WarmingUp 相反 ----------------------------------
wb = SmoothBursty()
wb.set_rate(2.0, 0)
ww = SmoothWarmingUp(warmup_micros=1_000_000)
ww.set_rate(2.0, 0)
ck("Bursty 初态 stored=0（没有突发额度）", wb.stored_permits == 0.0)
ck("WarmingUp 初态 stored=maxPermits（源码注释 initial state is cold）",
   near(ww.stored_permits, ww.max_permits))
b3 = SmoothBursty()
b3.max_permits = float("inf")
b3.do_set_rate(5.0, 1_000.0)
ck("Bursty 的 +inf 分支给满桶", near(b3.stored_permits, b3.max_permits))
w3 = SmoothWarmingUp(warmup_micros=1_000_000)
w3.max_permits = float("inf")
w3.do_set_rate(5.0, 1_000.0)
ck("WarmingUp 的 +inf 分支给 0", w3.stored_permits == 0.0)

# ---- 9. setRate 按比例缩放已存令牌 ----------------------------------------
b = SmoothBursty()
b.set_rate(5.0, 0)
b.resync(600_000)
before = b.stored_permits
b.set_rate(10.0, 600_000)
ck("setRate 后 storedPermits 按 maxPermits 比例缩放",
   near(b.stored_permits, before * 10.0 / 5.0, 1e-9))

# ---- 10. 饱和加法防溢出 ----------------------------------------------------
ck("saturatedAdd 钳到 Long.MAX_VALUE", saturated_add(LONG_MAX, 10 ** 6) == LONG_MAX)
ck("正常范围不钳", saturated_add(100, 200) == 300)
b = SmoothBursty()
b.set_rate(1.0, 0)
b.acquire(10 ** 14, 0)                           # 1e20 us > Long.MAX_VALUE
ck("超大 acquire 不会把 nextFreeTicket 溢出成负数", b.next_free_ticket_micros == LONG_MAX)

if FAIL:
    print("FAILED %d:" % len(FAIL))
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("guava_ratelimiter selfcheck OK: %d assertions" % len(OK))
