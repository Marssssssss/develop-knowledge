"""从零实现 AES-128 + RFC 3394 密钥包装 + JWE 的 A128CBC-HS256（RFC 7518 §5.2）。

S 盒不查表、不硬编码，而是**算出来**：先取 GF(2^8) 的乘法逆（0 映到 0），
再做仿射变换 b ^ rotl(b,1) ^ rotl(b,2) ^ rotl(b,3) ^ rotl(b,4) ^ 0x63 —— 这正是
FIPS 197 §5.1.1 的定义，算完可以拿官方向量核对，比抄一张表更有说服力。
"""

import hashlib
import hmac

# ------------------------------------------------------------------ GF(2^8)
IRRED = 0x1B


def gmul(a, b):
    r = 0
    for i in range(8):
        if (b >> i) & 1:
            r ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= IRRED
    return r


def ginv(a):
    if a == 0:
        return 0
    for x in range(1, 256):
        if gmul(a, x) == 1:
            return x
    raise ValueError


def _rotl8(v, n):
    return ((v << n) | (v >> (8 - n))) & 0xFF


def _sbox():
    out = [0] * 256
    for b in range(256):
        inv = ginv(b)
        out[b] = inv ^ _rotl8(inv, 1) ^ _rotl8(inv, 2) ^ _rotl8(inv, 3) \
            ^ _rotl8(inv, 4) ^ 0x63
    return out


SBOX = _sbox()
INV_SBOX = [0] * 256
for i, v in enumerate(SBOX):
    INV_SBOX[v] = i

RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


