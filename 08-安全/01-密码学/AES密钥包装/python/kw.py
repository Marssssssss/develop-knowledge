"""RFC 3394 AES Key Wrap + RFC 5649 AES Key Wrap with Padding。

RFC 3394 §2.2.1 索引式描述（与寄存器移位式等价，更易软件实现）：

    A = IV;  R[i] = P[i]
    for j in 0..5:
        for i in 1..n:
            B = AES(K, A | R[i])
            A = MSB(64, B) ^ t,   t = n*j + i
            R[i] = LSB(64, B)
    C[0] = A;  C[i] = R[i]

解包是上式逐行求逆：j 从 5 到 0、i 从 n 到 1，
    B = AES-1(K, (A ^ t) | R[i]);  A = MSB(64, B);  R[i] = LSB(64, B)

RFC 5649 只换了初始值（AIV = A65959A6 || MLI）并加了三条完整性检查，
另外 n == 1 时退化为单个 ECB 块。
"""

from aes_core import encrypt_block, decrypt_block

DEFAULT_IV = 0xA6A6A6A6A6A6A6A6          # RFC 3394 §2.2.3.1
AIV_CONST = 0xA65959A6                   # RFC 5649 §3
MAX_MLI = 0xFFFFFFFF


class KeyWrapError(Exception):
    """解包失败（完整性检查未通过）。"""


def _b2i(b: bytes) -> int:
    return int.from_bytes(b, "big")


def _i2b(v: int, n: int) -> bytes:
    return v.to_bytes(n, "big")


def _split_blocks(data: bytes):
    if len(data) % 8 != 0:
        raise ValueError("key wrap input must be a multiple of 8 octets")
    return [_b2i(data[i:i + 8]) for i in range(0, len(data), 8)]


# ---------------------------------------------------------------- RFC 3394

def aes_wrap(kek: bytes, plaintext: bytes, iv: int = DEFAULT_IV) -> bytes:
    """RFC 3394 索引式包装。plaintext 长度必须是 8 的倍数且 >= 16 字节。"""
    n = len(plaintext) // 8
    if n < 2:
        raise ValueError("RFC 3394 requires n >= 2 (at least 16 octets)")
    r = _split_blocks(plaintext)
    a = iv
    for j in range(6):
        for i in range(1, n + 1):
            b = encrypt_block(kek, _i2b(a, 8) + _i2b(r[i - 1], 8))
            a = _b2i(b[:8]) ^ (n * j + i)
            r[i - 1] = _b2i(b[8:])
    return _i2b(a, 8) + b"".join(_i2b(x, 8) for x in r)


def aes_unwrap(kek: bytes, ciphertext: bytes, iv: int = DEFAULT_IV) -> bytes:
    """RFC 3394 索引式解包，A 与期望的 IV 不符则抛 KeyWrapError。"""
    blocks = _split_blocks(ciphertext)
    if len(blocks) < 3:
        raise ValueError("RFC 3394 ciphertext has at least 3 blocks (n+1, n>=2)")
    n = len(blocks) - 1
    a = blocks[0]
    r = blocks[1:]
    for j in range(5, -1, -1):
        for i in range(n, 0, -1):
            b = decrypt_block(kek, _i2b(a ^ (n * j + i), 8) + _i2b(r[i - 1], 8))
            a = _b2i(b[:8])
            r[i - 1] = _b2i(b[8:])
    if a != iv:
        raise KeyWrapError("integrity check failed: A != IV")
    return b"".join(_i2b(x, 8) for x in r)


# ---------------------------------------------------------------- RFC 5649

def _aiv(m: int) -> int:
    return (AIV_CONST << 32) | m


def kwp_wrap(kek: bytes, plaintext: bytes) -> bytes:
    """RFC 5649 §4.1 扩展包装：任意 1 .. 2^32-1 字节。"""
    m = len(plaintext)
    if m == 0 or m > MAX_MLI:
        raise ValueError("plaintext length out of range")
    pad = (-m) % 8
    padded = plaintext + b"\x00" * pad
    n = len(padded) // 8
    if n == 1:
        b = encrypt_block(kek, _i2b(_aiv(m), 8) + padded)
        return b
    return aes_wrap(kek, padded, iv=_aiv(m))


def unwrap_core(kek: bytes, ciphertext: bytes):
    """解包但不校验 A，返回 (A, padded)；RFC 5649 §4.2 第 1 步。

    n == 1 时是单个 ECB 块；n >= 2 时是 RFC 3394 索引式解包。
    """
    if len(ciphertext) < 16 or len(ciphertext) % 8 != 0:
        raise KeyWrapError("ciphertext length invalid")
    n = len(ciphertext) // 8 - 1
    if n == 1:
        b = decrypt_block(kek, ciphertext)
        return _b2i(b[:8]), b[8:]
    blocks = _split_blocks(ciphertext)
    a = blocks[0]
    r = blocks[1:]
    for j in range(5, -1, -1):
        for i in range(n, 0, -1):
            t = n * j + i
            b = decrypt_block(kek, _i2b(a ^ t, 8) + _i2b(r[i - 1], 8))
            a = _b2i(b[:8])
            r[i - 1] = _b2i(b[8:])
    return a, b"".join(_i2b(x, 8) for x in r)


def kwp_unwrap(kek: bytes, ciphertext: bytes) -> bytes:
    """RFC 5649 §4.2 扩展解包：先解包，再对 A 做三条 AIV 检查。"""
    a, padded = unwrap_core(kek, ciphertext)
    n = len(padded) // 8

    # RFC 5649 §3 三条检查，顺序即规范顺序
    if (a >> 32) != AIV_CONST:
        raise KeyWrapError("AIV check 1 failed: MSB(32,A) != A65959A6")
    mli = a & 0xFFFFFFFF
    if not (8 * (n - 1) < mli <= 8 * n):
        raise KeyWrapError("AIV check 2 failed: MLI out of range for n")
    b = 8 * n - mli
    if any(x != 0 for x in padded[8 * n - b:]):
        raise KeyWrapError("AIV check 3 failed: padding octets not zero")
    return padded[:mli]
