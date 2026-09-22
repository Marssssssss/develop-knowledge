#!/usr/bin/env python3
"""NIST P-256 上的 ECDSA：纯 Python 实现（签名用 RFC 6979 确定性 nonce）。

只为一件事服务：把 **DSSE 官方测试向量**逐字节复现出来。
DSSE protocol.md 给出的向量是：

    payload     = b"hello world"
    payloadType = "http://example.com/HelloWorld"
    PAE         = b"DSSEv1 29 http://example.com/HelloWorld 11 hello world"
    算法         = ECDSA over NIST P-256 and SHA-256, deterministic-rfc6979
    签名编码     = r 与 s 的原始拼接（各 32 字节大端）
    X/Y/d       = 见 DSSE_VECTOR

如果本文件的签名实现与官方向量**不完全一致**，说明实现错了，
自检里的 C1 会立刻失败。
"""

import hashlib
import hmac

# ---------------------------------------------------------------- P-256 参数

P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
A = 0xffffffff00000001000000000000000000000000fffffffffffffffffffffffc  # -3 mod p
B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
GX = 0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296
GY = 0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
QLEN = N.bit_length()          # 256
RLEN = (QLEN + 7) // 8         # 32

# ---------------------------------------------------------------- 点运算


def is_on_curve(pt):
    if pt is None:
        return False
    x, y = pt
    return (y * y - (x * x * x + A * x + B)) % P == 0


def point_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None                       # 无穷远点
    if p1 == p2:
        lam = (3 * x1 * x1 + A) * pow(2 * y1 % P, P - 2, P) % P
    else:
        lam = (y2 - y1) * pow((x2 - x1) % P, P - 2, P) % P
    x3 = (lam * lam - x1 - x2) % P
    y3 = (lam * (x1 - x3) - y1) % P
    return (x3, y3)


def point_mul(pt, k):
    k %= N
    result = None
    addend = pt
    while k:
        if k & 1:
            result = point_add(result, addend)
        addend = point_add(addend, addend)
        k >>= 1
    return result


G = (GX, GY)

# ---------------------------------------------------------------- RFC 6979


def bits2int(b: bytes) -> int:
    blen = len(b) * 8
    x = int.from_bytes(b, "big")
    if blen > QLEN:
        x >>= (blen - QLEN)
    return x


def int2octets(x: int) -> bytes:
    return x.to_bytes(RLEN, "big")


def bits2octets(b: bytes) -> bytes:
    z1 = bits2int(b)
    z2 = z1 - N if z1 >= N else z1
    return int2octets(z2)


def _hmac(k: bytes, m: bytes) -> bytes:
    return hmac.new(k, m, hashlib.sha256).digest()


def rfc6979_nonce(d: int, h1: bytes) -> int:
    """RFC 6979 的确定性 nonce 生成（HMAC-SHA256 版）。"""
    hlen = hashlib.sha256().digest_size
    v = b"\x01" * hlen
    k = b"\x00" * hlen
    priv = int2octets(d)
    hb = bits2octets(h1)
    k = _hmac(k, v + b"\x00" + priv + hb)
    v = _hmac(k, v)
    k = _hmac(k, v + b"\x01" + priv + hb)
    v = _hmac(k, v)
    while True:
        t = b""
        while len(t) * 8 < QLEN:
            v = _hmac(k, v)
            t += v
        cand = bits2int(t)
        if 1 <= cand < N:
            r = point_mul(G, cand)
            if r is not None:
                rr = r[0] % N
                if rr != 0:
                    return cand
        k = _hmac(k, v + b"\x00")
        v = _hmac(k, v)


# ---------------------------------------------------------------- ECDSA


def sha256_int(msg: bytes) -> int:
    return int.from_bytes(hashlib.sha256(msg).digest(), "big")


def sign(d: int, msg: bytes) -> tuple:
    """返回 (r, s)；签名编码是 r||s 的原始拼接，不是 ASN.1 DER。"""
    h = hashlib.sha256(msg).digest()
    e = bits2int(h)
    while True:
        k = rfc6979_nonce(d, h)
        r_pt = point_mul(G, k)
        r = r_pt[0] % N
        if r == 0:
            continue
        s = pow(k, N - 2, N) * (e + r * d) % N
        if s == 0:
            continue
        return r, s


def verify(pub: tuple, msg: bytes, r: int, s: int) -> bool:
    if not (1 <= r < N and 1 <= s < N):
        return False
    if not is_on_curve(pub):
        return False
    if point_mul(pub, N) is not None:      # 公钥不在阶为 n 的子群上
        return False
    e = sha256_int(msg)
    w = pow(s, N - 2, N)
    u1 = e * w % N
    u2 = r * w % N
    pt = point_add(point_mul(G, u1), point_mul(pub, u2))
    if pt is None:
        return False
    return pt[0] % N == r
