"""OpenSSL 风格常量时间原语的 Python 复刻（include/internal/constant_time.h）。

核心思想只有一句：**不要用 if 选值，用掩码选值**。
掩码取 0 或全 1，于是 `select(m, a, b) = (m & a) | (~m & b)`。
Go 的 crypto/subtle 提供的是同一套（ConstantTimeSelect / ConstantTimeByteEq /
ConstantTimeLessOrEq），只是把 v 约定成 0/1 而不是 0/全 1。
"""

MASK32 = 0xFFFFFFFF
MASK64 = 0xFFFFFFFFFFFFFFFF


class Barrier(int):
    """模拟 OpenSSL 的 `value_barrier()`。

    真实实现是内联汇编（或 volatile），作用是**阻止编译器收窄掩码的取值范围**：
    一旦编译器能证明 mask ∈ {0, 全 1}，它就有权把 `(mask & a) | (~mask & b)`
    优化回一个条件跳转 —— 那样"恒定时间"就白写了。
    本 demo 用类型来标这一层：带 Barrier 的掩码不允许被折叠（见 leak.py 的 Compiler）。
    """


def value_barrier32(a):
    return Barrier(a & MASK32)


def msb32(a):
    """0 - (a >> 31)：把最高位复制成全 1 或全 0。"""
    return (0 - ((a & MASK32) >> 31)) & MASK32


def msb64(a):
    return (0 - ((a & MASK64) >> 63)) & MASK64


def is_zero32(a):
    """constant_time_is_zero：`msb(~a & (a - 1))`。a-1 只有在 a=0 时回绕成全 1。"""
    a &= MASK32
    return msb32((~a & MASK32) & ((a - 1) & MASK32))


def eq32(a, b):
    """constant_time_eq：就是 `is_zero(a ^ b)`。"""
    return is_zero32((a ^ b) & MASK32)


def lt32(a, b):
    """constant_time_lt：`msb(a ^ ((a ^ b) | ((a - b) ^ b)))`。

    把「借位」这件事用位运算表达：`a - b` 的借位落在最高位，
    但只有当 a、b 同号时借位才等价于 a < b，所以再与 `a ^ b` 的符号位做一次修正。
    """
    a &= MASK32
    b &= MASK32
    return msb32(a ^ ((a ^ b) | (((a - b) & MASK32) ^ b)))


def ge32(a, b):
    return ~lt32(a, b) & MASK32


def le32(a, b):
    return ge32(b, a)


def select32(mask, a, b):
    """constant_time_select：`(barrier(m) & a) | (barrier(~m) & b)`。

    mask 为全 1 取 a，为 0 取 b。**两侧都要过 barrier** —— 只挡一边没用。
    """
    m = value_barrier32(mask)
    return (m & a) | ((~m & MASK32) & b)


def select8(mask, a, b):
    return select32(mask, a, b) & 0xFF


def select_int(mask, a, b):
    """返回有符号结果（OpenSSL 的 constant_time_select_int）。"""
    v = select32(mask, a & MASK32, b & MASK32)
    return v - (1 << 32) if v >> 31 else v


def ct_memcmp(x, y):
    """常量时间比较：不提前返回，把所有字节的差异累积进一个累加器。

    与 Go 的 `subtle.ConstantTimeCompare` 语义一致（长度不等立即返回 0）。
    """
    if len(x) != len(y):
        return 0
    acc = 0
    for i in range(len(x)):
        acc |= x[i] ^ y[i]
    return int(acc == 0)


def ct_lookup(table, index):
    """常量时间表查找：把整张表都读一遍，用掩码挑出想要的那个。

    代价是 O(n) 而不是 O(1)，但**访问序列与 index 无关** —— 这正是
    「缓存侧信道」要求的性质（BearSSL 对 AES S 盒也这么干）。
    """
    out = 0
    for i, v in enumerate(table):
        out |= select32(eq32(i, index), v, 0)
    return out
