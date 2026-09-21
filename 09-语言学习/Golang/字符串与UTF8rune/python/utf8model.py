"""Go unicode/utf8 包的可执行模型。

逐行转写自官方源码 src/unicode/utf8/utf8.go（master 分支）。
本模块刻意**不依赖** Python 的编解码器，而是自己实现 Go 的那套查表逻辑，
再在 selfcheck 里与 Python codec 做对拍——这样差异才看得见。
"""

# 官方常量（utf8.go 的 const 块）
RUNE_ERROR = 0xFFFD  # '\uFFFD' 替换字符
RUNE_SELF = 0x80     # 小于它的字符用单字节表示
MAX_RUNE = 0x10FFFF
UTF_MAX = 4

SURROGATE_MIN = 0xD800
SURROGATE_MAX = 0xDFFF

T_X = 0b10000000
T_2 = 0b11000000
T_3 = 0b11100000
T_4 = 0b11110000
T_5 = 0b11111000

MASKX = 0b00111111
MASK2 = 0b00011111
MASK3 = 0b00001111
MASK4 = 0b00000111

RUNE1_MAX = (1 << 7) - 1
RUNE2_MAX = (1 << 11) - 1
RUNE3_MAX = (1 << 16) - 1

LOCB = 0b10000000
HICB = 0b10111111

# first 表的记号：高 4 位是 acceptRanges 下标（F=特殊单字节），低 4 位是长度或状态
XX = 0xF1  # 非法：size 1
AS = 0xF0  # ASCII：size 1
S1 = 0x02  # accept 0, size 2
S2 = 0x13  # accept 1, size 3
S3 = 0x03  # accept 0, size 3
S4 = 0x23  # accept 2, size 3
S5 = 0x34  # accept 3, size 4
S6 = 0x04  # accept 0, size 4
S7 = 0x44  # accept 4, size 4


def build_first_table():
    """复现 utf8.go 的 first [256]uint8 表（按官方注释的分区构造）。"""
    first = []
    first += [AS] * 0x80                 # 0x00-0x7F ASCII
    first += [XX] * 0x40                 # 0x80-0xBF 续字节当首字节 = 非法
    first += [S1] * 0x20                 # 0xC0-0xDF 两字节
    first.append(S2)                     # 0xE0
    first += [S3] * 12                   # 0xE1-0xEC
    first.append(S4)                     # 0xED（代理区：靠 acceptRanges 拦住）
    first += [S3] * 2                    # 0xEE-0xEF
    first.append(S5)                     # 0xF0
    first += [S6] * 3                    # 0xF1-0xF3
    first.append(S7)                     # 0xF4（> U+10FFFF：靠 acceptRanges 拦住）
    first += [XX] * 11                   # 0xF5-0xFF
    assert len(first) == 256, len(first)
    return first


FIRST = build_first_table()

# acceptRanges：第二个字节的合法区间（16 项，只有 0..4 被用到）
ACCEPT_RANGES = [
    (LOCB, HICB),   # 0
    (0xA0, HICB),   # 1：E0 开头，拒绝 overlong
    (LOCB, 0x9F),   # 2：ED 开头，拒绝代理区
    (0x90, HICB),   # 3：F0 开头，拒绝 overlong
    (LOCB, 0x8F),   # 4：F4 开头，拒绝 > U+10FFFF
]


class Rune(object):
    """为了与 Go 的 rune 对齐：解码结果是 (码点, 字节数) 二元组。"""

    __slots__ = ("r", "size")

    def __init__(self, r, size):
        self.r = r
        self.size = size

    def __iter__(self):
        return iter((self.r, self.size))

    def __eq__(self, other):
        return tuple(self) == tuple(other)

    def __repr__(self):
        return "(%d, %d)" % (self.r, self.size)


def decode_rune(p):
    """DecodeRune：ASCII 快路径 + decodeRuneSlow。"""
    if len(p) == 0:
        return Rune(RUNE_ERROR, 0)
    if p[0] < RUNE_SELF:
        return Rune(p[0], 1)
    return _decode_rune_slow(p)


