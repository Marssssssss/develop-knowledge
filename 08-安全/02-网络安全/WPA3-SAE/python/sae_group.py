"""
Dragonfly 的有限域（MODP）群运算与密码元素推导（RFC 7664 §2.2 / §3.2）

群：RFC 3526 的 2048 位 MODP Group（id 14），p = 2^2048 - 2^1984 - 1 + 2^64*{[2^1918 pi] + 124476}，
生成元 G = 2，子群阶 q = (p-1)/2（p 是安全素数）。

为什么这个 demo 用 MODP 而不是 ECC：WPA3-Personal 的 SAE 实际部署主要跑椭圆曲线群，
但 RFC 7664 同时定义了 ECC 与 MODP 两条路径，而 MODP 路径**只需要大整数模幂**，
不用写点运算/求平方根，能在同样篇幅里把「狩猎与啄食」「提交/确认」讲清楚。
两侧的协议结构完全一样，换群只换 §2.1/§2.2 的算子。

hunting-and-pecking 里有个容易忽略的点：RFC 7664 要求**至少**迭代 k 次，
目的是不泄漏「找到 PE 用了几轮」这个侧信道（见 §3.2 的说明）。
"""

from __future__ import annotations

import hashlib
import hmac

# RFC 3526 §3（2048-bit MODP Group，id 14）的素数十六进制原文
P_HEX = ("FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74"
         "020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437"
         "4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
         "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05"
         "98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB"
         "9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
         "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718"
         "3995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF")

P = int(P_HEX, 16)
G = 2
Q = (P - 1) // 2
P_BYTES = (P.bit_length() + 7) // 8

PWE_LABEL = b"Dragonfly Hunting And Pecking"
KEY_LABEL = b"Dragonfly Key Derivation"


def h(data: bytes) -> bytes:
    """RFC 7664 把单向函数 H 留给具体协议；WPA3-SAE 用 SHA-256。"""
    return hashlib.sha256(data).digest()


# ------------------------------------------------------------
# 1. 群运算（FFC）
# ------------------------------------------------------------

def scalar_op(x: int, element: int) -> int:
    """Z = scalar-op(x, Y) = Y^x mod p"""
    return pow(element, x, P)


def element_op(a: int, b: int) -> int:
    """Z = element-op(X, Y) = X*Y mod p"""
    return (a * b) % P


def inverse(element: int) -> int:
    """R * inverse(R) mod p = 1"""
    return pow(element, P - 2, P)


def is_valid_element(element: int) -> bool:
    """RFC 7664 §2.2：1 < element < p-1，且 element^q mod p == 1（在阶为 q 的子群内）。"""
    if not 1 < element < P - 1:
        return False
    return pow(element, Q, P) == 1


# ------------------------------------------------------------
# 2. KDF（HKDF-SHA256 的 Expand，标签作为 info）
# ------------------------------------------------------------

def kdf(label: bytes, ikm: bytes, n_bits: int) -> int:
    """输出 n_bits 位的伪随机串，返回其整数值。

    RFC 7664 只说「KDF-n」，没规定具体构造（这正是跨实现必须对齐的第一件事）。
    这里用 HKDF-SHA256：PRK = HMAC(zero_salt, ikm)，再按 info=label 展开。
    """
    prk = hmac.new(b"\x00" * 32, ikm, hashlib.sha256).digest()
    out, prev, counter = b"", b"", 1
    n_bytes = (n_bits + 7) // 8
    while len(out) < n_bytes:
        prev = hmac.new(prk, prev + label + bytes([counter]), hashlib.sha256).digest()
        out += prev
        counter += 1
    return int.from_bytes(out[:n_bytes], "big")


# ------------------------------------------------------------
# 3. 密码元素（PWE）—— 狩猎与啄食
# ------------------------------------------------------------

def hunting_and_pecking(password: bytes, id_a: bytes, id_b: bytes, k: int = 40,
                        nonce: bytes = b"") -> tuple[int, int]:
    """返回 (PE, 实际迭代次数)。

    身份用 max/min 排序后拼接，这样双方各自视角算出的 PE 相同 ——
    这是 Dragonfly 能做成对等（无固定角色）交换的关键。
    """
    hi, lo = max(id_a, id_b), min(id_a, id_b)
    n_bits = P.bit_length() + 64          # 多取 64 位，降低模约减的偏置
    pe, iterations = None, 0
    counter = 1
    while True:
        base = h(hi + lo + password + nonce + bytes([counter]))
        seed = (kdf(PWE_LABEL, base, n_bits) % (P - 1)) + 1
        temp = scalar_op((P - 1) // Q, seed)      # 安全素数下就是 seed^2 mod p
        if temp > 1 and pe is None:
            pe = temp
        iterations += 1
        counter += 1
        if pe is not None and iterations >= k:
            # 找到之后仍然继续跑，直到凑满 k 次，掩盖真实迭代数（防侧信道）
            return pe, iterations


def derive_pwe(password: bytes, id_a: bytes, id_b: bytes, k: int = 40,
               nonce: bytes = b"") -> int:
    return hunting_and_pecking(password, id_a, id_b, k, nonce)[0]


# ------------------------------------------------------------
# 4. 素数性自检（Miller-Rabin，仅用于验证群参数）
# ------------------------------------------------------------

def is_probable_prime(n: int, rounds: int = 12) -> bool:
    if n < 2:
        return False
    for small in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % small == 0:
            return n == small
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True
