"""确定性随机源(LCG,Numerical Recipes),只为可复现地造合成序列。"""

import math


class Rng:
    def __init__(self, seed):
        self.s = seed & 0xFFFFFFFF

    def u32(self):
        self.s = (1664525 * self.s + 1013904223) & 0xFFFFFFFF
        return self.s

    def uniform(self):
        return self.u32() / 4294967296.0

    def normal(self):
        u1 = max(self.uniform(), 1e-300)
        u2 = self.uniform()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def randn(n, rng):
    return [rng.normal() for _ in range(n)]