def _decode_rune_slow(p):
    """decodeRuneSlow：查 first 表 + acceptRanges，逐字节校验。"""
    n = len(p)
    if n < 1:
        return Rune(RUNE_ERROR, 0)
    p0 = p[0]
    x = FIRST[p0]
    if x >= AS:
        # 官方用 mask-and-or 同时处理 ASCII 与非法，避免一次分支
        mask = (x << 31 >> 31) & 0xFFFFFFFF
        if mask:
            mask = -1
        return Rune((p0 & ~mask) | (RUNE_ERROR & mask), 1)
    sz = x & 7
    accept = ACCEPT_RANGES[x >> 4]
    if n < sz:
        return Rune(RUNE_ERROR, 1)
    b1 = p[1]
    if b1 < accept[0] or accept[1] < b1:
        return Rune(RUNE_ERROR, 1)
    if sz <= 2:
        return Rune((p0 & MASK2) << 6 | (b1 & MASKX), 2)
    b2 = p[2]
    if b2 < LOCB or HICB < b2:
        return Rune(RUNE_ERROR, 1)
    if sz <= 3:
        return Rune((p0 & MASK3) << 12 | (b1 & MASKX) << 6 | (b2 & MASKX), 3)
    b3 = p[3]
    if b3 < LOCB or HICB < b3:
        return Rune(RUNE_ERROR, 1)
    return Rune((p0 & MASK4) << 18 | (b1 & MASKX) << 12 | (b2 & MASKX) << 6 | (b3 & MASKX), 4)


def decode_rune_in_string(s):
    """DecodeRuneInString：与 DecodeRune 同语义（官方是直接转成 []byte 处理）。"""
    return decode_rune(list(s.encode("utf-8", "surrogatepass"))
                       if isinstance(s, str) else list(s))


def rune_len(r):
    """RuneLen：注意 surrogate 的判断夹在 rune2Max 与 rune3Max 之间。"""
    if r < 0:
        return -1
    if r <= RUNE1_MAX:
        return 1
    if r <= RUNE2_MAX:
        return 2
    if SURROGATE_MIN <= r <= SURROGATE_MAX:
        return -1
    if r <= RUNE3_MAX:
        return 3
    if r <= MAX_RUNE:
        return 4
    return -1


def rune_error_bytes():
    """官方常量：RuneError 的三字节编码。"""
    return [T_3 | (RUNE_ERROR >> 12), T_X | (RUNE_ERROR >> 6) & MASKX, T_X | RUNE_ERROR & MASKX]


def encode_rune(r):
    """EncodeRune：返回字节列表；越界或代理区写 RuneError 的编码。"""
    if r < 0 or SURROGATE_MIN <= r <= SURROGATE_MAX or r > MAX_RUNE:
        return rune_error_bytes()
    if r <= RUNE1_MAX:
        return [r]
    if r <= RUNE2_MAX:
        return [T_2 | (r >> 6), T_X | (r & MASKX)]
    if r <= RUNE3_MAX:
        return [T_3 | (r >> 12), T_X | (r >> 6) & MASKX, T_X | (r & MASKX)]
    return [T_4 | (r >> 18), T_X | (r >> 12) & MASKX, T_X | (r >> 6) & MASKX, T_X | (r & MASKX)]


def rune_count_in_string(data):
    """RuneCountInString：官方实现就是 for range（每个非法字节算 1 个）。"""
    n = 0
    i = 0
    b = data if isinstance(data, (bytes, list)) else list(data)
    while i < len(b):
        r, size = decode_rune(b[i:])
        if size == 0:
            break
        n += 1
        i += size
    return n


def rune_start(b):
    """RuneStart：续字节的高两位恒为 10。"""
    return b & 0xC0 != 0x80


def valid(data):
    """Valid：整串都是合法 UTF-8。"""
    b = list(data)
    i = 0
    while i < len(b):
        r, size = decode_rune(b[i:])
        if r == RUNE_ERROR and size == 1 and b[i] >= RUNE_SELF:
            return False
        i += size
    return True
