"""Sentinel LeapArray 滑动窗口计数的逐行转写。

权威依据：alibaba/Sentinel@master
- sentinel-core/src/main/java/com/alibaba/csp/sentinel/slots/statistic/base/LeapArray.java
- sentinel-core/.../base/WindowWrap.java

LeapArray 用一个定长环形数组 + 时间桶覆盖 `intervalInMs` 的统计区间：
    sampleCount = intervalInMs / windowLengthInMs
"""

from __future__ import annotations


class MetricBucket:
    """简化版 MetricBucket：只保留一个计数。"""

    def __init__(self) -> None:
        self.value = 0

    def add(self, n: int = 1) -> None:
        self.value += n

    def reset(self) -> None:
        self.value = 0


class WindowWrap:
    """单个时间桶：记录窗口长度、窗口起点与桶内容。"""

    def __init__(self, window_length: int, window_start: int, bucket: MetricBucket) -> None:
        self.window_length = window_length
        self.window_start = window_start
        self.bucket = bucket

    def reset_to(self, start: int) -> None:
        self.window_start = start
        self.bucket.reset()


class LeapArray:
    """滑动窗口环形数组。"""

    def __init__(self, sample_count: int, interval_ms: int) -> None:
        assert sample_count > 0, "bucket count is invalid"
        assert interval_ms > 0, "total time interval should be positive"
        assert interval_ms % sample_count == 0, "time span needs to be evenly divided"
        self.sample_count = sample_count
        self.interval_ms = interval_ms
        self.window_length = interval_ms // sample_count
        self.array = [None] * sample_count
        self.reset_count = 0      # 记录复用（reset）了多少次，便于观察环形回绕

    # --- 下标与起点 -------------------------------------------------------
    def calculate_time_idx(self, time_ms: int) -> int:
        return (time_ms // self.window_length) % len(self.array)

    def calculate_window_start(self, time_ms: int) -> int:
        return time_ms - time_ms % self.window_length

    # --- 当前窗口（四分支） -----------------------------------------------
    def current_window(self, time_ms: int):
        if time_ms < 0:
            return None
        idx = self.calculate_time_idx(time_ms)
        window_start = self.calculate_window_start(time_ms)
        old = self.array[idx]
        if old is None:
            w = WindowWrap(self.window_length, window_start, MetricBucket())
            self.array[idx] = w                                   # CAS 成功
            return w
        if window_start == old.window_start:
            return old
        if window_start > old.window_start:
            old.reset_to(window_start)                            # 复用对象
            self.reset_count += 1
            return old
        # windowStart < old.windowStart：时间回拨，返回新桶但**不写回数组**
        return WindowWrap(self.window_length, window_start, MetricBucket())

    def get_previous_window(self, time_ms: int):
        idx = self.calculate_time_idx(time_ms - self.window_length)
        prev_start = time_ms - self.window_length
        wrap = self.array[idx]
        if wrap is None or self.is_window_deprecated(time_ms, wrap):
            return None
        if wrap.window_start + self.window_length < prev_start:
            return None
        return wrap

    # --- 过期判定 ---------------------------------------------------------
    def is_window_deprecated(self, time_ms: int, wrap: WindowWrap) -> bool:
        return time_ms - wrap.window_start > self.interval_ms      # 严格大于

    def list_valid(self, time_ms: int):
        return [w for w in self.array
                if w is not None and not self.is_window_deprecated(time_ms, w)]

    def values(self, time_ms: int) -> int:
        return sum(w.bucket.value for w in self.list_valid(time_ms))

    def add(self, n: int, time_ms: int) -> None:
        self.current_window(time_ms).bucket.add(n)


class FixedWindow:
    """对照用的固定窗口计数器（窗口网格对齐）。"""

    def __init__(self, interval_ms: int) -> None:
        self.interval_ms = interval_ms
        self.start = None
        self.value = 0

    def add(self, n: int, time_ms: int) -> None:
        if self.start is None or time_ms - self.start >= self.interval_ms:
            self.start = (time_ms // self.interval_ms) * self.interval_ms
            self.value = 0
        self.value += n

    def values(self, time_ms: int) -> int:
        if self.start is None or time_ms - self.start >= self.interval_ms:
            return 0
        return self.value


class SlidingWindowLimiter:
    """基于 LeapArray 的 QPS 限流器：values() 未达阈值即放行。"""

    def __init__(self, limit: int, sample_count: int, interval_ms: int = 1000) -> None:
        self.limit = limit
        self.array = LeapArray(sample_count, interval_ms)

    def try_pass(self, time_ms: int, n: int = 1) -> bool:
        if self.array.values(time_ms) + n <= self.limit:
            self.array.add(n, time_ms)
            return True
        return False
