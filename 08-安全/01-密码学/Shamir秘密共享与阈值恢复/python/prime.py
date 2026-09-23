"""素数域上的 Shamir 秘密分享（GF(p)，p = 2^31 - 1 梅森素数）。

Shamir 1979 的原始构造就是素数域：秘密 s ∈ GF(p)，随机取 t-1 个系数，
秘密是多项式的常数项 f(0) = s，份额是 (x_i, f(x_i))。
任意 t 份可插值还原；少于 t 份则**对 s 一无所知**（见 forge_share_for_secret）。

p 取 2^31-1 的理由：它是素数，且 Go 的 int64 能容纳 (p-1)^2 ≈ 2^62 而不溢出，
同一份算法可以逐行转写到 go/prime.go 做对拍。
"""

import random

P = 2 ** 31 - 1          # 2147483647，梅森素数


def is_prime_field():
    """教学用途：用小素数试除确认 P 是素数（P 很小，够用）。"""
    if P < 2:
        return False
    d = 2
    while d * d <= P:
        if P % d == 0:
            return False
        d += 1
    return True


def modinv(a, p=P):
    """费马小定理：a^(p-2) ≡ a^(-1) (mod p)。"""
    return pow(a % p, p - 2, p)


def make_poly(secret, degree, rand):
    """系数 [s, a_1, ..., a_degree]，常数项是秘密。"""
    coeffs = [secret % P]
    for _ in range(degree):
        coeffs.append(rand.randrange(1, P))     # 非常数项系数必须非零
    return coeffs


def eval_poly(coeffs, x, p=P):
    """Horner 法求值；x = 0 时直接得秘密。"""
    out = 0
    for c in reversed(coeffs):
        out = (out * x + c) % p
    return out


def split(secret, n, t, rand):
    """t-of-n 拆分，返回 [(x, y)]，x 取 1..n（x = 0 会成为秘密本身，禁止）。"""
    if not (2 <= t <= n < P):
        raise ValueError("需要满足 2 <= t <= n")
    coeffs = make_poly(secret, t - 1, rand)
    return [(x, eval_poly(coeffs, x)) for x in range(1, n + 1)]


def lagrange_basis(shares, i, x=0, p=P):
    """第 i 个拉格朗日基多项式在 x 处的值：Π_{j≠i} (x - x_j)/(x_i - x_j)。"""
    xi, _ = shares[i]
    num, den = 1, 1
    for j, (xj, _) in enumerate(shares):
        if j == i:
            continue
        num = num * (x - xj) % p
        den = den * (xi - xj) % p
    return num * modinv(den, p) % p


def interpolate(shares, x=0, p=P):
    """在 x 处插值；x = 0 得到秘密。"""
    xs = [s[0] for s in shares]
    if len(set(xs)) != len(xs):
        raise ValueError("存在重复的 x，插值无意义")
    total = 0
    for i, (_, yi) in enumerate(shares):
        total = (total + yi * lagrange_basis(shares, i, x, p)) % p
    return total


def recover(shares, t=None, p=P):
    """还原秘密；只取前 t 份（多给也无妨，插值结果唯一）。"""
    if t is not None:
        shares = shares[:t]
    return interpolate(shares, 0, p)


def forge_share_for_secret(shares, x_new, target_secret, p=P):
    """**少于 t 份时零信息泄漏**的构造器。

    已有 t-1 份份额时，对任意目标秘密 s' 都能唯一地造出第 t 份份额，
    使得 t 份一起插值恰好得到 s'。既然每个 s' 都对应一个合法份额，
    攻击者手里的 t-1 份就没有排除任何候选秘密 —— 这就是「信息论安全」。
    """
    pts = list(shares) + [(x_new, 0)]
    base = sum(y * lagrange_basis(pts, i, 0, p)
               for i, (x, y) in enumerate(pts) if x != x_new) % p
    coef = lagrange_basis(pts, len(pts) - 1, 0, p)
    return (target_secret - base) * modinv(coef, p) % p
