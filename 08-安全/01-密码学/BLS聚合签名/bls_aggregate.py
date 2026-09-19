# -*- coding: utf-8 -*-
"""BLS 聚合签名（Boneh-Lynn-Shacham）—— 在一条**玩具超奇异曲线**上跑真实配对。

生产系统用 BLS12-381（嵌入次数 12、254 位群阶），Miller 循环要几百次 F_p 运算，
作为 demo 读起来全是工程细节。这里换成嵌入次数 2 的超奇异曲线：

    E: y^2 = x^3 + x   over F_p，p ≡ 3 (mod 4)  ⇒  超奇异，#E = p+1
    p = 16252507, r = 4063127, h = 4   （p+1 = 4·r，r 为素数）

配对走**改造 Tate 配对** + 畸变映射：

    ψ(x, y) = (-x, i·y)      i^2 = -1 ∈ F_p^2      （把 E(F_p) 的点搬到 E(F_p^2)）
    e(P, Q) = f_{r,P}(ψ(Q))^((p^2-1)/r)            （最终幂 = (p-1)·h）

算法（Miller 循环、切线/弦线求值、最终幂）与真实 BLS12-381 完全同构，
只是参数小到能一眼看懂。签名/验签流程按 IETF draft-irtf-cfrg-bls-signature 的
CoreSign / CoreVerify / Aggregate / CoreAggregateVerify 定义实现。
"""

import hashlib

P_MOD = 16252507
R = 4063127          # 素数群阶
H_COF = 4            # p+1 = h·r
FINAL_EXP = (P_MOD - 1) * H_COF   # (p^2-1)/r = (p-1)·h

# ---------------------------------------------------------------- F_p
def _inv(a):
    return pow(a % P_MOD, P_MOD - 2, P_MOD)


# ---------------------------------------------------------------- E(F_p)
def pt_add(A, B):
    if A is None:
        return B
    if B is None:
        return A
    x1, y1 = A
    x2, y2 = B
    if x1 == x2 and (y1 + y2) % P_MOD == 0:
        return None                       # 互逆 ⇒ 无穷远点
    if A == B:
        lam = (3 * x1 * x1 + 1) * _inv(2 * y1) % P_MOD
    else:
        lam = (y2 - y1) * _inv(x2 - x1) % P_MOD
    x = (lam * lam - x1 - x2) % P_MOD
    return (x, (lam * (x1 - x) - y1) % P_MOD)


def pt_mul(k, A):
    k %= R
    T = None
    while k:
        if k & 1:
            T = pt_add(T, A)
        A = pt_add(A, A)
        k >>= 1
    return T


def pt_neg(A):
    return None if A is None else (A[0], (-A[1]) % P_MOD)


def on_curve(A):
    return A is None or (A[1] * A[1] - A[0] ** 3 - A[0]) % P_MOD == 0


# ---------------------------------------------------------------- F_p^2
def f2_mul(a, b):
    return ((a[0] * b[0] - a[1] * b[1]) % P_MOD, (a[0] * b[1] + a[1] * b[0]) % P_MOD)


def f2_inv(a):
    n = _inv((a[0] * a[0] + a[1] * a[1]) % P_MOD)
    return (a[0] * n % P_MOD, -a[1] * n % P_MOD)


def f2_pow(a, e):
    out, base = (1, 0), a
    while e:
        if e & 1:
            out = f2_mul(out, base)
        base = f2_mul(base, base)
        e >>= 1
    return out


# ---------------------------------------------------------------- 配对
def _psi(A):
    """畸变映射 ψ(x,y) = (-x, i·y)：把 G1 的点搬到 E(F_p^2)，两个自变量的“来源”分开"""
    return ((-A[0]) % P_MOD, 0), (0, A[1])


