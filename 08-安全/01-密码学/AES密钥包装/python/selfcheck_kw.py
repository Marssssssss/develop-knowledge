"""AES 密钥包装自检：RFC 3394（AES-KW）与 RFC 5649（AES-KWP）。

断言风格：
  * 分组密码与包装结果对拍的是 **RFC 原文 §4 逐字抽取** 的向量（kw_vectors.py）
  * 索引式实现额外与 RFC §2.2.1/§2.2.2 的**寄存器移位式**描述对拍（两种规范表述）
  * 完整性检查用「漏报集 / 误报集」成对断言：篡改必拒、原封必收
  * 标记 [经验探针] 的断言是统计性而非可证的，已在注释里写明
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aes_core import (SBOX, INV_SBOX, gmul, xtime, key_expansion,
                      encrypt_block, decrypt_block)
from kw import (aes_wrap, aes_unwrap, kwp_wrap, kwp_unwrap, unwrap_core,
                KeyWrapError, DEFAULT_IV, AIV_CONST)
from kw_vectors import RFC3394, RFC3394_ENC
from fips197_vectors import FIPS197

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


def unhex(s):
    return bytes.fromhex(s)


# ============================================================ A. GF(2^8) 与 S-box
ck(sorted(SBOX) == list(range(256)), "SBOX 是 0..255 的一个排列")
for x in range(256):
    ck(INV_SBOX[SBOX[x]] == x, "INV_SBOX o SBOX = id @ %02x" % x)
# 以下三式由定义直接推出，不依赖任何记忆中的表项
ck(SBOX[0] == 0x63, "SBOX[0] = affine(inv(0)) ^ 0x63 = 0x63")
ck(SBOX[1] == 0x7C, "SBOX[1] = affine(1) ^ 0x63 = 0x7c")
ck(xtime(0x80) == 0x1B, "xtime(0x80) = 0x1b（0x100 约去 0x11b）")
ck(xtime(0x01) == 0x02 and xtime(0x57) == 0xAE, "xtime 线性段")
for a in (0x00, 0x01, 0x53, 0xFF, 0x11):
    ck(gmul(a, 1) == a and gmul(1, a) == a, "gmul 单位元 @ %02x" % a)
    ck(gmul(a, 0) == 0, "gmul 零元 @ %02x" % a)
ck(gmul(3, 3) == 5, "gmul(3,3) = (x+1)^2 = x^2+1 = 5")
ck(gmul(2, 0x80) == 0x1B, "gmul(2,0x80) = xtime(0x80)")
for a in range(0, 256, 17):
    for b in range(0, 256, 29):
        ck(gmul(a, b) == gmul(b, a), "gmul 交换律 %02x,%02x" % (a, b))

# 密钥扩展长度：Nr = Nk + 6
for klen, nk in ((16, 4), (24, 6), (32, 8)):
    w, nr = key_expansion(bytes(range(klen)))
    ck(len(w) == 4 * (nr + 1), "w 长度 = 4(Nr+1)，Nk=%d" % nk)
    ck(nr == nk + 6, "Nr = Nk + 6，Nk=%d" % nk)

# ============================================================ A2. FIPS 197 附录 C
for e in FIPS197:
    key, pt, want = unhex(e["key"]), unhex(e["pt"]), unhex(e["ct"])
    bits = len(key) * 8
    ck(encrypt_block(key, pt) == want,
       "FIPS197 C AES-%d 期望 %s 实得 %s" % (bits, e["ct"], encrypt_block(key, pt).hex()))
    ck(decrypt_block(key, want) == pt, "FIPS197 C AES-%d 解密往返" % bits)
    tr = []
    encrypt_block(key, pt, trace=tr)
    seen = {}
    for rnd, name, val in tr:
        seen.setdefault(rnd, {})[name] = val.hex()
    for r, rec in enumerate(e["rounds"]):
        for name in ("start", "s_box", "s_row", "m_col", "k_sch"):
            exp = rec.get(name)
            if exp is None:
                ck(name not in seen.get(r, {}),
                   "FIPS197 AES-%d round%d 不应有 %s" % (bits, r, name))
                continue
            ck(seen.get(r, {}).get(name) == exp,
               "FIPS197 AES-%d round%d %s 期望 %s 实得 %s"
               % (bits, r, name, exp, seen.get(r, {}).get(name)))

# ============================================================ B. AES 分组对拍
for entry in RFC3394_ENC:
    kek = unhex(entry["kek"])
    for idx, (pin, pout) in enumerate(entry["blocks"]):
        got = encrypt_block(kek, unhex(pin))
        ck(got == unhex(pout),
           "AES-%d block #%d 期望 %s 实得 %s" % (len(kek) * 8, idx, pout, got.hex()))
        ck(decrypt_block(kek, got) == unhex(pin),
           "AES-%d 分组解密往返 #%d" % (len(kek) * 8, idx))

# ============================================================ C. RFC 3394 包装
def ref_wrap_shift(kek, ptext, iv=DEFAULT_IV):
    """RFC 3394 §2.2.1 的寄存器移位式表述（非索引式），用于对拍。"""
    n = len(ptext) // 8
    a = iv
    r = [int.from_bytes(ptext[8 * i:8 * i + 8], "big") for i in range(n)]
    s = 6 * n
    for t in range(1, s + 1):
        b = encrypt_block(kek, a.to_bytes(8, "big") + r[0].to_bytes(8, "big"))
        a = int.from_bytes(b[:8], "big") ^ t
        r = r[1:] + [int.from_bytes(b[8:], "big")]
    return a.to_bytes(8, "big") + b"".join(x.to_bytes(8, "big") for x in r)


def ref_unwrap_shift(kek, ctext, iv=DEFAULT_IV):
    """RFC 3394 §2.2.2 的寄存器移位式表述。"""
    n = len(ctext) // 8 - 1
    a = int.from_bytes(ctext[:8], "big")
    r = [int.from_bytes(ctext[8 * i:8 * i + 8], "big") for i in range(1, n + 1)]
    s = 6 * n
    for t in range(s, 0, -1):
        b = decrypt_block(kek, (a ^ t).to_bytes(8, "big") + r[n - 1].to_bytes(8, "big"))
        a = int.from_bytes(b[:8], "big")
        r = [int.from_bytes(b[8:], "big")] + r[:n - 1]
    if a != iv:
        raise KeyWrapError("ref: A != IV")
    return b"".join(x.to_bytes(8, "big") for x in r)


for v in RFC3394:
    kek, ptext, want = unhex(v["kek"]), unhex(v["key"]), unhex(v["ct"])
    got = aes_wrap(kek, ptext)
    ck(got == want, "RFC3394 wrap 期望 %s 实得 %s" % (want.hex(), got.hex()))
    ck(len(got) == len(ptext) + 8, "密文比明文多一个 64 位块")
    ck(ref_wrap_shift(kek, ptext) == want, "移位式表述与索引式结果一致")
    ck(aes_unwrap(kek, got) == ptext, "RFC3394 unwrap 往返")
    ck(ref_unwrap_shift(kek, want) == ptext, "移位式解包往返")

# ============================================================ D. 漏报集 / 误报集
for v in RFC3394:
    kek, ctext = unhex(v["kek"]), unhex(v["ct"])
    try:
        aes_unwrap(kek, ctext)
        ck(True, "误报集：原文必须被接受")
    except KeyWrapError as e:
        ck(False, "误报集：原文被误拒 %s" % e)
    for bit in range(len(ctext) * 8):
        mut = bytearray(ctext)
        mut[bit // 8] ^= 0x80 >> (bit % 8)
        mut = bytes(mut)
        ck(mut != ctext, "位翻转确实生效 @%d" % bit)
        try:
            aes_unwrap(kek, mut)
            ck(False, "漏报集：位 %d 篡改后仍被接受" % bit)
        except KeyWrapError:
            ck(True, "漏报集：位 %d 篡改被拒" % bit)

# 用错 KEK 也必须拒（换 KEK 是另一类篡改）
for v in RFC3394:
    ctext = unhex(v["ct"])
    wrong = bytes((b ^ 0x5A) for b in unhex(v["kek"]))
    try:
        aes_unwrap(wrong, ctext)
        ck(False, "错误 KEK 被接受")
    except KeyWrapError:
        ck(True, "错误 KEK 被拒")

# ============================================================ E. 支持集探针
# [经验探针] 固定 K 时 C[0] 应当依赖每一个明文块；碰撞概率 ~2^-64，不可证。
probe = RFC3394[5]
kek_p, pt_p, ct_p = unhex(probe["kek"]), unhex(probe["key"]), unhex(probe["ct"])
n_p = len(pt_p) // 8
for i in range(n_p):
    for bit in (0, 7, 63):
        mut = bytearray(pt_p)
        mut[8 * i + bit // 8] ^= 0x80 >> (bit % 8)
        c2 = aes_wrap(kek_p, bytes(mut))
        ck(c2[:8] != ct_p[:8], "C[0] 依赖 P[%d] 的位 %d" % (i + 1, bit))
        ck(aes_unwrap(kek_p, c2) == bytes(mut), "改过的明文仍可往返")

# ============================================================ F. RFC 5649 向量
RFC5649 = [
    dict(kek="5840df6e29b02af1ab493b705bf16ea1ae8338f4dcc176a8",
         key="c37b7e6492584340bed12207808941155068f738",
         ct="138bdeaa9b8fa7fc61f97742e72248ee5ae6ae5360d1ae6a"
            "5f54f373fa543b6a"),
    dict(kek="5840df6e29b02af1ab493b705bf16ea1ae8338f4dcc176a8",
         key="466f7250617369",
         ct="afbeb0f07dfbf5419200f2ccb50bb24f"),
]
for v in RFC5649:
    kek, ptext, want = unhex(v["kek"]), unhex(v["key"]), unhex(v["ct"])
    got = kwp_wrap(kek, ptext)
    ck(got == want, "RFC5649 wrap 期望 %s 实得 %s" % (want.hex(), got.hex()))
    ck(kwp_unwrap(kek, want) == ptext, "RFC5649 unwrap 往返")
    m = len(ptext)
    ck(len(got) == 8 * ((m + 7) // 8 + 1), "KWP 密文长度 = 8*(ceil(m/8)+1)")
    a, padded = unwrap_core(kek, want)
    ck((a >> 32) == AIV_CONST, "A 高 32 位 = A65959A6")
    ck((a & 0xFFFFFFFF) == m, "A 低 32 位 = MLI = %d" % m)
    ck(padded[m:] == b"\x00" * (len(padded) - m), "补齐字节全零")
    ck(padded[:m] == ptext, "补齐前部即原文")

# 第二个向量是 n == 1 的单块 ECB 分支
ck(len(RFC5649[1]["ct"]) // 2 == 16, "7 字节明文 -> 恰好 2 个 64 位块（n=1）")
ck(len(RFC5649[0]["ct"]) // 2 == 32, "20 字节明文 -> 24 字节补齐 -> n=3")

# ============================================================ G. AIV 三检查配对
KEK_G = unhex("000102030405060708090A0B0C0D0E0F")


def make(m, iv, pad=None):
    """构造一个 n=2 的伪造 KWP 包装体：A 与补齐字节由调用方指定。"""
    body = bytearray((b"\x11" * 64)[:m] + b"\x00" * (-m % 8))
    if pad:
        body[m:] = pad[:len(body) - m]
    return aes_wrap(KEK_G, bytes(body), iv=iv)


# 配对 1：MSB 正确 vs 错误
good_iv = (AIV_CONST << 32) | 16
bad_iv = (0x11223344 << 32) | 16
ck(kwp_unwrap(KEK_G, make(16, good_iv)) == b"\x11" * 16, "检查1 阳性：MSB 正确")
try:
    kwp_unwrap(KEK_G, make(16, bad_iv))
    ck(False, "检查1 应拒绝 MSB != A65959A6")
except KeyWrapError as e:
    ck("AIV check 1" in str(e), "检查1 拒绝，原因=%s" % e)

# 配对 2：MLI 在 (8(n-1), 8n] 内 vs 越界（17 > 8*2）
ck(kwp_unwrap(KEK_G, make(15, (AIV_CONST << 32) | 15, pad=b"\x00")) == b"\x11" * 15,
   "检查2 阳性：MLI=15，n=2，8 < 15 <= 16")
for bad_mli in (0, 8, 17, 0xFFFFFFFF):
    try:
        kwp_unwrap(KEK_G, make(15, (AIV_CONST << 32) | bad_mli))
        ck(False, "检查2 应拒绝 MLI=%d" % bad_mli)
    except KeyWrapError as e:
        ck("AIV check 2" in str(e), "检查2 拒绝 MLI=%d" % bad_mli)

# 配对 3：补齐字节为零 vs 非零（同一 MLI=15，只差最后一个字节）
ck(kwp_unwrap(KEK_G, make(15, (AIV_CONST << 32) | 15, pad=b"\x00")) == b"\x11" * 15,
   "检查3 阳性：补齐字节为零")
try:
    kwp_unwrap(KEK_G, make(15, (AIV_CONST << 32) | 15, pad=b"\x01"))
    ck(False, "检查3 应拒绝非零补齐")
except KeyWrapError as e:
    ck("AIV check 3" in str(e), "检查3 拒绝非零补齐，原因=%s" % e)

# n=1 分支同样走三条检查
try:
    kwp_unwrap(KEK_G, encrypt_block(KEK_G, ((0x11223344 << 32) | 7).to_bytes(8, "big")
                                    + b"\x11" * 7 + b"\x00"))
    ck(False, "n=1 分支应拒绝错误 AIV")
except KeyWrapError:
    ck(True, "n=1 分支拒绝错误 AIV")

# ============================================================ H. KWP 扫描
for klen in (16, 24, 32):
    k = bytes(range(klen))
    for m in range(1, 41):
        pt = bytes((i * 7 + 3) & 0xFF for i in range(m))
        c = kwp_wrap(k, pt)
        ck(len(c) == 8 * ((m + 7) // 8 + 1), "KWP 长度 m=%d k=%d" % (m, klen))
        ck(kwp_unwrap(k, c) == pt, "KWP 往返 m=%d k=%d" % (m, klen))
    # 长度阶梯：m=8 走 n=1（16 字节），m=9 走 n=2（24 字节）
    ck(len(kwp_wrap(k, b"a" * 8)) == 16, "m=8 -> n=1 -> 16 字节")
    ck(len(kwp_wrap(k, b"a" * 9)) == 24, "m=9 -> n=2 -> 24 字节")

# 边界与错误路径
try:
    kwp_wrap(KEK_G, b"")
    ck(False, "空明文应报错")
except ValueError:
    ck(True, "空明文报错")
try:
    aes_wrap(KEK_G, b"a" * 8)
    ck(False, "RFC 3394 要求 n >= 2")
except ValueError:
    ck(True, "RFC 3394 拒绝 n < 2")
try:
    aes_unwrap(KEK_G, b"a" * 16)
    ck(False, "n=1 的 RFC 3394 密文应被拒")
except ValueError:
    ck(True, "RFC 3394 拒绝 n+1 < 3")

print("PASS=%d FAIL=%d" % (PASS, FAIL))
for m in BAD[:20]:
    print("  FAIL:", m)
sys.exit(1 if FAIL else 0)
