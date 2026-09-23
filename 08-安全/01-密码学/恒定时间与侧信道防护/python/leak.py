"""侧信道观测器：把「泄漏」变成可断言的量。

真实侧信道要测时间、功耗或缓存，本 demo 退一步，只统计**可观测事件**：

- `branches`：条件分支的走向序列（BearSSL：条件跳转会读不同地址的指令字节）；
- `addr`：内存访问的索引序列（缓存命中的地址序列就是泄漏本身）；
- `steps`：基本运算次数。

这三者的**分布随秘密变化**就是泄漏。Python 里没有真编译器，
`Compiler` 模拟的是"能证明掩码取值范围就折叠成分支"这一件事（BearSSL §Compiler Woes），
属**模型**而非真编译器，README 里标注了口径。
"""

from ct import Barrier, MASK32, select32


class Observer:
    def __init__(self):
        self.branches = []
        self.addr = []
        self.steps = 0

    def branch(self, taken):
        self.branches.append(bool(taken))
        return bool(taken)

    def access(self, index):
        self.addr.append(index)

    def tick(self, n=1):
        self.steps += n


class Compiler:
    """模拟"看到 mask ∈ {0, 全 1} 就折叠成条件跳转"的优化器。"""

    def __init__(self, observer):
        self.obs = observer

    def select(self, mask, a, b):
        if isinstance(mask, Barrier):
            # 屏障挡住了取值范围分析，只能老老实实做算术
            self.obs.tick(3)
            return select32(int(mask), a, b)
        # 没有屏障：掩码可被证明是 0 或全 1 → 折叠成跳转
        if self.obs.branch(mask != 0):
            return a
        return b


# ------------------------------------------------------- 朴素 vs 常量时间
def naive_memcmp(obs, x, y):
    """典型错误写法：第一个字节不等就 return —— 步数直接暴露「首个差异位置」。"""
    obs.tick()
    if len(x) != len(y):
        return 0
    for i in range(len(x)):
        obs.tick()
        if x[i] != y[i]:
            obs.branch(True)
            return 0
        obs.branch(False)
    return 1


def ct_memcmp_observed(obs, x, y):
    from ct import ct_memcmp
    obs.tick()
    if len(x) != len(y):
        return 0
    for i in range(len(x)):
        obs.tick()
    return ct_memcmp(x, y)


def naive_lookup(obs, table, index):
    obs.tick()
    obs.access(index)               # ← 索引进了缓存，后续可被测出
    return table[index]


def ct_lookup_observed(obs, table, index):
    from ct import ct_lookup
    for i in range(len(table)):
        obs.tick()
        obs.access(i)               # 访问序列恒为 0,1,2,...,n-1
    return ct_lookup(table, index)


def naive_modexp(obs, base, exp, mod):
    """平方-乘：指数为 1 就多一次乘法 —— 观测到「乘法次数与分支序列」就还原了指数。"""
    r = 1 % mod
    b = base % mod
    bits = []
    while exp > 0:
        obs.tick()
        bit = exp & 1
        bits.append(bit)
        obs.branch(bit == 1)
        if bit:
            r = r * b % mod
            obs.tick()
        b = b * b % mod
        exp >>= 1
    return r, bits


def ladder_modexp(obs, base, exp, mod):
    """Montgomery 阶梯：从**最高位**开始，每比特都算两个乘积，再用掩码挑结果。

    关键点：这里一个 `if` 都没有 —— `0 - bit` 得到全 1 或全 0 掩码，
    靠 `select32` 选值。代价是翻倍的计算量，换来**运算序列与比特值无关**。
    """
    from ct import MASK32, select32
    if exp == 0:
        return 1 % mod, []
    bits = [int(c) for c in bin(exp)[2:]]        # 高位在前
    r0, r1 = 1 % mod, base % mod
    for bit in bits:
        obs.tick()
        mask = (0 - bit) & MASK32                # bit=1 → 全 1；bit=0 → 0（无分支）
        p = r0 * r1 % mod
        q = r0 * r0 % mod
        s1 = r1 * r1 % mod
        r0 = select32(mask, p, q)
        r1 = select32(mask, s1, p)
        obs.tick(2)
    return r0, bits


def naive_padding_check(obs, data):
    """PKCS#7 去填充的朴素写法：直接 return 长度 —— 泄漏的是填充字节数。"""
    obs.tick()
    n = data[-1]
    obs.branch(n > 0)
    if n == 0 or n > 16:
        return -1
    for i in range(len(data) - n, len(data)):
        obs.tick()
        if data[i] != n:
            obs.branch(True)
            return -1
        obs.branch(False)
    return n


def ct_padding_check(obs, data):
    """OpenSSL 的做法：全程掩码，观测序列与填充长度无关。

    注意这里**一个 Python 分支都不能有** —— 包括 `1 - eq32(...)` 这种把
    全 1 掩码当 0/1 用的写法也要改成 `~mask & 1`。
    """
    from ct import eq32, lt32, ge32, MASK32
    obs.tick()
    n = data[-1]
    acc = (eq32(n, 0) & 1) | (lt32(16, n) & 1)      # n == 0 或 n > 16 都非法
    for i in range(16):
        obs.tick()
        in_pad = ge32(i, (16 - n) & MASK32) & 1     # 该字节是否属于填充区
        bad = (~eq32(data[len(data) - 16 + i], n)) & 1
        acc |= in_pad & bad
    return n if acc == 0 else -1
