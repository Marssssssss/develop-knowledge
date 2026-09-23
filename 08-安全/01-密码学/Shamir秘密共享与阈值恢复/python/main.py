"""三套 Shamir 秘密共享的对照演示（确定性随机源，每次输出一致）。"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gf256
import prime
import slip39

SEC = b"hello shamir!!"
BAR = "-" * 60


def main():
    print("1. 素数域 GF(2^31-1)：秘密是常数项 f(0)")
    print(BAR)
    rp = random.Random(3)
    shares = prime.split(1234567, 5, 3, rp)
    for x, y in shares:
        print("   份额 x=%d  y=%d" % (x, y))
    print("   任意 3 份还原   ->", prime.recover(shares[:3]))
    print("   只给 2 份还原   ->", prime.recover(shares[:2]), "（不是秘密）")
    forged = prime.forge_share_for_secret(shares[:2], 9, 42)
    print("   伪造第 3 份     ->", prime.recover(shares[:2] + [(9, forged)]),
          "（想要多少就能命中多少 ⇒ 2 份零信息）")

    print()
    print("2. GF(2^8)（Vault shamir.go）：每字节一条多项式，x 挂在末尾")
    print(BAR)
    rv = random.Random(11)
    vs = gf256.split(SEC, 5, 3, rv)
    for q in vs:
        print("   份额 %s  x=%d" % (q[:6].hex(), q[-1]))
    print("   任意 3 份还原   ->", gf256.combine([vs[0], vs[2], vs[4]]))
    print("   只给 2 份还原   ->", gf256.combine([vs[0], vs[2]]))
    print("   0x57·0x83       -> 0x%02x（AES 域常量）" % gf256.mult(0x57, 0x83))

    print()
    print("3. SLIP-0039：秘密在 x=255、摘要在 x=254，份额索引是 0..N-1")
    print(BAR)
    r39 = random.Random(7)
    s39 = slip39.split_secret(3, 5, bytes.fromhex(
        "9d1c2f88a37701fe5bccd240196eab03"), r39)
    for i, q in enumerate(s39):
        print("   份额 %d  %s…" % (i, q[:8].hex()))
    print("   取 0,2,4 三份   ->",
          slip39.recover_secret(3, [(0, s39[0]), (2, s39[2]), (4, s39[4])]).hex())
    bad = bytearray(s39[1])
    bad[0] ^= 1
    try:
        slip39.recover_secret(3, [(0, s39[0]), (1, bytes(bad)), (2, s39[2])])
        print("   篡改后         -> 未检出（不该发生）")
    except ValueError as e:
        print("   篡改第 1 份后   -> 检出:", e)
    data = [5, 6, 7, 8]
    print("   RS1024 校验和   ->", slip39.rs1024_create_checksum("shamir", data),
          "verify =", slip39.rs1024_verify_checksum(
              "shamir", data + slip39.rs1024_create_checksum("shamir", data)))

    print()
    print("4. 三套方案的取舍")
    print(BAR)
    print("   素数域  ：份额是整数对，没有字节长度限制，但每个份额都带一个大整数")
    print("   GF(2^8) ：份额与秘密等长（+1 字节），实现最省事，代价是每字节一条多项式")
    print("   SLIP-0039：份额是助记词，带摘要 D 能自检出拼错，代价是 N ≤ 16 且秘密 ≥128 位")


if __name__ == "__main__":
    main()
