"""
ChaCha20-Poly1305（RFC 8439）—— 纯标准库实现

ECH 用 HPKE 加密 ClientHelloInner，而 RFC 9180 的 AEAD 里最适合纯 Python 实现的是
ChaCha20-Poly1305：它只有加法/异或/移位/32 位乘法，没有 AES 的 S 盒与 GHASH 查表。
本 demo 选的 HPKE 套件是 **DHKEM(X25519, HKDF-SHA256) + HKDF-SHA256 + ChaCha20-Poly1305**
（RFC 9180 §7.1 的 kem_id=0x0020、kdf_id=0x0001、aead_id=0x0003），
正好对应 RFC 9180 附录 A.2 的官方测试向量。

RFC 8439 与 RFC 9180 的 nonce 都是 96 位、每加密一次都要换；
HPKE 用「base_nonce XOR 序号」来派生（见 hpke.py），所以这里的 nonce 由调用方给。
"""

from __future__ import annotations

import struct

_CONST = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)
_MASK32 = 0xFFFFFFFF


def _rotl32(v: int, n: int) -> int:
    return ((v << n) | (v >> (32 - n))) & _MASK32


def _quarter(s, a, b, c, d) -> None:
    s[a] = (s[a] + s[b]) & _MASK32
    s[d] = _rotl32(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & _MASK32
    s[b] = _rotl32(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b]) & _MASK32
    s[d] = _rotl32(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & _MASK32
    s[b] = _rotl32(s[b] ^ s[c], 7)


def _block(key: bytes, counter: int, nonce: bytes) -> bytes:
    """RFC 8439 §2.3.2：一次 20 轮（10 次双轮）置换后加上初始状态。"""
    state = list(_CONST) + list(struct.unpack("<8I", key)) + [counter] + \
        list(struct.unpack("<3I", nonce))
    w = list(state)
    for _ in range(10):
        _quarter(w, 0, 4, 8, 12)
        _quarter(w, 1, 5, 9, 13)
        _quarter(w, 2, 6, 10, 14)
        _quarter(w, 3, 7, 11, 15)
        _quarter(w, 0, 5, 10, 15)
        _quarter(w, 1, 6, 11, 12)
        _quarter(w, 2, 7, 8, 13)
        _quarter(w, 3, 4, 9, 14)
    return struct.pack("<16I", *[(w[i] + state[i]) & _MASK32 for i in range(16)])


def chacha20_block(key: bytes, nonce: bytes, counter: int) -> bytes:
    """单块密钥流（64 字节）。Poly1305 的一次性密钥取 counter=0 的前 32 字节。"""
    return _block(key, counter, nonce)


def chacha20(key: bytes, nonce: bytes, counter: int, data: bytes) -> bytes:
    """RFC 8439 §2.4：密钥流逐块异或。counter 从 1 开始（0 块留给 Poly1305）。"""
    out = bytearray(len(data))
    for off in range(0, len(data), 64):
        ks = _block(key, (counter + off // 64) & _MASK32, nonce)
        chunk = data[off:off + 64]
        for i, b in enumerate(chunk):
            out[off + i] = b ^ ks[i]
    return bytes(out)


def poly1305(key: bytes, msg: bytes) -> bytes:
    """RFC 8439 §2.5：r 的 22 个高位必须清零，模数 2^130-5。"""
    assert len(key) == 32
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:], "little")
    mod = (1 << 130) - 5
    acc = 0
    for i in range(0, len(msg), 16):
        blk = msg[i:i + 16]
        n = int.from_bytes(blk + b"\x01", "little")   # 每块隐含补 1 位
        acc = ((acc + n) * r) % mod
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(data: bytes) -> bytes:
    return b"\x00" * (-len(data) % 16)


def _mac_data(aad: bytes, ct: bytes) -> bytes:
    return (aad + _pad16(aad) + ct + _pad16(ct)
            + struct.pack("<Q", len(aad)) + struct.pack("<Q", len(ct)))


TAG_LEN = 16
KEY_LEN = 32
NONCE_LEN = 12


def seal(key: bytes, nonce: bytes, aad: bytes, plaintext: bytes) -> bytes:
    """返回 ciphertext || tag（RFC 8439 §2.8）。"""
    assert len(key) == KEY_LEN and len(nonce) == NONCE_LEN
    otk = chacha20_block(key, nonce, 0)[:32]
    ct = chacha20(key, nonce, 1, plaintext)
    return ct + poly1305(otk, _mac_data(aad, ct))


def open_(key: bytes, nonce: bytes, aad: bytes, sealed: bytes) -> bytes:
    """校验失败抛 ValueError —— ECH 里这一条就是「ClientHelloOuter 被改过」的判据。"""
    assert len(key) == KEY_LEN and len(nonce) == NONCE_LEN
    if len(sealed) < TAG_LEN:
        raise ValueError("密文短于 tag 长度")
    ct, tag = sealed[:-TAG_LEN], sealed[-TAG_LEN:]
    otk = chacha20_block(key, nonce, 0)[:32]
    if poly1305(otk, _mac_data(aad, ct)) != tag:
        raise ValueError("认证标签不匹配")
    return chacha20(key, nonce, 1, ct)
