"""SLIP-0039 的 SplitSecret / RecoverSecret 与 RS1024 校验和。

与 Vault 版同样在 GF(256)（Rijndael 多项式）里做，但**三处设计不同**：

1. **秘密藏在 x = 255，摘要藏在 x = 254**，份额的索引是 0..N-1。也就是说
   「份额」和「秘密」是同一条多项式的不同取值点，而不是像 Vault 那样把秘密
   放在常数项 f(0)。这样 T=1 可以直接退化成「每人一份明文」。
2. **带摘要 D**：D = HMAC-SHA256(key=R, msg=S) 前 4 字节 ‖ R（R 是 n-4 字节随机数），
   恢复时先插值出 S 与 D，再用 R 验 HMAC —— 于是**拼错份额能被检出**。
3. 份额 ≤ 16、秘密至少 128 位且长度是 16 位的倍数；助记词用 GF(1024) 上的 RS1024
   校验和（3 个 10 位字 = 30 位）保护，能检出任意 ≤3 个词的错误。
"""

import hashlib
import hmac

from gf256 import add, mult, div

SECRET_INDEX = 255
DIGEST_INDEX = 254
MAX_SHARE_COUNT = 16
CUSTOMIZATION_STRING = "shamir"


def interpolate_at(x, points):
    """GF(256) 拉格朗日插值，y 是**字节向量**（SLIP-0039 逐字节独立做一遍）。

    f_k(x) = Σ_i y_i[k] · Π_{j≠i} (x - x_j)/(x_i - x_j)
    由于 GF(2^8) 里减法等于加法，`(x - x_j)` 写成 `add(x, x_j)`。
    """
    n = len(points[0][1])
    out = bytearray(n)
    for i, (xi, yi) in enumerate(points):
        scalar = 1
        for j, (xj, _) in enumerate(points):
            if i == j:
                continue
            scalar = mult(scalar, div(add(x, xj), add(xi, xj)))
        for k in range(n):
            out[k] ^= mult(yi[k], scalar)
    return bytes(out)


def _digest(secret, rand):
    """D = HMAC-SHA256(key=R, msg=S)[:4] || R，长度与秘密同为 n 字节。"""
    r = bytes(rand.randrange(256) for _ in range(len(secret) - 4))
    return hmac.new(r, secret, hashlib.sha256).digest()[:4] + r


def _check_len(secret):
    bits = len(secret) * 8
    if bits < 128 or bits % 16 != 0:
        raise ValueError("secret must be >=128 bits and a multiple of 16 bits")


def split_secret(t, n, secret, rand):
    """SLIP-0039 §SplitSecret。返回 n 份字节串；第 i 份（1-based）索引是 i-1。"""
    if not (0 < t <= n <= MAX_SHARE_COUNT):
        raise ValueError("require 0 < T <= N <= 16")
    _check_len(secret)
    if t == 1:
        return [secret for _ in range(n)]            # T=1：每人一份明文

    d = _digest(secret, rand)
    fixed = [(k, bytes(rand.randrange(256) for _ in range(len(secret))))
             for k in range(t - 2)]                  # (0,y1) … (T-3,y_{T-2})
    base = fixed + [(DIGEST_INDEX, d), (SECRET_INDEX, secret)]
    out = []
    for i in range(1, n + 1):
        if i <= t - 2:
            out.append(fixed[i - 1][1])              # 前 T-2 份本就是随机值
        else:
            out.append(interpolate_at(i - 1, base))
    return out


def recover_secret(t, shares):
    """SLIP-0039 §RecoverSecret：插值出 S 与 D，再用 D 里的 R 验 HMAC。"""
    if t == 1:
        return shares[0][1] if isinstance(shares[0], tuple) else shares[0]
    pts = [(x, y) for x, y in shares]                # [(索引, 份额值)]
    secret = interpolate_at(SECRET_INDEX, pts)
    d = interpolate_at(DIGEST_INDEX, pts)
    r = d[4:]
    if hmac.new(r, secret, hashlib.sha256).digest()[:4] != d[:4]:
        raise ValueError("digest mismatch：份额不属于同一次拆分")
    return secret


# --------------------------------------------------------- RS1024 校验和
# 生成多项式 (x-a)(x-a^2)(x-a^3)，a 是 GF(2) 上本原多项式 x^10+x^3+1 的根
GEN = [0xE0E040, 0x1C1C080, 0x3838100, 0x7070200, 0xE0E0009,
       0x1C0C2412, 0x38086C24, 0x3090FC48, 0x21B1F890, 0x3F3F120]


def rs1024_polymod(values):
    chk = 1
    for v in values:
        b = chk >> 20
        chk = (chk & 0xFFFFF) << 10 ^ v
        for i in range(10):
            chk ^= GEN[i] if ((b >> i) & 1) else 0
    return chk


def rs1024_create_checksum(cs, data):
    polymod = rs1024_polymod([ord(c) for c in cs] + list(data) + [0, 0, 0]) ^ 1
    return [(polymod >> 10 * (2 - i)) & 1023 for i in range(3)]


def rs1024_verify_checksum(cs, data):
    return rs1024_polymod([ord(c) for c in cs] + list(data)) == 1
