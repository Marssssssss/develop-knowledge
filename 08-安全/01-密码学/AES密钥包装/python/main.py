"""AES 密钥包装演示：RFC 3394（AES-KW）+ RFC 5649（AES-KWP）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aes_core import SBOX, encrypt_block, decrypt_block
from kw import (aes_wrap, aes_unwrap, kwp_wrap, kwp_unwrap, unwrap_core,
                KeyWrapError, DEFAULT_IV, AIV_CONST)


def hx(b):
    return b.hex().upper()


def line(t):
    print("\n" + t)
    print("-" * max(len(t) * 2, 60))


def main():
    line("1. AES 分组密码（FIPS 197，本 demo 自带）")
    print("SBOX[0x00]=%02X SBOX[0x01]=%02X SBOX[0x53]=%02X" %
          (SBOX[0], SBOX[1], SBOX[0x53]))
    key = bytes(range(16))
    blk = bytes.fromhex("00112233445566778899AABBCCDDEEFF")
    c = encrypt_block(key, blk)
    print("AES-128 %s -> %s" % (hx(blk), hx(c)))
    print("        解密还原 %s" % hx(decrypt_block(key, c)))

    line("2. RFC 3394 AES-KW：默认 IV = %016X" % DEFAULT_IV)
    kek = bytes.fromhex("000102030405060708090A0B0C0D0E0F")
    pt = bytes.fromhex("00112233445566778899AABBCCDDEEFF")
    ct = aes_wrap(kek, pt)
    print("KEK  %s" % hx(kek))
    print("明文 %s  (%d 位)" % (hx(pt), len(pt) * 8))
    print("密文 %s  (多出 64 位)" % hx(ct))
    print("解包 %s" % hx(aes_unwrap(kek, ct)))
    bad = bytearray(ct)
    bad[3] ^= 0x01
    try:
        aes_unwrap(kek, bytes(bad))
        print("篡改后竟然通过（不应发生）")
    except KeyWrapError as e:
        print("篡改 1 位 -> 解包拒绝：%s" % e)

    line("3. RFC 5649 AES-KWP：AIV = A65959A6 || MLI")
    kek3 = bytes.fromhex("5840df6e29b02af1ab493b705bf16ea1ae8338f4dcc176a8")
    for m in (7, 20):
        pt3 = bytes((i * 7 + 0x30) & 0xFF for i in range(m))
        ct3 = kwp_wrap(kek3, pt3)
        a, padded = unwrap_core(kek3, ct3)
        n = len(padded) // 8
        print("m=%2d -> 补齐 %2d 字节 (n=%d) -> 密文 %d 字节" %
              (m, len(padded), n, len(ct3)))
        print("     A = %016X  高32位=%08X 低32位(MLI)=%d" %
              (a, a >> 32, a & 0xFFFFFFFF))
        print("     往返 %s" % hx(kwp_unwrap(kek3, ct3)))

    line("4. AIV 三条完整性检查（RFC 5649 §3）")
    print("1) MSB(32,A) == %08X" % AIV_CONST)
    print("2) 8*(n-1) <  LSB(32,A) <= 8*n")
    print("3) 右端 b = 8*n - MLI 个补齐字节全为零")
    kek4 = bytes.fromhex("000102030405060708090A0B0C0D0E0F")
    body = bytearray(b"\x11" * 16)
    ok_ct = aes_wrap(kek4, bytes(body), iv=(AIV_CONST << 32) | 16)
    print("MSB 正确          -> %s" % hx(kwp_unwrap(kek4, ok_ct)))
    for name, iv, pad in (("MSB 错", (0x11223344 << 32) | 16, b""),
                          ("MLI=17 越界", (AIV_CONST << 32) | 17, b""),
                          ("补齐非零", (AIV_CONST << 32) | 15, b"\x01")):
        b2 = bytearray(b"\x11" * 15 + b"\x00")
        if pad:
            b2[15:] = pad
        ct4 = aes_wrap(kek4, bytes(b2), iv=iv)
        try:
            kwp_unwrap(kek4, ct4)
            print("%-14s -> 竟然通过（不应发生）" % name)
        except KeyWrapError as e:
            print("%-14s -> 拒绝：%s" % (name, e))

    line("5. 与 RFC 3394 的边界")
    print("AES-KW 要求 n >= 2（明文至少 16 字节）；n == 1 是 KWP 独有的单块 ECB 分支")
    print("AES-KWP 明文长度 1 .. 2^32-1 字节，密文恒为 8*(ceil(m/8)+1) 字节")
    for m in (8, 9, 16, 17):
        print("    m=%2d -> 密文 %d 字节" % (m, len(kwp_wrap(kek4, b"a" * m))))


if __name__ == "__main__":
    main()
