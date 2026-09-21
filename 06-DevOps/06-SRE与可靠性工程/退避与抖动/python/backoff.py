"""四种退避策略 —— 逐行转写自 AWS 官方模拟器源码。

来源:`aws-samples/aws-arch-backoff-simulator` 的 `src/backoff_simulator.py`
(该仓库是 AWS Architecture 博客《Exponential Backoff And Jitter》的配套代码)。
源码原文:

    class Backoff:
        def __init__(self, base, cap):
            self.base = base
            self.cap = cap
        def expo(self, n):
            return min(self.cap, pow(2, n)*self.base)

    class NoBackoff(Backoff):            def backoff(self, n): return 0
    class ExpoBackoff(Backoff):          def backoff(self, n): return self.expo(n)
    class ExpoBackoffEqualJitter:        v = self.expo(n); return v/2 + random.uniform(0, v/2)
    class ExpoBackoffFullJitter:         v = self.expo(n); return random.uniform(0, v)
    class ExpoBackoffDecorr:
        def __init__(self, base, cap):   self.sleep = self.base
        def backoff(self, n):
            self.sleep = min(self.cap, random.uniform(self.base, self.sleep * 3))
            return self.sleep

博客与源码里客户端的构造参数是 `backoff_cls(5, 2000)`,即 base=5ms、cap=2000ms;
网络延迟模型是 `Net(mean=10, sd=2)`(博客正文写 "mean of 10ms and variance of 4ms")。

语言差异显式落地:
- 源码用 Python 2 的全局 `random`;这里改成**可注入的 rng**,以便自检可复现。
- 源码 `xrange` 在 Python 3 下会 NameError,与算法无关,不转写。
- **Decorr 的 `n` 是摆设**:它只用 `self.sleep`,是有状态的。这是四种策略里
  唯一"与尝试次数无关"的一个,也是最容易被误读的一个。
"""

import random as _random


class Backoff(object):
    """基类:`expo(n) = min(cap, 2**n * base)`。"""

    def __init__(self, base, cap):
        self.base = base
        self.cap = cap

    def expo(self, n):
        return min(self.cap, pow(2, n) * self.base)

    def backoff(self, n):
        raise NotImplementedError


class NoBackoff(Backoff):
    def backoff(self, n):
        return 0.0


class ExpoBackoff(Backoff):
    def __init__(self, base, cap, rng=None):
        Backoff.__init__(self, base, cap)
        self.rng = rng or _random

    def backoff(self, n):
        return float(self.expo(n))


class ExpoBackoffEqualJitter(Backoff):
    def __init__(self, base, cap, rng=None):
        Backoff.__init__(self, base, cap)
        self.rng = rng or _random

    def backoff(self, n):
        v = self.expo(n)
        return v / 2 + self.rng.uniform(0, v / 2)


class ExpoBackoffFullJitter(Backoff):
    def __init__(self, base, cap, rng=None):
        Backoff.__init__(self, base, cap)
        self.rng = rng or _random

    def backoff(self, n):
        v = self.expo(n)
        return self.rng.uniform(0, v)


class ExpoBackoffDecorr(Backoff):
    """Decorrelated Jitter:**有状态**,下界恒为 base,上界是上一次睡眠的 3 倍。

    因为下界不随 sleep 增长,sleep 可以突然掉回 base 附近 —— 它不是单调的。
    """

    def __init__(self, base, cap, rng=None):
        Backoff.__init__(self, base, cap)
        self.rng = rng or _random
        self.sleep = self.base

    def backoff(self, n):
        self.sleep = min(self.cap, self.rng.uniform(self.base, self.sleep * 3))
        return self.sleep


def make(name, base=5.0, cap=2000.0, rng=None):
    """按名字构造策略。"""
    table = {
        "none": NoBackoff,
        "expo": ExpoBackoff,
        "equal": ExpoBackoffEqualJitter,
        "full": ExpoBackoffFullJitter,
        "decorr": ExpoBackoffDecorr,
    }
    if name not in table:
        raise ValueError("unknown backoff %r" % name)
    cls = table[name]
    if cls is NoBackoff:
        return cls(base, cap)
    return cls(base, cap, rng)


ALL = ("none", "expo", "equal", "full", "decorr")
