"""RedisBloom 的布隆过滤器：位宽/哈希数公式、scalable 链的误差收紧与扩容。

事实来源（本轮实读，非记忆）：
  - RedisBloom@master `deps/bloom/bloom.h`：struct bloom 字段、BLOOM_OPT_* 四个选项
  - RedisBloom@master `deps/bloom/bloom.c` 120-127：calc_bpe（denom = ln(2)^2 = 0.480453013918201）
  - RedisBloom@master `deps/bloom/bloom.c` 66-90：bloom_calc_hash / bloom_calc_hash64 与 CHECK_ADD_FUNC
  - RedisBloom@master `deps/bloom/bloom.c` 135-205：bloom_init 的三条分支与 64 位对齐
  - RedisBloom@master `src/sb.h`：SBLink / SBChain
  - RedisBloom@master `src/sb.c` 29-111：ERROR_TIGHTENING_RATIO 0.5、SBChain_AddLink、SBChain_AddToLink、SBChain_Add
  - RedisBloom@master `src/sb.c` 128-140：SB_NewChain（首链的 error 已经乘过 tightening）
  - RedisBloom@master `src/config.c` 20-64：8 个模块默认值
  - RedisBloom@master `src/config.h` 22：BF_ERROR_RATE_CAP 0.25
  - RedisBloom@master `src/rebloom.c` 137-205：BF.RESERVE 的参数校验
"""

import math

LN2 = math.log(2.0)
LN2_SQUARED = 0.480453013918201          # bloom.c:121 的 denom

BLOOM_OPT_NOROUND = 1                    # 不取整到 2 的幂，省内存
BLOOM_OPT_ENTS_IS_BITS = 2               # entries 参数其实是位数的 log2
BLOOM_OPT_FORCE64 = 4                    # 强制 64 位哈希
BLOOM_OPT_NO_SCALING = 8                 # 禁止扩容

ERROR_TIGHTENING_RATIO = 0.5
BF_ERROR_RATE_CAP = 0.25
UINT64_MAX = (1 << 64) - 1

# src/config.c 的模块默认值
DEFAULT_BF_ERROR_RATE = 0.01
DEFAULT_BF_INITIAL_SIZE = 100
DEFAULT_BF_EXPANSION_FACTOR = 2
DEFAULT_CF_BUCKET_SIZE = 2
DEFAULT_CF_INITIAL_SIZE = 1024
DEFAULT_CF_MAX_ITERATIONS = 20
DEFAULT_CF_EXPANSION_FACTOR = 1
DEFAULT_CF_MAX_EXPANSIONS = 32

SB_SUCCESS = 0
SB_ERR = -1
SB_FULL = -2
SB_OOM = -3
SB_INVALID = -4

MODE_READ = 0
MODE_WRITE = 1


class BloomError(Exception):
    """参数非法或过滤器已满。"""


# ------------------------------------------------------------------ 位宽公式
def calc_bpe(error):
    """bloom.c:120：bpe = -ln(error) / ln(2)^2。"""
    return -math.log(error) / LN2_SQUARED


