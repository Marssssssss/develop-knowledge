"""位域工具与常量:来自 include/mach-o/fixup-chains.h。

位域在 C/C++ 里从最低位开始分配,故 bits(lo, hi) 里的 lo 就是 LSB 侧起点。
"""

MASK64 = (1 << 64) - 1


def bits(v, lo, hi):
    """取 [lo, hi] 闭区间的位(0 = LSB)。"""
    return (v >> lo) & ((1 << (hi - lo + 1)) - 1)


def ins(v, lo, hi, x):
    """把 x 放进 [lo, hi] 位域(超出位宽的部分会被截断,与 C 位域赋值一致)。"""
    w = hi - lo + 1
    return (v & ~(((1 << w) - 1) << lo) & MASK64) | ((x & ((1 << w) - 1)) << lo)


def sign_extend(v, w):
    """把 w 位宽的 v 按补码还原成有符号整数。"""
    if v & (1 << (w - 1)):
        return v - (1 << w)
    return v


# ---- page_start[] 哨兵 ----
START_NONE = 0xFFFF   # 该页无修正
START_MULTI = 0x8000  # 该页有多个链起点(高位)
START_LAST = 0x8000   # overflow 列表中最后一项(与 MULTI 同值)

# ---- pointer_format ----
PTR_ARM64E = 1
PTR_64 = 2
PTR_32 = 3
PTR_64_OFFSET = 6
PTR_ARM64E_KERNEL = 7
PTR_ARM64E_USERLAND = 9
PTR_ARM64E_USERLAND24 = 12
PTR_ARM64E_SHARED_CACHE = 13

# ---- imports_format ----
IMPORT = 1
IMPORT_ADDEND = 2
IMPORT_ADDEND64 = 3


class Fixup:
    """一个链式修正条目。target 一律是"未加 slide 的 vm offset"。"""

    def __init__(self, is_bind=False, authenticated=False, ordinal=0, addend=0,
                 target=0, key=0, addr_div=0, diversity=0):
        self.is_bind = is_bind
        self.authenticated = authenticated
        self.ordinal = ordinal
        self.addend = addend
        self.target = target
        self.key = key
        self.addr_div = addr_div
        self.diversity = diversity


class Error(Exception):
    pass
