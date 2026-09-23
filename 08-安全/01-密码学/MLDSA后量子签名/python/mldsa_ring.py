"""ML-DSA 环运算：Montgomery 归约、NTT、高低位分解与 hint。

逐行对应 pq-crystals/dilithium `ref/reduce.c` / `ref/ntt.c` / `ref/rounding.c`。
C 的 int32_t 回绕在这里用 `_i32` 显式折回有符号 32 位——Python 整数不回绕，
不做这一步「取低 32 位」的分支永远不会触发（09-21 槽在 Go deadline 上踩过同类坑）。
"""

from mldsa_params import Q, D, N, QINV, F_INVNTT, ZETAS

QHALF = (Q - 1) // 2


def _i32(x):
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x >= (1 << 31) else x


def montgomery_reduce(a):
    """ref/reduce.c:17 —— a*2^-32 mod Q，结果落在 (-Q, Q)。"""
    t = _i32(_i32(a) * QINV)
    return (a - t * Q) >> 32


def reduce32(a):
    """ref/reduce.c:37 —— 折回 [-6283008, 6283008]。"""
    t = (a + (1 << 22)) >> 23
    return a - t * Q


def caddq(a):
    """ref/reduce.c:59 —— 负数加 Q。"""
    return a + ((a >> 31) & Q)


def freeze(a):
    """标准代表元 a mod^+ Q。"""
    return caddq(reduce32(a))


# ------------------------------------------------------------------ NTT
def ntt(a):
    """ref/ntt.c:47 —— 前向 NTT，输出 bit-reversed 序；加减后不做归约。"""
    a = list(a)
    k = 0
    length = 128
    while length > 0:
        start = 0
        while start < N:
            k += 1
            zeta = ZETAS[k]
            for j in range(start, start + length):
                t = montgomery_reduce(zeta * a[j + length])
                a[j + length] = a[j] - t
                a[j] = a[j] + t
            start += 2 * length
        length >>= 1
    return a


def invntt_tomont(a):
    """ref/ntt.c:80 —— 逆 NTT，并乘上 Montgomery 因子 2^32。"""
    a = list(a)
    k = 256
    length = 1
    while length < N:
        start = 0
        while start < N:
            k -= 1
            zeta = -ZETAS[k]
            for j in range(start, start + length):
                t = a[j]
                a[j] = t + a[j + length]
                a[j + length] = t - a[j + length]
                a[j + length] = montgomery_reduce(zeta * a[j + length])
            start += 2 * length
        length <<= 1
    for j in range(N):
        a[j] = montgomery_reduce(F_INVNTT * a[j])
    return a


def pointwise_montgomery(a, b):
    """NTT 域逐点乘（ref/poly.c pointwise_montgomery）。"""
    return [montgomery_reduce(x * y) for x, y in zip(a, b)]


# ------------------------------------------- 高低位分解 / hint（FIPS 204 §7.4）
def power2round(a, d=D):
    """ref/rounding.c:17 —— a = a1*2^D + a0，且 -2^(D-1) < a0 <= 2^(D-1)。"""
    a1 = (a + (1 << (d - 1)) - 1) >> d
    return a1, a - (a1 << d)


def decompose(a, gamma2):
    """ref/rounding.c:39 —— a = a1*(2*gamma2) + a0 (mod Q)。"""
    a1 = (a + 127) >> 7
    if gamma2 == (Q - 1) // 32:
        a1 = (a1 * 1025 + (1 << 21)) >> 22
        a1 &= 15
    else:
        a1 = (a1 * 11275 + (1 << 23)) >> 24
        # C: a1 ^= ((43 - a1) >> 31) & a1 —— a1 > 43 时把 a1 清成 0
        a1 ^= ((43 - a1) >> 31) & a1
    a0 = a - a1 * 2 * gamma2
    a0 -= (((Q - 1) // 2 - a0) >> 31) & Q
    return a1, a0


def high_bits(a, gamma2):
    """FIPS 204 Algorithm 37。"""
    return decompose(a, gamma2)[0]


def low_bits(a, gamma2):
    """FIPS 204 Algorithm 38。"""
    return decompose(a, gamma2)[1]


def make_hint(a0, a1, gamma2):
    """ref/rounding.c:67（FIPS 204 Algorithm 39）。"""
    if a0 > gamma2 or a0 < -gamma2 or (a0 == -gamma2 and a1 != 0):
        return 1
    return 0


def use_hint(a, hint, gamma2):
    """ref/rounding.c:84（FIPS 204 Algorithm 40）。"""
    a1, a0 = decompose(a, gamma2)
    if hint == 0:
        return a1
    if gamma2 == (Q - 1) // 32:
        return (a1 + 1) & 15 if a0 > 0 else (a1 - 1) & 15
    if a0 > 0:
        return 0 if a1 == 43 else a1 + 1
    return 43 if a1 == 0 else a1 - 1


# ------------------------------------------------------------ 范数检查
def chknorm(a, bound):
    """ref/poly.c poly_chknorm —— 有任意系数 |c| >= bound 即返回 True。

    绝对值用的是 `t = c>>31; t = c - (t & 2*c)`，Python 的算术右移给出同样结果。
    """
    if bound > (Q - 1) // 8:
        return True
    for c in a:
        s = c >> 31
        t = c - (s & 2 * c)
        if t >= bound:
            return True
    return False
