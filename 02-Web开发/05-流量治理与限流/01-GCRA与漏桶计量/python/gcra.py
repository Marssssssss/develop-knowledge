"""GCRA 与漏桶计量。

权威依据：
- ITU-T I.371《Traffic control and congestion control in B-ISDN》给出的两种等价描述
  （虚拟调度 virtual scheduling / 连续状态漏桶 continuous-state leaky bucket），
  原文见 Wikipedia《Generic cell rate algorithm》对该建议书的引述。
- Brandur Leach《Rate Limiting, Cells, and GCRA》(brandur.org/rate-limiting)。

三种计量器在本模块中并列实现，便于逐事件对拍：
- FixedWindow：时间桶（按窗口网格对齐）。
- Gcra：虚拟调度形态，状态只有一个标量 TAT。
- ContinuousLeakyBucket：连续状态漏桶，状态是 (X, LCT)。
"""

from __future__ import annotations


class FixedWindow:
    """时间桶（time bucketed）：窗口网格对齐，计数在窗口边界清零。"""

    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self.window_start = None
        self.count = 0

    def arrive(self, ta: float, cost: int = 1) -> bool:
        if self.window_start is None or ta - self.window_start >= self.window:
            self.window_start = (ta // self.window) * self.window
            self.count = 0
        if self.count + cost <= self.limit:
            self.count += cost
            return True
        return False


class Gcra:
    """虚拟调度形态的 GCRA。

    T    = 1/rate，发射间隔（秒）。
    tau  = 容限（秒），即「最多可以比理论到达时间提前多久」。

    buffer_mode:
      "itu"     —— ITU-T I.371 / Wikipedia 口径：next_allowed = TAT - tau。
      "brandur" —— Brandur 文章口径：扣减 (tau + T)，等于多发一张令牌。
    """

    def __init__(self, rate: float, tau: float, strict: bool = False,
                 buffer_mode: str = "itu") -> None:
        assert rate > 0, "rate 必须为正"
        assert tau >= 0, "容限不能为负"
        self.T = 1.0 / rate
        self.tau = tau
        self.strict = strict          # True 用规范原文的 ta > next_allowed
        self.buffer_mode = buffer_mode
        self.tat = None               # 理论到达时间；None 表示尚未收到任何请求

    @property
    def buffer(self) -> float:
        return self.tau if self.buffer_mode == "itu" else self.tau + self.T

    def next_allowed(self):
        if self.tat is None:
            return None
        return self.tat - self.buffer

    def arrive(self, ta: float, cost: int = 1):
        """一次到达。返回 (conforming, retry_after)。非一致时状态不变。"""
        if self.tat is None:
            self.tat = ta + cost * self.T
            return True, 0.0
        na = self.next_allowed()
        ok = (ta > na) if self.strict else (ta >= na)
        if not ok:
            return False, na - ta
        self.tat = max(self.tat, ta) + cost * self.T
        return True, 0.0

    def burst_capacity(self) -> int:
        """同一瞬间最多能连续通过多少次（cost=1）。"""
        g = Gcra(1.0 / self.T, self.tau, self.strict, self.buffer_mode)
        n = 0
        while True:
            ok, _ = g.arrive(0.0)
            if not ok:
                return n
            n += 1


class ContinuousLeakyBucket:
    """连续状态漏桶（ITU-T I.371）：桶内以 1 单位/单位时间漏水，一致信元加 T。

    桶容量上界是 (T + tau)；判据是 X' <= tau。
    """

    def __init__(self, rate: float, tau_limit: float) -> None:
        assert rate > 0
        assert tau_limit >= 0
        self.T = 1.0 / rate
        self.tau = tau_limit
        self.X = 0.0       # 桶内水量（单位是时间）
        self.lct = None    # 上一次一致信元的到达时刻

    def arrive(self, ta: float, cost: int = 1):
        if self.lct is None:
            self.lct = ta
            self.X = cost * self.T
            return True, 0.0
        xp = self.X - (ta - self.lct)   # 漏水
        if xp < 0.0:
            xp = 0.0
        if xp > self.tau:
            return False, xp - self.tau  # 漏水速率为 1，故差值即还需等待的时间
        self.lct = ta
        self.X = xp + cost * self.T
        return True, 0.0


def measure_throughput(limiter, rate: float, horizon: float = 10.0) -> int:
    """以远快于限额的速度持续冲击，统计 horizon 秒内通过的次数。"""
    n = 0
    t = 0.0
    step = 1.0 / (rate * 1000.0) if rate > 0 else 0.001
    while t < horizon:
        ok, _ = limiter.arrive(t)
        if ok:
            n += 1
        t += step
    return n
