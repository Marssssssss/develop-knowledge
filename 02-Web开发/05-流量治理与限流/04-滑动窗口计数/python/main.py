"""演示入口：固定窗口 vs 滑动窗口在窗口边界上的放行差异。"""

from leap_array import FixedWindow, LeapArray, SlidingWindowLimiter

LIMIT = 100


def main():
    print("== LeapArray(sampleCount=2, interval=1000ms) 的环形复用 ==")
    la = LeapArray(2, 1000)
    for t in (0, 250, 500, 750, 1000, 1250):
        w = la.current_window(t)
        print("  t=%4d → idx=%d windowStart=%4d  (array=[%s])"
              % (t, la.calculate_time_idx(t), w.window_start,
                 ", ".join("-" if b is None else str(b.window_start) for b in la.array)))

    print("\n== 边界突发：%d 次/秒 限额，先在 t=900 打满 ==" % LIMIT)
    for sample in (1, 2, 10):
        sw = SlidingWindowLimiter(limit=LIMIT, sample_count=sample, interval_ms=1000)
        first = sum(1 for _ in range(LIMIT + 20) if sw.try_pass(900))
        second = sum(1 for _ in range(LIMIT + 20) if sw.try_pass(1001))
        print("  sampleCount=%2d → t=900 放行 %3d，t=1001 放行 %3d" % (sample, first, second))

    fw = FixedWindow(interval_ms=1000)
    for _ in range(LIMIT):
        fw.add(1, 900)
    ok_after = 0
    for _ in range(LIMIT + 20):
        if fw.values(1001) < LIMIT:
            fw.add(1, 1001)
            ok_after += 1
    print("  固定窗口   → t=900 放行 %3d，t=1001 放行 %3d（2 毫秒内共 %d 次）"
          % (LIMIT, ok_after, LIMIT + ok_after))

    print("\n== 过期判据是严格大于 ==")
    la = LeapArray(2, 1000)
    la.current_window(500)
    for t in (1500, 1501):
        print("  t=%d，start=500 的桶 %s（time-start=%d %s interval=1000）"
              % (t, "过期" if la.is_window_deprecated(t, la.array[1]) else "未过期",
                 t - 500, ">" if t - 500 > 1000 else "<="))


if __name__ == "__main__":
    main()
