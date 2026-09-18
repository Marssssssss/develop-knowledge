"""
WireGuard / Noise_IKpsk2 所需的密码学原语（纯标准库实现，无第三方依赖）

包含四个原语，均在 README「原理详解」中被逐条引用：
  1. X25519        —— RFC 7748 §5 的 Montgomery 阶梯，用于 Curve25519 ECDH
  2. ChaCha20      —— RFC 8439 §2.3 的块函数与流加密
  3. Poly1305      —— RFC 8439 §2.5 的一次性认证器
  4. ChaCha20Poly1305 —— RFC 8439 §2.8 的 AEAD 组合
  5. BLAKE2s / HMAC-BLAKE2s / KDF —— WireGuard 把 HKDF 的哈希整体换成 BLAKE2s
     (内核源码 noise.c 的 hmac()/kdf() 即为「HMAC-BLAKE2s 上的 HKDF」)

标准库能直接提供 BLAKE2s（hashlib.blake2s 支持 digest_size 与 key 参数，与内核
blake2s(out, outlen, key, keylen, in, inlen) 语义一致），故只需手写前四项。
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import struct
import time

# ============================================================
# 1. X25519 (RFC 7748 §5)
# ============================================================

_P = 2**255 - 19
_A24 = 121665                     # (486662 - 2) / 4，RFC 7748 §5 的阶梯常数
_BITS = 255


def _clamp(k: bytes) -> int:
    """私钥钳位：清低 3 位、清最高位、置次高位（RFC 7748 §5 decodeScalar25519）。"""
    b = bytearray(k)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return int.from_bytes(b, "little")


def x25519(private: bytes, peer_public: bytes) -> bytes:
    """X25519 标量乘法，返回 32 字节 u 坐标。

    私钥与对端公钥的高位都被强制清零（RFC 7748 要求忽略 u 的最高位）。
    """
    k = _clamp(private)
    u = int.from_bytes(peer_public, "little") & ((1 << _BITS) - 1)
    x1, x2, z2, x3, z3, swap = u, 1, 0, u, 1, 0
    for t in range(_BITS - 1, -1, -1):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb) % _P
        x3 = x3 * x3 % _P
        z3 = (da - cb) % _P
        z3 = x1 * z3 % _P * z3 % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def x25519_public(private: bytes) -> bytes:
    """公钥 = X25519(私钥, 9)：基准点 u=9 固定。"""
    return x25519(private, b"\x09" + b"\x00" * 31)


def x25519_keypair(seed: bytes) -> tuple[bytes, bytes]:
    """确定性密钥对（演示用；生产必须用 os.urandom 取私钥）。"""
    priv = hashlib.blake2s(seed, digest_size=32).digest()
    return priv, x25519_public(priv)


# ============================================================
# 2. ChaCha20 (RFC 8439 §2.3)
# ============================================================

_SIGMA = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)
ZERO4 = bytes(4)            # XChaCha20 内层 nonce 的前 4 字节填充（4 个零字节）


def _rotl(v: int, n: int) -> int:
    return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF


def _qr(s: list[int], a: int, b: int, c: int, d: int) -> None:
    """RFC 8439 §2.1 的四分之一轮：4 次加、4 次异或、4 次循环左移。"""
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF
    s[d] = _rotl(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF
    s[b] = _rotl(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF
    s[d] = _rotl(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF
    s[b] = _rotl(s[b] ^ s[c], 7)


def chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    """生成一个 64 字节密钥流块（20 轮 = 10 次双轮）。"""
    st = list(_SIGMA) + list(struct.unpack("<8I", key)) + [counter] + \
        list(struct.unpack("<3I", nonce))
    w = st[:]
    for _ in range(10):
        _qr(w, 0, 4, 8, 12)
        _qr(w, 1, 5, 9, 13)
        _qr(w, 2, 6, 10, 14)
        _qr(w, 3, 7, 11, 15)
        _qr(w, 0, 5, 10, 15)
        _qr(w, 1, 6, 11, 12)
        _qr(w, 2, 7, 8, 13)
        _qr(w, 3, 4, 9, 14)
    return struct.pack("<16I", *[(w[i] + st[i]) & 0xFFFFFFFF for i in range(16)])


def chacha20_xor(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    """流加密：块起始计数器 counter，逐块 +1（RFC 8439 §2.4）。"""
    out = bytearray()
    for i in range(0, len(data), 64):
        ks = chacha20_block(key, counter + i // 64, nonce)
        chunk = data[i:i + 64]
        out += bytes(a ^ b for a, b in zip(chunk, ks))
    return bytes(out)


# ============================================================
# 3. Poly1305 (RFC 8439 §2.5)
# ============================================================

_M1305 = (1 << 130) - 5


def poly1305_mac(key: bytes, msg: bytes) -> bytes:
    """Poly1305 一次性认证器：r 先钳位再累加，最后加 s 取低 128 位。"""
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:32], "little")
    acc = 0
    for i in range(0, len(msg), 16):
        block = msg[i:i + 16]
        n = int.from_bytes(block + b"\x01", "little")   # 末尾补 1 字节高位
        acc = (acc + n) * r % _M1305
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(data: bytes) -> bytes:
    return b"" if len(data) % 16 == 0 else b"\x00" * (16 - len(data) % 16)


# ============================================================
# 4. AEAD_CHACHA20_POLY1305 (RFC 8439 §2.8)
# ============================================================

def aead_encrypt(key: bytes, nonce: bytes, aad: bytes, plaintext: bytes) -> bytes:
    """返回 ciphertext || tag(16)。密钥流派生用 counter=0，正文从 counter=1 起。"""
    otk = chacha20_block(key, 0, nonce)[:32]
    ct = chacha20_xor(key, 1, nonce, plaintext)
    mac_data = aad + _pad16(aad) + ct + _pad16(ct) + \
        struct.pack("<QQ", len(aad), len(ct))
    return ct + poly1305_mac(otk, mac_data)


def aead_decrypt(key: bytes, nonce: bytes, aad: bytes, sealed: bytes) -> bytes:
    """认证失败抛 ValueError（对应内核 chacha20poly1305_decrypt 返回 false）。"""
    ct, tag = sealed[:-16], sealed[-16:]
    otk = chacha20_block(key, 0, nonce)[:32]
    mac_data = aad + _pad16(aad) + ct + _pad16(ct) + \
        struct.pack("<QQ", len(aad), len(ct))
    if not _hmac.compare_digest(poly1305_mac(otk, mac_data), tag):
        raise ValueError("AEAD authentication failed")
    return chacha20_xor(key, 1, nonce, ct)
# ============================================================
# 4b. XChaCha20-Poly1305 (draft-irtf-cfrg-xchacha §2, 中译本见 RFC 8439 第 7 章讨论)
#
# WireGuard 的 cookie 回复用 XChaCha20Poly1305 而非 ChaCha20Poly1305：
# 因为它要一个「随机生成的 24 字节 nonce」，而 96 位 nonce 下随机取值的碰撞概率
# 不够低（同一静态密钥会长期复用）。HChaCha20 把 24 字节 nonce 的前 16 字节
# 与主密钥混成子密钥，剩下的 8 字节补 4 个零字节当 96 位 nonce 用。
# ============================================================

def hchacha20(key: bytes, nonce16: bytes) -> bytes:
    """HChaCha20：只跑 20 轮、不做末轮加初态，输出 state 的第 0~3 与 12~15 个字。"""
    st = list(_SIGMA) + list(struct.unpack("<8I", key)) + list(struct.unpack("<4I", nonce16))
    w = st[:]
    for _ in range(10):
        _qr(w, 0, 4, 8, 12)
        _qr(w, 1, 5, 9, 13)
        _qr(w, 2, 6, 10, 14)
        _qr(w, 3, 7, 11, 15)
        _qr(w, 0, 5, 10, 15)
        _qr(w, 1, 6, 11, 12)
        _qr(w, 2, 7, 8, 13)
        _qr(w, 3, 4, 9, 14)
    return struct.pack("<8I", *(w[0:4] + w[12:16]))


def xchacha20_xor(key: bytes, nonce: bytes, data: bytes, counter: int = 1) -> bytes:
    """XChaCha20：子密钥 = HChaCha20(key, nonce[0:16])，再用 nonce = 0^4 ‖ nonce[16:24]。"""
    subkey = hchacha20(key, nonce[:16])
    return chacha20_xor(subkey, counter, b"\x00" * 4 + nonce[16:24], data)


def xchacha20poly1305_encrypt(key: bytes, nonce: bytes, aad: bytes,
                              plaintext: bytes) -> bytes:
    subkey = hchacha20(key, nonce[:16])
    return aead_encrypt(subkey, b"\x00" * 4 + nonce[16:24], aad, plaintext)


def xchacha20poly1305_decrypt(key: bytes, nonce: bytes, aad: bytes,
                              sealed: bytes) -> bytes:
    subkey = hchacha20(key, nonce[:16])
    return aead_decrypt(subkey, b"\x00" * 4 + nonce[16:24], aad, sealed)
