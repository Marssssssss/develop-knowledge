"""向量度量空间与归一化 —— 按 hnswlib / faiss 官方定义转写。

依据（本轮实读）：
  - hnswlib `space_l2.h`：`L2Sqr` 返回的是**平方**距离，不开根
  - hnswlib `space_ip.h`：`InnerProductDistance = 1.0f - InnerProduct(...)`；
    hnswlib **没有 cosine 空间**，要用余弦就得自己先把向量归一化再用 ip
  - hnswlib `space_ip.h::InnerProductSpace(size_t dim)`：距离函数按 dim 的整除性派发
  - faiss `MetricType.h`：`METRIC_INNER_PRODUCT` 是 maximum inner product search，
    `METRIC_L2` 是 **squared** L2 search

语言差异显式落地：
  - C++ 的 `1.0f` 是 float32；Python 是 float64。这里不模拟 float32 精度，
    SIMD 求和顺序的差异用 1e-12 级别的容差对拍。
"""

import math


def l2sqr(a, b):
    """hnswlib `L2Sqr`：Σ (a_i - b_i)²，**不开根**。

    刻意用**朴素循环累加**而不是内置 `sum()` —— Python 3.12+ 的 `sum()` 对 float
    走 Neumaier 补偿求和，与 C++ 的朴素累加不是一回事（见自检第 5 节）。
    """
    total = 0.0
    for x, y in zip(a, b):
        t = x - y
        total += t * t
    return total


def l2(a, b):
    """真正的欧氏距离（开根）。仅供对照，hnswlib 不用它。"""
    return math.sqrt(l2sqr(a, b))


def inner_product(a, b):
    """hnswlib `InnerProduct`：朴素累加，对应 C++ 的 `for` 循环。"""
    total = 0.0
    for x, y in zip(a, b):
        total += x * y
    return total


def inner_product_distance(a, b):
    """hnswlib `InnerProductDistance`：`1.0f - InnerProduct(a, b)`。

    注意这里**没有**做归一化 —— 它不是余弦距离。且当 `<a,b> > 1` 时结果为负。
    """
    return 1.0 - inner_product(a, b)


def norm(v):
    return math.sqrt(sum(x * x for x in v))


def normalize(v):
    """L2 归一化。零向量会抛错。"""
    n = norm(v)
    if n == 0.0:
        raise ValueError("cannot normalize the zero vector")
    return [x / n for x in v]


def cosine(a, b):
    """余弦相似度。要求两条向量非零。"""
    na, nb = norm(a), norm(b)
    if na == 0.0 or nb == 0.0:
        raise ValueError("cosine undefined for zero vector")
    return inner_product(a, b) / (na * nb)


# ------------------------------------------------------------ SIMD 派发
def simd_route(dim):
    """复刻 `InnerProductSpace(size_t dim)` 的派发顺序。

    ```cpp
    if (dim % 16 == 0)      fstdistfunc_ = InnerProductDistanceSIMD16Ext;
    else if (dim % 4 == 0)  fstdistfunc_ = InnerProductDistanceSIMD4Ext;
    else if (dim > 16)      fstdistfunc_ = InnerProductDistanceSIMD16ExtResiduals;
    else if (dim > 4)       fstdistfunc_ = InnerProductDistanceSIMD4ExtResiduals;
    ```
    标量版在最后一个 else（dim <= 4 或没有 SIMD 宏）时生效。
    """
    if dim % 16 == 0:
        return "SIMD16Ext"
    if dim % 4 == 0:
        return "SIMD4Ext"
    if dim > 16:
        return "SIMD16ExtResiduals"
    if dim > 4:
        return "SIMD4ExtResiduals"
    return "scalar"


def inner_product_as_simd(a, b, route):
    """按不同分块宽度求和 —— 演示浮点加法不满足结合律。

    SIMD16Ext 一次处理 16 个 float 后横向归约；标量版逐个累加。两者在数学上
    相等，在浮点上可能差最后几位。
    """
    if route == "SIMD16Ext":
        return _blocked_sum(a, b, 16)
    if route == "SIMD4Ext":
        return _blocked_sum(a, b, 4)
    if route == "SIMD16ExtResiduals":
        return _residual_sum(a, b, 16)
    if route == "SIMD4ExtResiduals":
        return _residual_sum(a, b, 4)
    return inner_product(a, b)


def _blocked_sum(a, b, width):
    total = 0.0
    for base in range(0, len(a), width):
        part = 0.0
        for i in range(base, min(base + width, len(a))):
            part += a[i] * b[i]
        total += part
    return total


def naive_accumulate(values):
    """C++ 标量版的朴素累加（对照 Python 3.12+ 的补偿 `sum()`）。"""
    total = 0.0
    for v in values:
        total += v
    return total


def _residual_sum(a, b, width):
    """先用 width 宽度吃到长度的整数倍，剩下的残差用标量补齐。"""
    blocks = (len(a) // width) * width
    total = _blocked_sum(a[:blocks], b[:blocks], width)
    for i in range(blocks, len(a)):
        total += a[i] * b[i]
    return total


# ------------------------------------------------------------ 单位向量恒等式
def l2sqr_of_unit(a, b):
    """单位向量：`‖a−b‖² = ‖a‖² + ‖b‖² − 2⟨a,b⟩ = 2 − 2·cos`。"""
    return l2sqr(a, b)


def from_cosine(a, b):
    """用余弦反推出 L2 平方距离（仅在两条向量都归一化时成立）。"""
    return 2.0 - 2.0 * cosine(a, b)


# ------------------------------------------------------------ 排序
def rank_by_l2(q, vectors):
    return [i for _, i in sorted((l2sqr(q, v), i) for i, v in enumerate(vectors))]


def rank_by_ip(q, vectors):
    """faiss `METRIC_INNER_PRODUCT`：越大越近。"""
    return [i for _, i in sorted(((-inner_product(q, v), i))
                                 for i, v in enumerate(vectors))]


def rank_by_cosine(q, vectors):
    return [i for _, i in sorted(((-cosine(q, v), i))
                                 for i, v in enumerate(vectors))]


def lcg_points(n, dim, seed=777):
    x = seed & 0x7FFFFFFF
    pts = []
    for _ in range(n):
        v = []
        for _d in range(dim):
            x = (1103515245 * x + 12345) & 0x7FFFFFFF
            v.append((x % 100000) / 100000.0)
        pts.append(v)
    return pts