class Bloom:
    """对应 deps/bloom 的 struct bloom。bf 用 bytearray 表示。"""

    def __init__(self, entries, error, options=BLOOM_OPT_NOROUND):
        if entries < 1 or error <= 0 or error >= 1.0:
            raise BloomError("invalid entries or error")
        self.error = error
        self.bpe = calc_bpe(error)
        self.n2 = 0
        self.entries = entries

        if options & BLOOM_OPT_ENTS_IS_BITS:
            if entries > 64:
                raise BloomError("ENT_IS_BITS: too many bits")
            self.n2 = entries
            bits = 1 << self.n2
            self.entries = int(bits / self.bpe)
        elif options & BLOOM_OPT_NOROUND:
            bits = int(entries * self.bpe)
            if bits == 0:
                bits = 1
        else:
            product = entries * self.bpe
            bn2 = math.floor(math.log2(product))
            if bn2 > 63 or math.isinf(bn2):
                raise BloomError("too many bits")
            self.n2 = int(bn2) + 1
            bits = 1 << self.n2
            # 向上取整多出来的位能多装几个元素，一并认领
            item_diff = int((bits - product) / self.bpe)
            self.entries = entries + item_diff

        # 关键：bytes 一律向上取到 8 的倍数（64 位字对齐）
        self.bytes = ((bits // 64) + 1) * 8 if bits % 64 else bits // 8
        self.bits = self.bytes * 8
        self.hashes = int(math.ceil(LN2 * self.bpe))
        self.bf = bytearray(self.bytes)

    # ------------------------------------------------------------ 位操作
    def _mod(self):
        """取整过的过滤器用 1<<n2 做模，NOROUND 的用真实 bits。"""
        return (1 << self.n2) if self.n2 else self.bits

    def _test_bit_set_bit(self, index, mode):
        byte, offset = index >> 3, index & 7
        mask = 1 << offset
        current = self.bf[byte]
        if current & mask:
            return 1
        if mode == MODE_WRITE:
            self.bf[byte] = current | mask
        return 0

    def _check_add(self, a, b, mode):
        """bloom.c:87 CHECK_ADD_FUNC：双哈希 (a + i*b) % mod。"""
        found_unset = 0
        mod = self._mod()
        for i in range(self.hashes):
            x = ((a + i * b) % mod) % self.bits
            if not self._test_bit_set_bit(x, mode):
                if mode == MODE_READ:
                    return 0
                found_unset = 1
        return 1 if mode == MODE_READ else found_unset

    def check(self, a, b):
        """全部位都为 1 → 1（可能存在）；任一位为 0 → 0（一定不存在）。"""
        return self._check_add(a, b, MODE_READ)

    def add(self, a, b):
        """返回 found_unset：1 表示本次确实置上了新位（此前大概率不存在）。"""
        return self._check_add(a, b, MODE_WRITE)

    def popcount(self):
        return sum(bin(byte).count("1") for byte in self.bf)


# ------------------------------------------------------- scalable bloom 链
class SBLink:
    __slots__ = ("inner", "size")

    def __init__(self, inner):
        self.inner = inner
        self.size = 0


class SBChain:
    """src/sb.h 的 SBChain：一条由若干 Bloom 串成的链。"""

    def __init__(self, options=BLOOM_OPT_FORCE64 | BLOOM_OPT_NOROUND, growth=2):
        self.filters = []
        self.size = 0
        self.options = options
        self.growth = growth

    @property
    def nfilters(self):
        return len(self.filters)

    @property
    def cur(self):
        return self.filters[-1]


def sb_chain_add_link(chain, size, error_rate):
    """sb.c:31。"""
    chain.filters.append(SBLink(Bloom(size, error_rate, chain.options)))
    return SB_SUCCESS


def sb_new_chain(initsize, error_rate, options=BLOOM_OPT_FORCE64 | BLOOM_OPT_NOROUND,
                 growth=2):
    """sb.c:128：首链的 error 就已经乘过 tightening（0.5），NONSCALING 时 tightening 为 1。"""
    if initsize == 0 or error_rate == 0 or error_rate >= 1:
        raise BloomError(f"SB_INVALID (initsize={initsize}, error={error_rate})")
    tightening = 1 if (options & BLOOM_OPT_NO_SCALING) else ERROR_TIGHTENING_RATIO
    chain = SBChain(options=options, growth=growth)
    sb_chain_add_link(chain, initsize, error_rate * tightening)
    return chain


def sb_chain_add(chain, a, b):
    """sb.c:83：先在新旧所有链里查，命中返回 0；当前链满了才加新链。"""
    for i in range(chain.nfilters - 1, -1, -1):
        if chain.filters[i].inner.check(a, b):
            return 0
    cur = chain.cur
    if cur.size >= cur.inner.entries:
        if chain.options & BLOOM_OPT_NO_SCALING:
            return SB_FULL
        if chain.growth == 0 or cur.inner.entries > UINT64_MAX // chain.growth:
            return SB_ERR
        error = cur.inner.error * ERROR_TIGHTENING_RATIO
        sb_chain_add_link(chain, cur.inner.entries * chain.growth, error)
        cur = chain.cur
    if cur.size == UINT64_MAX or chain.size == UINT64_MAX:
        return SB_ERR
    rv = 1 if cur.inner.add(a, b) else 0
    if rv:
        cur.size += 1
        chain.size += 1
    return rv


def sb_chain_check(chain, a, b):
    """sb.h 的 SBChain_Check：任一链命中即认为见过。"""
    return any(link.inner.check(a, b) for link in chain.filters)


# ------------------------------------------------------------- BF.RESERVE
def bf_reserve_validate(error_rate, capacity, expansion=None, nonscaling=False):
    """rebloom.c:137：返回 (error_rate, capacity, expansion, options)。

    - error_rate 必须在 (0, 1) 开区间，超过 BF_ERROR_RATE_CAP 被**截断**到 0.25（不是报错）
    - capacity 必须在 [1, 2^30]
    - EXPANSION 与 NONSCALING 互斥：先声明 NONSCALING 又给 EXPANSION → 报错
    - expansion == 0 等价于 NONSCALING
    """
    if not (0.0 < error_rate < 1.0):
        raise BloomError("ERR error rate must be in the range (0, 1)")
    if error_rate > BF_ERROR_RATE_CAP:
        error_rate = BF_ERROR_RATE_CAP
    if not (1 <= capacity <= (1 << 30)):
        raise BloomError("ERR capacity must be in the range [1, 1073741824]")
    options = BLOOM_OPT_FORCE64 | BLOOM_OPT_NOROUND
    if expansion is None:
        expansion = DEFAULT_BF_EXPANSION_FACTOR
        if nonscaling:
            options |= BLOOM_OPT_NO_SCALING
    else:
        if expansion == 0:
            options |= BLOOM_OPT_NO_SCALING
        elif nonscaling:
            raise BloomError("Nonscaling filters cannot expand")
        if not (0 <= expansion <= 32768):
            raise BloomError("ERR expansion must be in the range [0, 32768]")
    return error_rate, capacity, expansion, options