def _miller(A, Q, r):
    """计算 f_{r,A}(Q)，Q 的坐标在 F_p^2 里"""
    T, f = A, (1, 0)
    for bit in bin(r)[3:]:                       # 跳过最高位的 1
        if T is None:
            break
        xT, yT = T
        if yT == 0:                              # 2-挠点：切线退化
            T = pt_add(T, T)
            continue
        lam = (3 * xT * xT + 1) * _inv(2 * yT) % P_MOD
        # 切线在 Q 处的取值：(y_Q - y_T) - λ(x_Q - x_T)
        l = ((Q[1][0] - yT - lam * (Q[0][0] - xT)) % P_MOD,
             (Q[1][1] - lam * Q[0][1]) % P_MOD)
        T2 = pt_add(T, T)
        v = ((Q[0][0] - T2[0]) % P_MOD, Q[0][1]) if T2 else (1, 0)
        f = f2_mul(f2_mul(f2_mul(f, f), l), f2_inv(v))   # f ← f²·l/v
        T = T2
        if bit == "1":
            TP = pt_add(T, A) if T else A
            if TP is None:                       # T = -A：只剩垂直线
                l = ((Q[0][0] - A[0]) % P_MOD, Q[0][1])
                v = (1, 0)
            else:
                if T != A:
                    lam = (A[1] - T[1]) * _inv(A[0] - T[0]) % P_MOD
                else:
                    lam = (3 * T[0] * T[0] + 1) * _inv(2 * T[1]) % P_MOD
                l = ((Q[1][0] - T[1] - lam * (Q[0][0] - T[0])) % P_MOD,
                     (Q[1][1] - lam * Q[0][1]) % P_MOD)
                v = ((Q[0][0] - TP[0]) % P_MOD, Q[0][1])
            f = f2_mul(f2_mul(f, l), f2_inv(v))
            T = TP
    return f


def pairing(A, B):
    """e(A, B) ∈ μ_r ⊂ F_p^2*"""
    return f2_pow(_miller(A, _psi(B), R), FINAL_EXP)


# ---------------------------------------------------------------- hash-to-point
def hash_to_point(msg):
    """try-and-increment：真实实现要走 RFC 9380 的 hash_to_curve（含共因子清乘）"""
    ctr = 0
    while True:
        h = hashlib.sha256(msg + b"|" + str(ctr).encode()).digest()
        x = int.from_bytes(h, "big") % P_MOD
        v = (x ** 3 + x) % P_MOD
        if pow(v, (P_MOD - 1) // 2, P_MOD) == 1:
            y = pow(v, (P_MOD + 1) // 4, P_MOD)      # p ≡ 3 (mod 4) 的开方捷径
            return pt_mul(H_COF, (x, y))             # 乘共因子清到 r 阶子群
        ctr += 1


def _generator():
    for x in range(1, 500):
        v = (x ** 3 + x) % P_MOD
        if pow(v, (P_MOD - 1) // 2, P_MOD) == 1:
            y = pow(v, (P_MOD + 1) // 4, P_MOD)
            cand = pt_mul(H_COF, (x, y))
            if cand and pt_mul(R, cand) is None and pt_mul(R // 2, cand) is not None:
                return cand
    raise RuntimeError("未找到生成元")


G = _generator()

# ---------------------------------------------------------------- BLS
def keygen(sk):
    return pt_mul(sk, G)


def core_sign(sk, msg):
    """CoreSign：Q = hash_to_point(m)；R = SK·Q"""
    return pt_mul(sk, hash_to_point(msg))


def core_verify(pk, msg, sig):
    """CoreVerify：e(H(m), PK) == e(σ, G)"""
    if pk is None or sig is None:
        return False
    return pairing(hash_to_point(msg), pk) == pairing(sig, G)


def aggregate(sigs):
    out = None
    for s in sigs:
        out = pt_add(out, s)
    return out


def core_aggregate_verify(pks, msgs, sig):
    """CoreAggregateVerify：e(σ, G) == Π e(H(m_i), PK_i) —— 不做任何重复消息检查"""
    lhs = pairing(sig, G)
    rhs = (1, 0)
    for pk, m in zip(pks, msgs):
        rhs = f2_mul(rhs, pairing(hash_to_point(m), pk))
    return lhs == rhs


def aggregate_verify(pks, msgs, sig):
    """Basic scheme 的 AggregateVerify：先强制消息互不相同，再调 CoreAggregateVerify"""
    if len(set(msgs)) != len(msgs):
        return False
    return core_aggregate_verify(pks, msgs, sig)


def augment_sign(sk, pk, msg):
    """Message augmentation：签的是 (PK || m)，天然让不同签名者的消息互不相同"""
    return core_sign(sk, pk_bytes(pk) + b"||" + msg)


def augment_verify(pks, msgs, sig):
    rhs = (1, 0)
    for pk, m in zip(pks, msgs):
        rhs = f2_mul(rhs, pairing(hash_to_point(pk_bytes(pk) + b"||" + m), pk))
    return pairing(sig, G) == rhs


def pk_bytes(pk):
    return b"".join(c.to_bytes(4, "big") for c in (pk or (0, 0)))
