"""Shamir 秘密共享自检：素数域 / Vault GF(256) / SLIP-0039 三套对照。

断言原则：只钉**可确定的一侧**（恢复成功/失败、不相等、报错类型），
以及**官方实现里逐字抄来的常量**（AES 的 0x57·0x83 = 0xC1、SLIP-0039 的 RS1024 生成多项式）。
"""

import hashlib
import hmac
import itertools
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gf256
import prime
import slip39

PASS = 0
FAIL = 0
BAD = []


def ck(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        BAD.append(msg)


def raises(fn, msg):
    """必须传**可调用对象**，写成 raises(f(x)) 会在调用前就抛错。"""
    try:
        fn()
    except Exception:
        ck(True, msg)
        return
    ck(False, msg + " —— 期望抛错但没有")


SECRET16 = bytes.fromhex("9d1c2f88a37701fe5bccd240196eab03")

# ================================================== A. 素数域 GF(p)
ck(prime.is_prime_field(), "p = 2^31-1 是素数")
ck(prime.P == 2147483647, "p = 2147483647")
for a in (2, 7, 12345, prime.P - 1):
    ck(a * prime.modinv(a) % prime.P == 1, "a·a^-1 = 1 mod p, a=%d" % a)

rp = random.Random(3)
ps = prime.split(1234567, 5, 3, rp)
ck(prime.eval_poly(prime.make_poly(1234567, 2, random.Random(1)), 0) == 1234567,
   "f(0) 就是秘密")
ck(len(ps) == 5 and [x for x, _ in ps] == [1, 2, 3, 4, 5], "份额 x 取 1..n（不用 0）")
for comb in itertools.combinations(range(5), 3):
    ck(prime.recover([ps[i] for i in comb]) == 1234567,
       "任意 3 份都能还原，组合=%s" % (comb,))
ck(prime.recover(ps[:2]) != 1234567, "2 份还原不出秘密（3-of-5）")
ck(prime.recover(ps) == 1234567, "多给份额不影响结果")
ck(sum(prime.lagrange_basis(ps[:3], i) for i in range(3)) % prime.P == 1,
   "Σ L_i(0) = 1（常数多项式插值不变量）")
# 少于 t 份 ⇒ 零信息：任意目标秘密都能造出第 t 份份额
for target in (0, 42, 1, prime.P - 1):
    forged = prime.forge_share_for_secret(ps[:2], 9, target)
    ck(prime.recover(ps[:2] + [(9, forged)]) == target,
       "伪造第 3 份使插值命中任意目标秘密=%d（说明 2 份零信息）" % target)
ck(prime.forge_share_for_secret(ps[:2], 9, 1234567) != ps[2][1],
   "即使目标就是真秘密，伪造出的份额也与真份额不同（重构不唯一）")
raises(lambda: prime.interpolate([(1, 5), (1, 6)]), "重复 x 必须报错")
raises(lambda: prime.split(1, 3, 5, rp), "t > n 必须报错")

# ============================================ B. GF(2^8)（Vault shamir.go）
ck(gf256.IRRED == 0x1B, "不可约多项式低 8 位是 0x1B（x^8+x^4+x^3+x+1）")
ck(gf256.mult(0x57, 0x83) == 0xC1, "0x57·0x83 = 0xC1（FIPS 197 AES 域常量）")
ck(gf256.mult(0x53, gf256.inverse(0x53)) == 1, "inv(0x53)·0x53 = 1")
ck(gf256.inverse(0x53) == 0xCA, "inv(0x53) = 0xCA")
for a in range(1, 256):
    ck(gf256.mult(a, gf256.inverse(a)) == 1, "GF(256) 求逆全枚举 a=%d" % a)
for a in (0x11, 0xAF, 0x02):
    for b in (0x03, 0x80, 0xFF):
        ck(gf256.mult(a, b) == gf256.mult(b, a), "乘法交换律 %02x,%02x" % (a, b))
        ck(gf256.mult(a, 1) == a, "乘 1 不变 %02x" % a)
        ck(gf256.mult(a, 0) == 0, "乘 0 归零 %02x" % a)
        ck(gf256.add(a, b) == (a ^ b), "加法就是 XOR %02x,%02x" % (a, b))
        ck(gf256.add(a, a) == 0, "GF(2^8) 里 a+a = 0（特征 2）" )
ck(gf256.mult(2, 3) == 6, "小数值乘法与直觉一致 2·3=6（未触发约减）")
ck(gf256.div(6, 3) == 2, "除法是乘逆 6/3 = 2")
raises(lambda: gf256.div(1, 0), "Vault 的 div 遇 0 分母会 panic/抛错")
ck(gf256.div(0, 7) == 0, "a=0 时 div 直接返回 0（Vault 用 ConstantTimeSelect 抹平）")

rv = random.Random(11)
vs = gf256.split(b"hello shamir", 5, 3, rv)
ck(all(len(q) == len(b"hello shamir") + gf256.SHARE_OVERHEAD for q in vs),
   "每份份额比秘密长 1 字节（Vault 的 ShareOverhead）")
ck(all(1 <= q[-1] <= 255 for q in vs), "x 落在 [1,255]（Perm(255) 后 +1）")
ck(len({q[-1] for q in vs}) == 5, "各份 x 互不相同")
for comb in itertools.combinations(range(5), 3):
    ck(gf256.combine([vs[i] for i in comb]) == b"hello shamir",
       "Vault 版任意 3 份还原，组合=%s" % (comb,))
ck(gf256.combine([vs[0], vs[2]]) != b"hello shamir", "2 份还原不出（3-of-5）")
ck(gf256.evaluate([9, 4, 7], 0) == 9, "evaluate 对 x=0 直接返回常数项")
raises(lambda: gf256.split(b"x", 5, 1, rv), "threshold < 2 报错")
raises(lambda: gf256.split(b"x", 2, 3, rv), "parts < threshold 报错")
raises(lambda: gf256.split(b"", 3, 2, rv), "空秘密报错")
raises(lambda: gf256.split(b"x", 256, 2, rv), "parts > 255 报错")
raises(lambda: gf256.combine([vs[0], vs[0]]), "重复 x 触发 duplicate part detected")
raises(lambda: gf256.combine([vs[0], vs[0][:-1]]), "份额长度不一致报错")
raises(lambda: gf256.combine([vs[0]]), "少于 2 份报错")

# ==================================================== C. SLIP-0039
ck(slip39.SECRET_INDEX == 255 and slip39.DIGEST_INDEX == 254,
   "秘密在 x=255、摘要在 x=254（SLIP-0039 的取值点约定）")
r39 = random.Random(7)
s39 = slip39.split_secret(3, 5, SECRET16, r39)
ck(len(s39) == 5, "SplitSecret 返回 5 份")
for comb in itertools.combinations(range(5), 3):
    ck(slip39.recover_secret(3, [(i, s39[i]) for i in comb]) == SECRET16,
       "SLIP-0039 任意 3 份还原，组合=%s" % (comb,))
raises(lambda: slip39.recover_secret(3, [(0, s39[0]), (1, s39[1])]),
       "只有 2 份（T=3）→ 摘要校验失败报错")
tam = bytearray(s39[2])
tam[0] ^= 0x01
raises(lambda: slip39.recover_secret(3, [(0, s39[0]), (1, s39[1]), (2, bytes(tam))]),
       "篡改任一份 → 摘要校验失败（这就是 D 的意义）")
ck(slip39.recover_secret(3, [(i, s39[i]) for i in (0, 2, 4)]) == SECRET16,
   "不连续的份额索引也能还原（索引即 x）")
one = slip39.split_secret(1, 3, SECRET16, r39)
ck(all(q == SECRET16 for q in one), "T=1 时每份就是明文（规范第 2 步）")
raises(lambda: slip39.split_secret(3, 5, SECRET16[:15], r39),
       "秘密不足 128 位报错")
raises(lambda: slip39.split_secret(3, 5, SECRET16 + b"\x00", r39),
       "秘密长度不是 16 位的倍数报错")
raises(lambda: slip39.split_secret(3, 17, SECRET16, r39), "N > 16 报错")
raises(lambda: slip39.split_secret(4, 3, SECRET16, r39), "T > N 报错")
# 摘要 D 的结构：D = HMAC(R,S)[:4] || R
pts = [(i, s39[i]) for i in range(3)]
d_hat = slip39.interpolate_at(slip39.DIGEST_INDEX, pts)
s_hat = slip39.interpolate_at(slip39.SECRET_INDEX, pts)
ck(s_hat == SECRET16, "在 x=255 处插值得到秘密")
ck(hmac.new(d_hat[4:], s_hat, hashlib.sha256).digest()[:4] == d_hat[:4],
   "D 的前 4 字节 = HMAC-SHA256(key=R, msg=S) 的前 4 字节")
ck(len(d_hat) == len(SECRET16), "D 与秘密等长（4 字节摘要 + n-4 字节随机数 R）")
# RS1024 校验和
ck(slip39.rs1024_verify_checksum("shamir", [1, 2, 3, 4] +
                                 slip39.rs1024_create_checksum("shamir", [1, 2, 3, 4])),
   "RS1024 生成后立即校验通过")
ck(slip39.rs1024_verify_checksum("shamir", [1, 2, 3, 4, 613, 87, 196]),
   "数据 + 正确校验和整体喂进去才通过（校验和挂在数据之后）")
ck(not slip39.rs1024_verify_checksum("shamir", [1, 2, 3, 4, 613, 87, 197]),
   "校验和末字差 1 即失败")
data = [5, 6, 7, 8]
chk3 = slip39.rs1024_create_checksum("shamir", data)
for k in range(1, 4):
    bad = list(data)
    bad[0] ^= 1 << (k - 1)
    ck(not slip39.rs1024_verify_checksum("shamir", bad + chk3),
       "改 1 个词必被检出（第 %d 种翻转）" % k)
bad3 = [data[0] ^ 1, data[1] ^ 1, data[2] ^ 1, data[3]]
ck(not slip39.rs1024_verify_checksum("shamir", bad3 + chk3),
   "同时改 3 个词仍被检出（规范保证 ≤3 词全检）")
ck(not slip39.rs1024_verify_checksum("shamirx", data + chk3),
   "定制串参与计算：换 cs 后同一份数据校验失败")

print("PASS=%d FAIL=%d" % (PASS, FAIL))
for m in BAD[:12]:
    print("  FAIL:", m)
sys.exit(1 if FAIL else 0)
