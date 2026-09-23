"""X25519（RFC 7748 §5）与 HKDF（RFC 5869）。

HPKE 的 DHKEM(X25519, HKDF-SHA256) 就架在这两个原语上。
"""

import hmac
import hashlib

P25519 = (1 << 255) - 19
A24 = 121665


def clamp_scalar(k):
    """RFC 7748 §5：清低 3 位、清最高位、置次高位。"""
    k = bytearray(k)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    return bytes(k)


def decode_u(u):
    """小端解码并把最高位清零（u 坐标只有 255 位有效）。"""
    return int.from_bytes(u, "little") & ((1 << 255) - 1)


def encode_u(x):
    return (x & ((1 << 255) - 1)).to_bytes(32, "little")


def _cswap(swap, a, b):
    dummy = swap * ((a - b) % P25519)
    a = (a - dummy) % P25519
    b = (b + dummy) % P25519
    return a, b


def x25519(k, u):
    """Montgomery 阶梯，逐位（从 254 到 0）做差分加/倍点。"""
    k = clamp_scalar(k)
    x1 = decode_u(u)
    x2, z2, x3, z3 = 1, 0, x1, 1
    swap = 0
    for t in range(254, -1, -1):
        kt = (k[t // 8] >> (t % 8)) & 1
        swap ^= kt
        x2, x3 = _cswap(swap, x2, x3)
        z2, z3 = _cswap(swap, z2, z3)
        swap = kt

        a = (x2 + z2) % P25519
        aa = a * a % P25519
        b = (x2 - z2) % P25519
        bb = b * b % P25519
        e = (aa - bb) % P25519
        c = (x3 + z3) % P25519
        d = (x3 - z3) % P25519
        da = d * a % P25519
        cb = c * b % P25519
        x3 = pow(da + cb, 2, P25519)
        z3 = x1 * pow(da - cb, 2, P25519) % P25519
        x2 = aa * bb % P25519
        z2 = e * (aa + A24 * e) % P25519
    x2, x3 = _cswap(swap, x2, x3)
    z2, z3 = _cswap(swap, z2, z3)
    return encode_u(x2 * pow(z2, P25519 - 2, P25519) % P25519)


X25519_BASE = encode_u(9)


def x25519_base(k):
    return x25519(k, X25519_BASE)


# ------------------------------------------------------------------ HKDF
def hkdf_extract(salt, ikm):
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk, info, length):
    out = b""
    t = b""
    counter = 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        out += t
        counter += 1
    return out[:length]
