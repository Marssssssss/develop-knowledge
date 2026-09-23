"""HNSW 的原子构件：两个方向的堆、距离、层级生成、确定性随机源。

与 C++ 的语言差异显式落地：
  - C++ `(int)` 截断是**向零取整**；Python `int()` 同为向零。因 `-log(U)*mult` 恒为
    非负，两者一致。
  - C++ `std::priority_queue` 同距离时无稳定顺序；这里统一用 id 作次键保证可复现。
"""

import heapq
import math


class MaxHeapOnDist:
    """top_candidates 的等价物。

    官方 `CompareByFirst` 定义为 `a.first < b.first`，而 `std::priority_queue`
    把「按比较器最大」的元素放在 `top()`，因此这是一个**按距离的大顶堆**：
    `top()` 是已选集合里最远的那个；`size() > ef` 时 `pop()` 淘汰最远。
    """

    def __init__(self):
        self._h = []

    def push(self, dist, node):
        heapq.heappush(self._h, (-dist, node))

    def top(self):
        nd, node = self._h[0]
        return (-nd, node)

    def pop(self):
        nd, node = heapq.heappop(self._h)
        return (-nd, node)

    def size(self):
        return len(self._h)

    def empty(self):
        return not self._h

    def items(self):
        """按距离升序返回全部 (dist, node)。"""
        return sorted(((-nd, node) for nd, node in self._h))


class MinHeapOnDist:
    """candidateSet 的等价物。

    官方入堆是 `candidateSet.emplace(-dist, ep_id)` —— 存的是**负距离**，同一个
    大顶堆比较器作用在负值上就等于「按真实距离的小顶堆」，`top()` 最近。
    """

    def __init__(self):
        self._h = []

    def push(self, dist, node):
        heapq.heappush(self._h, (dist, node))

    def top(self):
        return self._h[0]

    def pop(self):
        return heapq.heappop(self._h)

    def size(self):
        return len(self._h)


def l2sqr(a, b):
    """`L2Sqr`（space_l2.h 的标量版）。注意返回的是**平方**距离，不开根。"""
    return sum((x - y) * (x - y) for x, y in zip(a, b))


def get_random_level(u, mult):
    """`getRandomLevel`: `(int)(-log(distribution(level_generator_)) * reverse_size)`。

    u 必须落在 (0, 1]；u → 0+ 时层级发散（C++ 的 uniform_real_distribution 取不到 0）。
    """
    if not (0.0 < u <= 1.0):
        raise ValueError("u must be in (0, 1]")
    return int(-math.log(u) * mult)


def lcg_uniforms(n, seed=20240923):
    """确定性 (0,1) 均匀序列 —— 替代 C++ 的 `std::default_random_engine`。

    hnswlib 用 `level_generator_.seed(random_seed)`，同一 seed 完全可复现；这里
    同样用定种子 LCG 钉住，避免「跑通只是运气」。
    """
    x = seed & 0xFFFFFFFF
    out = []
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        out.append((x % 1000000) / 1000000.0 + 1e-6)
    return out


def lcg_points(n, dim, seed=777):
    """确定性点云。"""
    x = seed & 0xFFFFFFFF
    pts = []
    for _ in range(n):
        v = []
        for _d in range(dim):
            x = (1103515245 * x + 12345) & 0x7FFFFFFF
            v.append((x % 100000) / 100000.0)
        pts.append(v)
    return pts


def brute_knn(q, vectors, k):
    """暴力基线，用于验证「ef 足够大时 HNSW 结果是精确的」。"""
    return sorted(((l2sqr(q, v), i) for i, v in enumerate(vectors)))[:k]
