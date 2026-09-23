"""侧信道演示：把「泄漏」量化出来。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ct import MASK32, eq32, lt32, msb32, select32, value_barrier32, ct_memcmp
from leak import (Compiler, Observer, ct_lookup_observed, ct_padding_check,
                  ladder_modexp, naive_lookup, naive_memcmp, naive_modexp,
                  naive_padding_check)

BAR = "-" * 62


def main():
    print("1. 掩码原语（OpenSSL constant_time.h）")
    print(BAR)
    print("   msb32(0x80000000)      = %#010x  （全 1）" % msb32(0x80000000))
    print("   lt32(3, 7)             = %#010x  lt32(7,3) = %#x" % (lt32(3, 7), lt32(7, 3)))
    print("   select32(全1, 0xAA, 0x55) = %#04x" % select32(MASK32, 0xAA, 0x55))
    print("   select32(0,   0xAA, 0x55) = %#04x" % select32(0, 0xAA, 0x55))

    print()
    print("2. memcmp：朴素版泄漏「首个差异字节的位置」")
    print(BAR)
    for pos in (0, 4, 15):
        x = bytes([0x5A] * 16)
        y = bytearray(x)
        y[pos] ^= 1
        o1, o2 = Observer(), Observer()
        naive_memcmp(o1, x, bytes(y))
        ct_memcmp(x, bytes(y))
        for _ in range(16):
            o2.tick()
        print("   差异在字节 %-2d  朴素观测=%-3d  常量时间观测=%-3d"
              % (pos, o1.steps + len(o1.branches), o2.steps + len(o2.branches)))
    print("   → 朴素版的观测值随位置线性变化，等于把「前几位是对的」告诉了攻击者")

    print()
    print("3. 表查找：朴素版把索引送进缓存")
    print(BAR)
    table = [i * 7 % 256 for i in range(256)]
    o = Observer()
    naive_lookup(o, table, 200)
    print("   朴素  访问序列 =", o.addr)
    o = Observer()
    ct_lookup_observed(o, table, 200)
    print("   常量时间  访问序列 =", o.addr[:6], "… 共 %d 次（与索引无关）" % len(o.addr))

    print()
    print("4. 模幂：平方-乘 与 Montgomery 阶梯")
    print(BAR)
    for exp in (0b1011, 0b1000):
        o1, o2 = Observer(), Observer()
        r1, b1 = naive_modexp(o1, 3, exp, 65537)
        r2, b2 = ladder_modexp(o2, 3, exp, 65537)
        print("   exp=%s  朴素步数=%d（1 的个数=%d）  阶梯步数=%d  结果都=%d"
              % (bin(exp), o1.steps, sum(b1), o2.steps, r1))
    print("   → 朴素版步数 = 比特数 + 1 的个数，指数因此被逐比特读出来")

    print()
    print("5. value_barrier：挡住「把掩码折叠成 if」的编译器")
    print(BAR)
    o = Observer()
    c = Compiler(o)
    c.select(0, 0xAA, 0x55)
    c.select(MASK32, 0xAA, 0x55)
    print("   不带屏障 -> 分支序列", o.branches, "（直接暴露掩码）")
    o = Observer()
    c = Compiler(o)
    c.select(value_barrier32(0), 0xAA, 0x55)
    c.select(value_barrier32(MASK32), 0xAA, 0x55)
    print("   带屏障   -> 分支序列", o.branches, "，只剩 %d 次算术" % o.steps)

    print()
    print("6. PKCS#7 去填充")
    print(BAR)
    for n in (1, 8, 16):
        data = bytes(16 - n) + bytes([n]) * n
        o1, o2 = Observer(), Observer()
        naive_padding_check(o1, data)
        ct_padding_check(o2, data)
        print("   填充 %-2d 字节  朴素观测=%-3d  常量时间观测=%-3d"
              % (n, o1.steps + len(o1.branches), o2.steps))


if __name__ == "__main__":
    main()
