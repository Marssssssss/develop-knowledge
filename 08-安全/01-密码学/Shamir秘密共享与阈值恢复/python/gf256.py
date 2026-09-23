"""GF(2^8) 上的 Shamir 分享 —— 复刻 HashiCorp Vault `shamir/shamir.go`。

与素数域版的三个实质差别：

1. **域运算不同**：不可约多项式取 Rijndael 多项式 x^8+x^4+x^3+x+1（低 8 位 0x1B），
   加法即 XOR（故减法等于加法），乘除法用位串行的 `mult` 与 a^254 求逆。
2. **每个字节一条多项式**：GF(256) 只有 256 个元素，一个多项式只能承载 1 字节秘密，
   所以 n 字节的秘密要拆成 n 条独立多项式（Vault 的注释原话）。
3. **份额布局是 {y_1..y_n, x}**：x 只存一份、挂在末尾（`ShareOverhead = 1`），
   且 x 取自 [1,255]（Vault 用 `mathrand.Perm(255)` 后再 +1，x=0 会直接给出秘密）。
"""

import random

IRRED = 0x1B          # x^8+x^4+x^3+x+1 的低 8 位（AES / Rijndael 多项式）
SHARE_OVERHEAD = 1    # Vault 的 ShareOverhead：份额比秘密多 1 字节（存 x）


def add(a, b):
    """GF(2^8) 加法 = 减法 = XOR。"""
    return a ^ b


def mult(a, b):
    """Vault 的位串行乘法，逐位展开的原文是：
       r = (-(b>>i & 1) & a) ^ (-(r>>7) & 0x1B) ^ (r + r)
    其中 `r+r` 就是左移一位，`-(r>>7) & 0x1B` 是溢出时的模约减。"""
    r = 0
    for i in range(7, -1, -1):
        r = ((-((b >> i) & 1)) & a) ^ ((-(r >> 7)) & IRRED) ^ ((r + r) & 0xFF)
    return r & 0xFF


def inverse(a):
    """Vault 的求逆链：连乘 11 次得到 a^254 = a^(-1)（因为域的阶是 255）。"""
    b = mult(a, a)          # a^2
    c = mult(a, b)          # a^3
    b = mult(c, c)          # a^6
    b = mult(b, b)          # a^12
    c = mult(b, c)          # a^15
    b = mult(b, b)          # a^24
    b = mult(b, b)          # a^48
    b = mult(b, c)          # a^63
    b = mult(b, b)          # a^126
    b = mult(a, b)          # a^127
    return mult(b, b)       # a^254


def div(a, b):
    """Vault 里 b=0 会 panic；a=0 时用 ConstantTimeSelect 抹平成 0（防计时）。"""
    if b == 0:
        raise ZeroDivisionError("divide by zero")
    if a == 0:
        return 0
    return mult(a, inverse(b))


def make_polynomial(intercept, degree, rand):
    """coefficients[0] 固定为秘密（字节），其余随机。"""
    return [intercept] + [rand.randrange(256) for _ in range(degree)]


def evaluate(coeffs, x):
    """Vault 的 Horner 实现：x == 0 时直接返回常数项。**从不把 x=0 当份额**。"""
    if x == 0:
        return coeffs[0]
    out = coeffs[-1]
    for i in range(len(coeffs) - 2, -1, -1):
        out = add(mult(out, x), coeffs[i])
    return out


def interpolate(xs, ys, x):
    """Vault 的 interpolatePolynomial：拉格朗日在 x 处的值（GF(256) 里减法=加法）。"""
    limit = len(xs)
    result = 0
    for i in range(limit):
        basis = 1
        for j in range(limit):
            if i == j:
                continue
            num = add(x, xs[j])
            denom = add(xs[i], xs[j])
            basis = mult(basis, div(num, denom))
        result = add(result, mult(ys[i], basis))
    return result


def split(secret, parts, threshold, rand):
    """Vault 的 Split：每字节一条多项式，份额 = y_1..y_n 再附一个 x 字节。"""
    if parts < threshold:
        raise ValueError("parts cannot be less than threshold")
    if parts > 255:
        raise ValueError("parts cannot exceed 255")
    if threshold < 2:
        raise ValueError("threshold must be at least 2")
    if threshold > 255:
        raise ValueError("threshold cannot exceed 255")
    if len(secret) == 0:
        raise ValueError("cannot split an empty secret")

    xs = rand.sample(range(1, 256), parts)      # Vault: Perm(255) 后 +1，x ∈ [1,255]
    out = [bytearray(len(secret) + SHARE_OVERHEAD) for _ in range(parts)]
    for i in range(parts):
        out[i][len(secret)] = xs[i]
    for idx, val in enumerate(secret):
        p = make_polynomial(val, threshold - 1, rand)
        for i in range(parts):
            out[i][idx] = evaluate(p, xs[i])
    return [bytes(s) for s in out]


def combine(parts):
    """Vault 的 Combine：长度必须一致、x 不得重复，逐字节插值回 x=0。"""
    if len(parts) < 2:
        raise ValueError("less than two parts cannot be used to reconstruct")
    n = len(parts[0])
    if n < 2:
        raise ValueError("parts must be at least two bytes")
    if any(len(q) != n for q in parts):
        raise ValueError("all parts must be the same length")
    xs = [q[n - 1] for q in parts]
    if len(set(xs)) != len(xs):
        raise ValueError("duplicate part detected")

    secret = bytearray(n - 1)
    for idx in range(n - 1):
        secret[idx] = interpolate(xs, [q[idx] for q in parts], 0)
    return bytes(secret)


def rand_bytes(k, rand):
    return bytes(rand.randrange(256) for _ in range(k))
