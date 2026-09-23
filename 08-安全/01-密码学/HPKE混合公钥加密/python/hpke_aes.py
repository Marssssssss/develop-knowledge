"""AES-128 与 AES-128-GCM（FIPS 197 + NIST SP 800-38D）。

HPKE 的 AEAD 用的是 AES-128-GCM（`aead_id = 0x0001`），为了能对上 RFC 9180
附录 A.1 的密文向量，这里从零实现。S 盒由 GF(2^8) 上求逆 + 仿射变换**算出来**，
不是手抄的 256 个常数——抄错一个字节只会让密文静默对不上。
"""

RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _init_sbox():
    s = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        x = (q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6))
             ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4)))
        s[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    s[0] = 0x63
    return s


SBOX = _init_sbox()


def _xtime(a):
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1B) & 0xFF
    return a


def _mul(a, b):
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        b >>= 1
        a = _xtime(a)
    return r


def key_expansion(key):
    """AES-128 轮密钥：11 个 16 字节轮密钥。"""
    assert len(key) == 16
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [SBOX[b] for b in t]
            t[0] ^= RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return w


def encrypt_block(w, block):
    """单块 AES-128 加密。

    `w` 是 44 个 4 字节字的轮密钥表。state 按**列**组织：输入字节 `4c+r` 落在
    `state[r][c]`（这里最容易写反，写反了密文会静默对不上）。
    """
    state = [[0] * 4 for _ in range(4)]
    for r in range(4):
        for c in range(4):
            state[r][c] = block[4 * c + r]

    def add_round_key(rnd):
        for c in range(4):
            for r in range(4):
                state[r][c] ^= w[rnd * 4 + c][r]

    def sub_bytes():
        for r in range(4):
            for c in range(4):
                state[r][c] = SBOX[state[r][c]]

    def shift_rows():
        for r in range(1, 4):
            row = [state[r][c] for c in range(4)]
            for c in range(4):
                state[r][c] = row[(c + r) % 4]

    def mix_columns():
        for c in range(4):
            a0, a1, a2, a3 = [state[r][c] for r in range(4)]
            state[0][c] = _mul(a0, 2) ^ _mul(a1, 3) ^ a2 ^ a3
            state[1][c] = a0 ^ _mul(a1, 2) ^ _mul(a2, 3) ^ a3
            state[2][c] = a0 ^ a1 ^ _mul(a2, 2) ^ _mul(a3, 3)
            state[3][c] = _mul(a0, 3) ^ a1 ^ a2 ^ _mul(a3, 2)

    add_round_key(0)
    for rnd in range(1, 10):
        sub_bytes()
        shift_rows()
        mix_columns()
        add_round_key(rnd)
    sub_bytes()
    shift_rows()
    add_round_key(10)
    out = bytearray(16)
    for r in range(4):
        for c in range(4):
            out[4 * c + r] = state[r][c]
    return bytes(out)


# ------------------------------------------------------------------ GCM
def _ghash_mul(x, y):
    """GF(2^128) 乘法，GCM 的多项式 x^128 + x^7 + x^2 + x + 1，位反射记法。"""
    r = 0
    v = y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            r ^= v
        if v & 1:
            v = (v >> 1) ^ 0xE1000000000000000000000000000000
        else:
            v >>= 1
    return r


def _ghash(h, data):
    y = 0
    for i in range(0, len(data), 16):
        blk = data[i:i + 16]
        blk = blk + b"\x00" * (16 - len(blk))
        y = _ghash_mul(y ^ int.from_bytes(blk, "big"), h)
    return y


def _gctr(rk, icb, data):
    """GCM 的 GCTR：计数器从 inc32(J0) 开始，每块 inc32 一次。"""
    out = bytearray()
    cb = icb
    for i in range(0, len(data), 16):
        ks = encrypt_block(rk, cb)
        chunk = data[i:i + 16]
        out += bytes(a ^ b for a, b in zip(chunk, ks))
        cb = _inc32(cb)
    return bytes(out)


def _inc32(cb):
    return cb[:12] + ((int.from_bytes(cb[12:], "big") + 1) & 0xFFFFFFFF).to_bytes(4, "big")


class AES128GCM:
    """Nk=16、Nn=12、Nt=16 的 AEAD，接口与 HPKE 用到的 Seal/Open 对齐。"""

    Nk, Nn, Nt = 16, 12, 16

    def __init__(self, key):
        self.rk = key_expansion(key)
        self.h = int.from_bytes(encrypt_block(self.rk, b"\x00" * 16), "big")

    def seal(self, nonce, aad, pt):
        if len(nonce) != 12:
            raise ValueError("GCM nonce must be 12 bytes")
        j0 = nonce + b"\x00\x00\x00\x01"
        ct = _gctr(self.rk, _inc32(j0), pt)
        len_blk = (len(aad) * 8).to_bytes(8, "big") + (len(ct) * 8).to_bytes(8, "big")
        # 长度块必须作为 GHASH 的**最后一个数据块**参与（要再乘一次 H），
        # 只在最后异或进去是错的——空输入时 H^0 情形碰巧对得上，所以很容易漏。
        s = _ghash(self.h, aad + _pad16(aad) + ct + _pad16(ct) + len_blk)
        tag = bytes(a ^ b for a, b in zip(s.to_bytes(16, "big"),
                                          encrypt_block(self.rk, j0)))
        return ct + tag

    def open(self, nonce, aad, ct):
        if len(ct) < 16:
            raise ValueError("ciphertext too short")
        body, tag = ct[:-16], ct[-16:]
        j0 = nonce + b"\x00\x00\x00\x01"
        len_blk = (len(aad) * 8).to_bytes(8, "big") + (len(body) * 8).to_bytes(8, "big")
        s = _ghash(self.h, aad + _pad16(aad) + body + _pad16(body) + len_blk)
        expect = bytes(a ^ b for a, b in zip(s.to_bytes(16, "big"),
                                             encrypt_block(self.rk, j0)))
        if not _ct_eq(expect, tag):
            raise ValueError("authentication failed")
        return _gctr(self.rk, _inc32(j0), body)


def _pad16(x):
    return b"\x00" * ((16 - len(x) % 16) % 16)


def _ct_eq(a, b):
    if len(a) != len(b):
        return False
    d = 0
    for x, y in zip(a, b):
        d |= x ^ y
    return d == 0
