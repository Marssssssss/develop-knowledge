"""CRC 参数反解的自检。

E1~E3 模型正确性（113 个官方 check 全量对拍 + zlib 交叉验证）；
E4~E5 反射与目录的 init 例外；E6~E7 函数等价与「等长差不变式」；
E8~E10 反解回路与参数不唯一性。

断言基于实抓的 `reveng.sourceforge.io/crc-catalogue/all.htm`（113 个模型）。

运行：python selfcheck_crc.py
"""

import random
import sys
import zlib

from crc_model import (CATALOGUE, INIT_ALREADY_REFLECTED, Model, catalogue_models,
                       crc, crc_fast, functional_key, reflect)
from main import crc0_poly, equivalent, recover, samples, search_polys

PASS = [0]
FAIL = [0]


def ok(cond, msg):
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
        print("  FAIL:", msg)


def eq(got, want, msg):
    ok(got == want, "%s (got=%r want=%r)" % (msg, got, want))


BY_ROW = {row[0]: row for row in CATALOGUE}


# ------------------------------------------------------------ E1 官方 check

def e1_check_values():
    for m in catalogue_models():
        eq(crc(m, b"123456789"), BY_ROW[m.name][7], "E1 check %s" % m.name)


# ------------------------------------------------------------ E2 两条路径一致

def e2_fast_equals_bit():
    rnd = random.Random(11)
    for m in catalogue_models():
        for _ in range(4):
            msg = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 24)))
            eq(crc_fast(m, msg), crc(m, msg), "E2 fast==bit %s" % m.name)


# ------------------------------------------------------------ E3 zlib 对拍

def e3_zlib():
    m = next(x for x in catalogue_models() if x.name == "CRC-32/ISO-HDLC")
    rnd = random.Random(3)
    for _ in range(24):
        msg = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 40)))
        eq(crc_fast(m, msg), zlib.crc32(msg), "E3 zlib.crc32 对拍")
    eq(crc_fast(m, b"123456789"), 0xCBF43926, "E3 CRC-32 的 check")


# ------------------------------------------------------------ E4 反射

def e4_reflect():
    eq(reflect(0x01, 8), 0x80, "E4 8 位反射")
    eq(reflect(0x80, 8), 0x01, "E4 8 位反射")
    eq(reflect(0x04C11DB7, 32), 0xEDB88320, "E4 CRC-32 多项式反射")
    for w in (3, 5, 7, 16, 24):
        v = (1 << w) - 1
        eq(reflect(reflect(v, w), w), v, "E4 反射是对合 w=%d" % w)


# ------------------------------------------------------------ E5 init 例外

def e5_init_exception():
    eq(len(INIT_ALREADY_REFLECTED), 4, "E5 4 个条目 init 已是寄存器域")
    for name in sorted(INIT_ALREADY_REFLECTED):
        row = BY_ROW[name]
        m = Model(row[1], row[2], row[3], row[4], row[5], row[6], name)
        ok(crc(m, b"123456789") != row[7],
           "E5 %s 反射 init 后 check 反而不对（负向）" % name)
        m.init = reflect(m.init, m.width)
        eq(crc(m, b"123456789"), row[7], "E5 %s 不反射 init 才对" % name)


# ------------------------------------------------------------ E6 函数等价

def e6_functional_key():
    ms = catalogue_models()
    rnd = random.Random(5)
    pairs = [(ms[rnd.randrange(len(ms))], ms[rnd.randrange(len(ms))])
             for _ in range(40)]
    diff = sum(1 for a, b in pairs
               if a.name != b.name and functional_key(a) != functional_key(b))
    ok(diff >= 30, "E6 不同模型的函数指纹大多不同（实测 %d/40）" % diff)
    # 同参数必同指纹
    a = Model(16, 0x1021, 0xFFFF, False, False, 0x0000)
    b = Model(16, 0x1021, 0xFFFF, False, False, 0x0000)
    eq(functional_key(a), functional_key(b), "E6 同参数同指纹")


# ------------------------------------------------------------ E7 差不变式

def e7_difference_invariant():
    """等长报文的 CRC 之差与 init / xorout 无关——这是 poly 搜索能成立的根据。"""
    for name in ("CRC-8/SMBUS", "CRC-16/ARC", "CRC-32/ISO-HDLC"):
        m = next(x for x in catalogue_models() if x.name == name)
        msg1, msg2 = b"\x01\x02\x03\x04", b"\xaa\xbb\xcc\xdd"
        deltas = set()
        for init in range(0, 1 << m.width, max(1, (1 << m.width) // 16)):
            a = crc_fast(Model(m.width, m.poly, init, m.refin, m.refout, 0), msg1)
            b = crc_fast(Model(m.width, m.poly, init, m.refin, m.refout, 0), msg2)
            deltas.add(a ^ b)
        eq(len(deltas), 1, "E7 %s 等长差与 init 无关" % name)


# ------------------------------------------------------------ E8 反解回路

def e8_recovery():
    for name in ("CRC-8/SMBUS", "CRC-8/MAXIM-DOW", "CRC-8/AUTOSAR"):
        m = next(x for x in catalogue_models() if x.name == name)
        pairs = samples(m, n=10, seed=5, minlen=1, maxlen=6)
        found = recover(pairs, m.width)
        ok(len(found) >= 1, "E8 %s 至少解出一个参数组" % name)
        good = [f for f in found if equivalent(f, m)]
        ok(len(good) >= 1, "E8 %s 至少一个与真值函数等价" % name)
        for f in found:
            ok(equivalent(f, m), "E8 %s 解出的都应与真值等价" % name)


# ------------------------------------------------------------ E9 参数不唯一

def e9_non_unique():
    m = next(x for x in catalogue_models() if x.name == "CRC-8/SMBUS")
    pairs = samples(m, n=10, seed=5, minlen=1, maxlen=6)
    found = recover(pairs, m.width)
    ok(len(found) >= 2, "E9 参数组不唯一（实测 %d 个）" % len(found))
    ok(any(f.params() != m.params() for f in found),
       "E9 至少有一个解的参数与真值不同却函数等价")


# ------------------------------------------------------------ E10 两路径一致

def e10_crc0_poly():
    rnd = random.Random(17)
    for _ in range(30):
        w = rnd.choice((8, 16, 24, 32))
        poly = rnd.randrange(1 << w)
        msg = bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 6)))
        a = crc0_poly(w, poly, msg)
        b = crc_fast(Model(w, poly, 0, False, False, 0), msg)
        eq(a, b, "E10 crc0_poly 与查表路径一致 w=%d" % w)


def main():
    for fn in (e1_check_values, e2_fast_equals_bit, e3_zlib, e4_reflect,
               e5_init_exception, e6_functional_key, e7_difference_invariant,
               e8_recovery, e9_non_unique, e10_crc0_poly):
        fn()
    print("PASS=%d FAIL=%d" % (PASS[0], FAIL[0]))
    return 1 if FAIL[0] else 0


if __name__ == "__main__":
    sys.exit(main())
