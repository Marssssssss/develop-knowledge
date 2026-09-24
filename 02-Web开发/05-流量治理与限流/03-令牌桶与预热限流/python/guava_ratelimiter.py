"""Guava RateLimiter：SmoothBursty 与 SmoothWarmingUp 的逐行转写。

权威依据：google/guava@master
- guava/src/com/google/common/util/concurrent/RateLimiter.java（22178 B）
- guava/src/com/google/common/util/concurrent/SmoothRateLimiter.java（19744 B）

时间统一用**微秒整数**（与源码一致），permits 用浮点。
"""

from __future__ import annotations

MICROS_PER_SECOND = 1_000_000
LONG_MAX = 2 ** 63 - 1


def saturated_add(a: int, b: int) -> int:
    """LongMath.saturatedAdd 的等价实现：溢出时钳到 Long.MAX_VALUE 而不是回绕。"""
    s = a + b
    if s > LONG_MAX:
        return LONG_MAX
    return s


class SmoothRateLimiter:
    """SmoothRateLimiter 抽象基类（resync / reserveEarliestAvailable 两处公共逻辑）。"""

    def __init__(self) -> None:
        self.stored_permits = 0.0
        self.max_permits = 0.0
        self.stable_interval_micros = 0.0
        self.next_free_ticket_micros = 0

    # --- 子类钩子 -------------------------------------------------------
    def do_set_rate(self, permits_per_second: float, stable_interval_micros: float) -> None:
        raise NotImplementedError

    def cool_down_interval_micros(self) -> float:
        raise NotImplementedError

    def stored_permits_to_wait_time(self, stored_permits: float, permits_to_take: float) -> int:
        raise NotImplementedError

    # --- 公共逻辑 -------------------------------------------------------
    def set_rate(self, permits_per_second: float, now_micros: int = 0) -> None:
        self.resync(now_micros)
        self.stable_interval_micros = MICROS_PER_SECOND / permits_per_second
        self.do_set_rate(permits_per_second, self.stable_interval_micros)

    def resync(self, now_micros: int) -> None:
        if now_micros > self.next_free_ticket_micros:
            new_permits = ((now_micros - self.next_free_ticket_micros)
                           / self.cool_down_interval_micros())
            self.stored_permits = min(self.max_permits,
                                      self.stored_permits + new_permits)
            self.next_free_ticket_micros = now_micros

    def reserve_earliest_available(self, required_permits: int, now_micros: int) -> int:
        self.resync(now_micros)
        moment_available = self.next_free_ticket_micros
        stored_to_spend = min(required_permits, self.stored_permits)
        fresh_permits = required_permits - stored_to_spend
        wait_micros = (self.stored_permits_to_wait_time(self.stored_permits, stored_to_spend)
                       + int(fresh_permits * self.stable_interval_micros))
        self.next_free_ticket_micros = saturated_add(self.next_free_ticket_micros, wait_micros)
        self.stored_permits -= stored_to_spend
        return moment_available

    def acquire(self, permits: int, now_micros: int) -> float:
        """返回调用方需要睡眠的秒数（从 now_micros 起算，永不为负）。"""
        moment = self.reserve_earliest_available(permits, now_micros)
        return max(moment - now_micros, 0) / MICROS_PER_SECOND

    def can_acquire(self, now_micros: int, timeout_micros: int) -> bool:
        """注意：queryEarliestAvailable **不触发 resync**，用的是上一次 reserve 留下的值。"""
        return self.next_free_ticket_micros - timeout_micros <= now_micros

    def try_acquire(self, permits: int, timeout_micros: int, now_micros: int) -> bool:
        timeout_micros = max(timeout_micros, 0)
        if not self.can_acquire(now_micros, timeout_micros):
            return False
        self.reserve_earliest_available(permits, now_micros)
        return True


class SmoothBursty(SmoothRateLimiter):
    """突发型：storedPermitsToWaitTime 恒为 0（存储令牌免费），冷却间隔 = 稳定间隔。"""

    def __init__(self, max_burst_seconds: float = 1.0) -> None:
        super().__init__()
        self.max_burst_seconds = max_burst_seconds

    def do_set_rate(self, permits_per_second: float, stable_interval_micros: float) -> None:
        old_max = self.max_permits
        self.max_permits = self.max_burst_seconds * permits_per_second
        if old_max == float("inf"):
            self.stored_permits = self.max_permits
        else:
            # 初态（oldMaxPermits == 0.0）：注释就写 initial state
            self.stored_permits = (0.0 if old_max == 0.0
                                   else self.stored_permits * self.max_permits / old_max)

    def cool_down_interval_micros(self) -> float:
        return self.stable_interval_micros

    def stored_permits_to_wait_time(self, stored_permits: float, permits_to_take: float) -> int:
        return 0


class SmoothWarmingUp(SmoothRateLimiter):
    """预热型：梯形积分 + 冷因子（默认 3.0）。"""

    def __init__(self, warmup_micros: int, cold_factor: float = 3.0) -> None:
        super().__init__()
        self.warmup_period_micros = warmup_micros
        self.cold_factor = cold_factor
        self.slope = 0.0
        self.threshold_permits = 0.0

    @property
    def cold_interval_micros(self) -> float:
        return self.stable_interval_micros * self.cold_factor

    def do_set_rate(self, permits_per_second: float, stable_interval_micros: float) -> None:
        old_max = self.max_permits
        cold = stable_interval_micros * self.cold_factor
        self.threshold_permits = 0.5 * self.warmup_period_micros / stable_interval_micros
        self.max_permits = (self.threshold_permits
                            + 2.0 * self.warmup_period_micros / (stable_interval_micros + cold))
        self.slope = (cold - stable_interval_micros) / (self.max_permits - self.threshold_permits)
        if old_max == float("inf"):
            self.stored_permits = 0.0
        else:
            # 初态：满桶 —— 源码注释即 "initial state is cold"
            self.stored_permits = (self.max_permits if old_max == 0.0
                                   else self.stored_permits * self.max_permits / old_max)

    def cool_down_interval_micros(self) -> float:
        return self.warmup_period_micros / self.max_permits

    def permits_to_time(self, permits: float) -> float:
        return self.stable_interval_micros + permits * self.slope

    def stored_permits_to_wait_time(self, stored_permits: float, permits_to_take: float) -> int:
        above = stored_permits - self.threshold_permits
        micros = 0
        if above > 0.0:                       # 右半段：梯形（climbing line）
            take_above = min(above, permits_to_take)
            length = (self.permits_to_time(above)
                      + self.permits_to_time(above - take_above))
            micros = int(take_above * length / 2.0)
            permits_to_take -= take_above
        micros += int(self.stable_interval_micros * permits_to_take)   # 左半段：水平线
        return micros
