"""
QUIC v1 包保护所需的密码学（纯标准库）

QUIC 的包保护只用两套原语，且**没有独立协商过程**——沿用 TLS 1.3 的流量密钥：
  - AEAD：AEAD_AES_128_GCM（头保护用 AES-ECB）或 AEAD_CHACHA20_POLY1305（头保护用 ChaCha20）
  - KDF：HKDF + TLS 1.3 的 HKDF-Expand-Label（RFC 8446 §7.1）

本 demo 走 ChaCha20-Poly1305 套件，这样可以只用纯 Python 实现（标准库没有 AES/ChaCha），
AES-ECB 路径在 C 版里用 OpenSSL 演示，两侧互为对照。

依赖仅标准库 hashlib / hmac / struct。
"""

from __future__ import annotations

import hashlib
import hmac
import struct

HASH_LEN = 32                       # SHA-256
BLOCK = 64


# ============================================================
# 1. HKDF-SHA256 与 TLS 1.3 的 HKDF-Expand-Label
# ============================================================

def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    """HKDF-Extract(salt, IKM) = HMAC-Hash(salt, IKM)；salt 为空时按 RFC 5869 取全零。"""
    if not salt:
        salt = b"\x00" * HASH_LEN
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """HKDF-Expand(PRK, info, L)：按 T(i)=HMAC(PRK, T(i-1) | info | i) 迭代。"""
    out, prev, i = b"", b"", 1
    while len(out) < length:
        prev = hmac.new(prk, prev + info + bytes([i]), hashlib.sha256).digest()
        out += prev
        i += 1
    return out[:length]


def hkdf_expand_label(secret: bytes, label: bytes, context: bytes, length: int) -> bytes:
    """RFC 8446 §7.1：HkdfLabel = uint16(length) | opaque label<7..255> | opaque context。

    标签**必须**带 "tls13 " 前缀（6 字节），context 用 1 字节长度前缀；QUIC 的
    所有用途 context 都为空串，因此前缀后紧跟一个 0x00。
    """
    full_label = b"tls13 " + label
    info = struct.pack("!H", length) + bytes([len(full_label)]) + full_label + \
        bytes([len(context)]) + context
    return hkdf_expand(secret, info, length)


# RFC 9001 §5.2 的固定盐：Initial 密钥必须由它派生，否则与实现对不上
INITIAL_SALT = bytes.fromhex("38762cf7f55934b34d179ae6a4c80cadccbb7f0a")


def initial_secrets(client_dst_conn_id: bytes) -> tuple[bytes, bytes]:
    """Initial 密钥从「客户端首个 Initial 包的 DCID」派生，哈希固定用 SHA-256。"""
    secret = hkdf_extract(INITIAL_SALT, client_dst_conn_id)
    return (hkdf_expand_label(secret, b"client in", b"", HASH_LEN),
            hkdf_expand_label(secret, b"server in", b"", HASH_LEN))


def packet_keys(secret: bytes, key_len: int = 32, iv_len: int = 12,
                hp_len: int = 32) -> tuple[bytes, bytes, bytes]:
    """从某个加密级别的 secret 派生 (AEAD key, IV, 头保护 key)。

    三个标签分别是 "quic key" / "quic iv" / "quic hp"，用于把 QUIC 与 TLS 的密钥
    相互分离（key separation）。IV 长度取 AEAD nonce 最小长度与 8 的较大者。
    """
    key = hkdf_expand_label(secret, b"quic key", b"", key_len)
    iv = hkdf_expand_label(secret, b"quic iv", b"", max(iv_len, 8))
    hp = hkdf_expand_label(secret, b"quic hp", b"", hp_len)
    return key, iv, hp


def next_secret(secret: bytes) -> bytes:
    """密钥更新：只换标签 "quic ku"，其余流程不变（头保护密钥**不**更新）。"""
    return hkdf_expand_label(secret, b"quic ku", b"", HASH_LEN)


# ============================================================
# 2. ChaCha20 / Poly1305 / AEAD（RFC 8439，QUIC 的 ChaCha20 套件用）
# ============================================================

_SIGMA = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)


def _rotl(v: int, n: int) -> int:
    return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF


def _qr(s: list[int], a: int, b: int, c: int, d: int) -> None:
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF
    s[d] = _rotl(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF
    s[b] = _rotl(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF
    s[d] = _rotl(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF
    s[b] = _rotl(s[b] ^ s[c], 7)


def chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
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
    out = bytearray()
    for i in range(0, len(data), 64):
        ks = chacha20_block(key, counter + i // 64, nonce)
        out += bytes(a ^ b for a, b in zip(data[i:i + 64], ks))
    return bytes(out)


_M1305 = (1 << 130) - 5


def poly1305_mac(key: bytes, msg: bytes) -> bytes:
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:32], "little")
    acc = 0
    for i in range(0, len(msg), 16):
        blk = msg[i:i + 16]
        acc = (acc + int.from_bytes(blk + b"\x01", "little")) * r % _M1305
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(b: bytes) -> bytes:
    return b"" if len(b) % 16 == 0 else b"\x00" * (16 - len(b) % 16)


def aead_seal(key: bytes, nonce: bytes, aad: bytes, plaintext: bytes) -> bytes:
    otk = chacha20_block(key, 0, nonce)[:32]
    ct = chacha20_xor(key, 1, nonce, plaintext)
    mac = aad + _pad16(aad) + ct + _pad16(ct) + struct.pack("<QQ", len(aad), len(ct))
    return ct + poly1305_mac(otk, mac)


def aead_open(key: bytes, nonce: bytes, aad: bytes, sealed: bytes) -> bytes:
    ct, tag = sealed[:-16], sealed[-16:]
    otk = chacha20_block(key, 0, nonce)[:32]
    mac = aad + _pad16(aad) + ct + _pad16(ct) + struct.pack("<QQ", len(aad), len(ct))
    if not hmac.compare_digest(poly1305_mac(otk, mac), tag):
        raise ValueError("QUIC AEAD authentication failed")
    return chacha20_xor(key, 1, nonce, ct)


def header_mask(hp_key: bytes, sample: bytes) -> bytes:
    """ChaCha20 头保护掩码：样本前 4 字节为块计数器（小端），后 12 字节为 nonce。

    掩码本身是「用 ChaCha20 加密 5 个零字节」得到的 5 字节。
    AES 套件则不同：`mask = AES-ECB(hp_key, sample)`（见 C 版）。
    """
    counter = int.from_bytes(sample[0:4], "little")
    nonce = sample[4:16]
    return chacha20_xor(hp_key, counter, nonce, b"\x00" * 5)