class AES128:
    """AES-128（FIPS 197）：10 轮，Nb = 4，Nk = 4。"""

    def __init__(self, key):
        if len(key) != 16:
            raise ValueError("AES-128 key must be 16 bytes")
        self.round_keys = self._expand(key)

    @staticmethod
    def _expand(key):
        w = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
        for i in range(4, 44):
            temp = list(w[i - 1])
            if i % 4 == 0:
                temp = temp[1:] + temp[:1]                  # RotWord
                temp = [SBOX[b] for b in temp]              # SubWord
                temp[0] ^= RCON[i // 4 - 1]                 # Rcon
            w.append([w[i - 4][j] ^ temp[j] for j in range(4)])
        return w

    def _add_round_key(self, s, rnd):
        for c in range(4):
            for r in range(4):
                s[r][c] ^= self.round_keys[rnd * 4 + c][r]

    @staticmethod
    def _sub_bytes(s):
        for c in range(4):
            for r in range(4):
                s[r][c] = SBOX[s[r][c]]

    @staticmethod
    def _inv_sub_bytes(s):
        for c in range(4):
            for r in range(4):
                s[r][c] = INV_SBOX[s[r][c]]

    @staticmethod
    def _shift_rows(s):                                     # 第 r 行循环左移 r
        for r in range(1, 4):
            row = [s[r][(c + r) % 4] for c in range(4)]
            for c in range(4):
                s[r][c] = row[c]

    @staticmethod
    def _inv_shift_rows(s):
        for r in range(1, 4):
            row = [s[r][(c - r) % 4] for c in range(4)]
            for c in range(4):
                s[r][c] = row[c]

    @staticmethod
    def _mix_columns(s):
        for c in range(4):
            a = [s[r][c] for r in range(4)]
            s[0][c] = gmul(a[0], 2) ^ gmul(a[1], 3) ^ a[2] ^ a[3]
            s[1][c] = a[0] ^ gmul(a[1], 2) ^ gmul(a[2], 3) ^ a[3]
            s[2][c] = a[0] ^ a[1] ^ gmul(a[2], 2) ^ gmul(a[3], 3)
            s[3][c] = gmul(a[0], 3) ^ a[1] ^ a[2] ^ gmul(a[3], 2)

    @staticmethod
    def _inv_mix_columns(s):
        for c in range(4):
            a = [s[r][c] for r in range(4)]
            s[0][c] = gmul(a[0], 14) ^ gmul(a[1], 11) ^ gmul(a[2], 13) ^ gmul(a[3], 9)
            s[1][c] = gmul(a[0], 9) ^ gmul(a[1], 14) ^ gmul(a[2], 11) ^ gmul(a[3], 13)
            s[2][c] = gmul(a[0], 13) ^ gmul(a[1], 9) ^ gmul(a[2], 14) ^ gmul(a[3], 11)
            s[3][c] = gmul(a[0], 11) ^ gmul(a[1], 13) ^ gmul(a[2], 9) ^ gmul(a[3], 14)

    def encrypt_block(self, block):
        s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
        self._add_round_key(s, 0)
        for rnd in range(1, 10):
            self._sub_bytes(s)
            self._shift_rows(s)
            self._mix_columns(s)
            self._add_round_key(s, rnd)
        self._sub_bytes(s)
        self._shift_rows(s)
        self._add_round_key(s, 10)
        return bytes(s[r][c] for c in range(4) for r in range(4))

    def decrypt_block(self, block):
        s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
        self._add_round_key(s, 10)
        for rnd in range(9, 0, -1):
            self._inv_shift_rows(s)
            self._inv_sub_bytes(s)
            self._add_round_key(s, rnd)
            self._inv_mix_columns(s)
        self._inv_shift_rows(s)
        self._inv_sub_bytes(s)
        self._add_round_key(s, 0)
        return bytes(s[r][c] for c in range(4) for r in range(4))


# --------------------------------------------------------------- PKCS#7 / CBC
def pkcs7_pad(data, block=16):
    n = block - (len(data) % block)
    return data + bytes([n]) * n


def pkcs7_unpad(data, block=16):
    if len(data) == 0 or len(data) % block != 0:
        raise ValueError("bad padded length")
    n = data[-1]
    if n == 0 or n > block or data[-n:] != bytes([n]) * n:
        raise ValueError("bad padding")
    return data[:-n]


def cbc_encrypt(aes, data, iv):
    out = b""
    prev = iv
    for i in range(0, len(data), 16):
        blk = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
        cur = aes.encrypt_block(blk)
        out += cur
        prev = cur
    return out


def cbc_decrypt(aes, data, iv):
    out = b""
    prev = iv
    for i in range(0, len(data), 16):
        cur = data[i:i + 16]
        out += bytes(a ^ b for a, b in zip(aes.decrypt_block(cur), prev))
        prev = cur
    return out


# ----------------------------------------------------- RFC 3394 AES Key Wrap
DEFAULT_IV = b"\xa6" * 8


def aes_key_wrap(kek, plaintext):
    """RFC 3394 §2.2.1：n = len/8，6*n 轮，A 初值是默认 IV A6A6A6A6A6A6A6A6。"""
    aes = AES128(kek)
    if len(plaintext) % 8 != 0 or len(plaintext) < 16:
        raise ValueError("AES-KW plaintext must be a multiple of 8 and >= 16 bytes")
    n = len(plaintext) // 8
    a = int.from_bytes(DEFAULT_IV, "big")
    r = [int.from_bytes(plaintext[i * 8:i * 8 + 8], "big") for i in range(n)]
    for j in range(6):
        for i in range(1, n + 1):
            b = aes.encrypt_block((a << 64 | r[i - 1]).to_bytes(16, "big"))
            a = int.from_bytes(b[:8], "big") ^ (n * j + i)
            r[i - 1] = int.from_bytes(b[8:], "big")
    return a.to_bytes(8, "big") + b"".join(x.to_bytes(8, "big") for x in r)


def aes_key_unwrap(kek, ciphertext):
    """RFC 3394 §2.2.2：倒着跑，最后必须回收到默认 IV，否则就是**完整性校验失败**。"""
    aes = AES128(kek)
    if len(ciphertext) % 8 != 0 or len(ciphertext) < 24:
        raise ValueError("bad AES-KW ciphertext length")
    n = len(ciphertext) // 8 - 1
    a = int.from_bytes(ciphertext[:8], "big")
    r = [int.from_bytes(ciphertext[i * 8 + 8:i * 8 + 16], "big") for i in range(n)]
    for j in range(5, -1, -1):
        for i in range(n, 0, -1):
            b = aes.decrypt_block(((a ^ (n * j + i)) << 64 | r[i - 1]).to_bytes(16, "big"))
            a = int.from_bytes(b[:8], "big")
            r[i - 1] = int.from_bytes(b[8:], "big")
    if a.to_bytes(8, "big") != DEFAULT_IV:
        raise ValueError("integrity check failed")
    return b"".join(x.to_bytes(8, "big") for x in r)


# ------------------------------------- A128CBC-HS256（RFC 7518 §5.2.2）
def a128cbc_hs256_encrypt(cek, iv, aad, plaintext):
    """MAC_KEY = CEK 前 16 字节、ENC_KEY = 后 16 字节；AL 是 AAD 的**位**长大端 8 字节。"""
    if len(cek) != 32:
        raise ValueError("A128CBC-HS256 needs a 256-bit CEK")
    mac_key, enc_key = cek[:16], cek[16:]
    aes = AES128(enc_key)
    ct = cbc_encrypt(aes, pkcs7_pad(plaintext), iv)
    al = (len(aad) * 8).to_bytes(8, "big")
    tag = hmac.new(mac_key, aad + iv + ct + al, hashlib.sha256).digest()[:16]
    return ct, tag


def a128cbc_hs256_decrypt(cek, iv, aad, ct, tag):
    if len(cek) != 32:
        raise ValueError("A128CBC-HS256 needs a 256-bit CEK")
    mac_key, enc_key = cek[:16], cek[16:]
    al = (len(aad) * 8).to_bytes(8, "big")
    expect = hmac.new(mac_key, aad + iv + ct + al, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(expect, tag):
        raise ValueError("authentication tag mismatch")
    return pkcs7_unpad(cbc_decrypt(AES128(enc_key), ct, iv))
