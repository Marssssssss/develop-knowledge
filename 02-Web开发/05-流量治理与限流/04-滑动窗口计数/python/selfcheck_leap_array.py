"""04-滑动窗口计数 自检。运行：python selfcheck_leap_array.py"""

import sys

from leap_array import FixedWindow, LeapArray, SlidingWindowLimiter

OK = []
FAIL = []


def ck(name, cond):
    (OK if cond else FAIL).append(name)


def rejected(fn):
    try:
        fn()
    except AssertionError:
        return True
    return False


# ---- 1. 构造期断言与派生量 ------------------------------------------------
la = LeapArray(sample_count=2, interval_ms=1000)
ck("windowLength = interval/sampleCount = 500", la.window_length == 500)
ck("sampleCount 必须为正", rejected(lambda: LeapArray(0, 1000)))
ck("intervalMs 必须为正", rejected(lambda: LeapArray(2, 0)))
ck("intervalMs 必须被 sampleCount 整除（AssertUtil 原文）",
   rejected(lambda: LeapArray(3, 1000)))

# ---- 2. 下标与窗口起点 ----------------------------------------------------
ck("idx(t=0)=0 idx(t=499)=0", la.calculate_time_idx(0) == 0 and la.calculate_time_idx(499) == 0)
ck("idx(t=500)=1 idx(t=999)=1",
   la.calculate_time_idx(500) == 1 and la.calculate_time_idx(999) == 1)
ck("idx(t=1000) 回绕到 0", la.calculate_time_idx(1000) == 0)
ck("windowStart 向下取整到 windowLength 的倍数",
   la.calculate_window_start(1234) == 1000 and la.calculate_window_start(1000) == 1000)

# ---- 3. currentWindow 的四条分支 ------------------------------------------
la = LeapArray(2, 1000)
w1 = la.current_window(10)
ck("分支 1：桶为空 → 新建", w1 is not None and w1.window_start == 0)
w2 = la.current_window(20)
ck("分支 2：windowStart 相同 → 复用同一对象", w2 is w1)
la.add(5, 30)
ck("同一窗口内累加", la.list_valid(30)[0].bucket.value == 5)

w3 = la.current_window(510)
ck("分支 3 之前：idx=1 首次访问走的是「新建」分支", w3 is not w1 and w3.window_start == 500)
la.add(4, 550)
w4 = la.current_window(1010)                  # idx=0 上已有 start=0 的旧桶
ck("分支 3：windowStart 更大 → 复用对象并 reset", w4 is w1 and w4.window_start == 1000)
ck("reset 后旧计数清零", la.array[0].bucket.value == 0)
ck("reset 只发生在环形回绕撞上旧桶时（第 1 次回绕）", la.reset_count == 1)

la2 = LeapArray(2, 1000)
la2.current_window(1500)                      # 在 idx=1 上建 start=1500 的桶
before = la2.array[1]
w_back = la2.current_window(700)              # 时间回拨：start=500 < 1500
ck("分支 4：时间回拨返回新桶", w_back is not before and w_back.window_start == 500)
ck("回拨产生的桶不写回数组（数组里仍是旧桶）", la2.array[1] is before)
ck("回拨取两次得到的是两个不同对象（每次都是临时桶）",
   la2.current_window(700) is not la2.current_window(700))
ck("负时间返回 None", la.current_window(-1) is None)

# ---- 4. 过期判据是严格大于 ------------------------------------------------
la = LeapArray(2, 1000)
la.current_window(500)
ck("time - start == intervalMs 时**不**过期（严格大于）", not la.is_window_deprecated(1500, la.array[1]))
ck("time - start == intervalMs + 1 时过期", la.is_window_deprecated(1501, la.array[1]))

# ---- 5. 持续流量下 n 个桶全部有效 -----------------------------------------
la = LeapArray(2, 1000)
for t in range(0, 1000, 50):
    la.add(1, t)
ck("t=999 时两个桶都未过期（最老的距现在 999ms < interval）", len(la.list_valid(999)) == 2)
ck("t=1001 时 start=0 的桶已过期（1001 > 1000），只剩 1 个", len(la.list_valid(1001)) == 1)
la.add(1, 1000)                               # 回绕并 reset idx=0 到 start=1000
ck("补一次 add 后重新凑齐 2 个有效桶", len(la.list_valid(1001)) == 2)
ck("覆盖跨度落在 [interval-windowLength, interval) 内",
   999 - min(w.window_start for w in la.list_valid(999)) < 1000)

# ---- 6. 与固定窗口的边界对比（核心结论） ----------------------------------
L, N = 100, 2
sw = SlidingWindowLimiter(limit=L, sample_count=N, interval_ms=1000)
fw = FixedWindow(interval_ms=1000)
passed_sw = passed_fw = 0
for _ in range(L):
    if sw.try_pass(900):
        passed_sw += 1
    fw.add(1, 900)
    if fw.values(900) <= L:
        passed_fw += 1
ck("t=900 打满后滑动窗口通过 %d 次" % passed_sw, passed_sw == L)
ck("固定窗口同期也通过 %d 次" % passed_fw, passed_fw == L)

ck("t=1001（跨窗口边界）滑动窗口拒绝（旧桶仍有效）", not sw.try_pass(1001))
fw.add(1, 1001)
ck("t=1001 固定窗口放行（新窗口计数归零）", fw.values(1001) == 1)
ck("滑动窗口 t=1500 仍拒绝（1500-500=1000 未过期）", not sw.try_pass(1500))
ck("滑动窗口 t=1501 起放行（1501-500=1001 > 1000 过期）", sw.try_pass(1501))

# ---- 7. 跨窗口突发上限：滑动窗口 <= 1.5x，固定窗口可到 2x -------------------
sw2 = SlidingWindowLimiter(limit=100, sample_count=2, interval_ms=1000)
n_start = sum(1 for _ in range(200) if sw2.try_pass(999))
n_after = sum(1 for _ in range(200) if sw2.try_pass(1001))
ck("固定窗口式双倍突发在滑动窗口下被压掉", n_start == 100 and n_after == 0)

sw3 = SlidingWindowLimiter(limit=100, sample_count=10, interval_ms=1000)
ck("sampleCount=10 时最细粒度：t=999 打满 100", sum(1 for _ in range(200) if sw3.try_pass(999)) == 100)
ck("t=1001 仍为 0（窗口粒度更细不改变结论）", sum(1 for _ in range(200) if sw3.try_pass(1001)) == 0)

# ---- 8. add 影响的是它自己所在窗口 ----------------------------------------
la = LeapArray(2, 1000)
la.add(3, 100)
la.add(7, 400)
ck("同一窗口两次 add 累加到同一桶", la.values(400) == 10)
la.add(1, 600)
ck("跨窗口后旧桶仍是 10，新桶是 1", la.values(600) == 11)
ck("getPreviousWindow 命中上一窗口", la.get_previous_window(600) is la.array[0])

if FAIL:
    print("FAILED %d:" % len(FAIL))
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("leap_array selfcheck OK: %d assertions" % len(OK))
