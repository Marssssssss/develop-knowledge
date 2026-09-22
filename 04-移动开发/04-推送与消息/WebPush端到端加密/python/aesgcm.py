"""AES-128-GCM 的最小实现，只为跑通 RFC 8291 附录 A 的官方向量。

GCM 的构造见 NIST SP 800-38D：H = CIPH_K(0^128)，12 字节 IV 时
J0 = IV || 0^31 || 1，密文 = CTR_K(inc32(J0), P)，
T = MSB_16(CIPH_K(J0) XOR GHASH_H(A || C || [len(A)]_64 || [len(C)]_64))。
GHASH 的乘法在 GF(2^128) 上按 GCM 规定的"位序反转"约定实现。
"""
SBOX = None
RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


def _build_sbox():
    """由 GF(2^8) 的乘法逆元 + 仿射变换生成 S 盒，避免手抄 256 个常数。"""
    sbox = [0] * 256
    p = q = 1
    while True:
        p = (p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        q &= 0xFF
        x = q ^ ((q << 1 | q >> 7) & 0xFF) ^ ((q << 2 | q >> 6) & 0xFF) \
              ^ ((q << 3 | q >> 5) & 0xFF) ^ ((q << 4 | q >> 4) & 0xFF)
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


SBOX = _build_sbox()


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


def expand_key(key):
    """AES-128 密钥扩展：44 个 4 字节字，即 11 轮轮密钥。"""
    if len(key) != 16:
        raise ValueError('AES-128 key must be 16 bytes')
    w = [list(key[4 * i:4 * i + 4]) for i in range(4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [SBOX[b] for b in t]
            t[0] ^= RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [b for word in w for b in word]


def _add_round_key(state, rk, rnd):
    return [state[i] ^ rk[16 * rnd + i] for i in range(16)]


def encrypt_block(key_bytes, block):
    """单块 AES-128 加密。state 按列优先排布（字节 0,1,2,3 是第一列）。"""
    rk = key_bytes if isinstance(key_bytes, list) else expand_key(key_bytes)
    s = _add_round_key(list(block), rk, 0)
    for rnd in range(1, 11):
        s = [SBOX[b] for b in s]
        # ShiftRows：列优先下标 i 的行是 i % 4
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]
        if rnd != 10:
            t = list(s)
            for c in range(4):
                col = t[4 * c:4 * c + 4]
                s[4 * c + 0] = _mul(col[0], 2) ^ _mul(col[1], 3) ^ col[2] ^ col[3]
                s[4 * c + 1] = col[0] ^ _mul(col[1], 2) ^ _mul(col[2], 3) ^ col[3]
                s[4 * c + 2] = col[0] ^ col[1] ^ _mul(col[2], 2) ^ _mul(col[3], 3)
                s[4 * c + 3] = _mul(col[0], 3) ^ col[1] ^ col[2] ^ _mul(col[3], 2)
        s = _add_round_key(s, rk, rnd)
    return bytes(s)


def _gf_mul(x, y):
    """GF(2^128) 乘法，x/y 以 bit127 为最高位（大端字节序直接读成整数）。"""
    R = 0xe1000000000000000000000000000000
    z = 0
    v = y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ R
        else:
            v >>= 1
    return z


def _ghash(h, data):
    y = 0
    for i in range(0, len(data), 16):
        blk = data[i:i + 16]
        blk = blk + b'\x00' * (16 - len(blk))
        y = _gf_mul(y ^ int.from_bytes(blk, 'big'), h)
    return y


def _inc32(counter):
    return counter[:12] + ((int.from_bytes(counter[12:], 'big') + 1) % (1 << 32)).to_bytes(4, 'big')


def _ctr(key_bytes, iv, data):
    out = bytearray()
    counter = iv
    for i in range(0, len(data), 16):
        ks = encrypt_block(key_bytes, counter)
        chunk = data[i:i + 16]
        out += bytes(a ^ b for a, b in zip(chunk, ks))
        counter = _inc32(counter)
    return bytes(out)


def gcm_encrypt(key, iv, plaintext, aad=b''):
    """AES-128-GCM 加密，返回 ciphertext || tag（16 字节）。"""
    if len(iv) != 12:
        raise ValueError('GCM nonce must be 12 bytes here')
    rk = expand_key(key)
    h = int.from_bytes(encrypt_block(rk, b'\x00' * 16), 'big')
    j0 = iv + b'\x00\x00\x00\x01'
    ct = _ctr(rk, _inc32(j0), plaintext)
    pad = lambda b: b + b'\x00' * (-len(b) % 16)
    s = _ghash(h, pad(aad) + pad(ct) + (len(aad) * 8).to_bytes(8, 'big')
               + (len(ct) * 8).to_bytes(8, 'big'))
    tag = bytes(a ^ b for a, b in zip(encrypt_block(rk, j0), s.to_bytes(16, 'big')))
    return ct + tag


def gcm_decrypt(key, iv, data, aad=b''):
    """AES-128-GCM 解密；认证失败抛 ValueError。"""
    if len(data) < 16:
        raise ValueError('truncated AEAD output')
    body, tag = data[:-16], data[-16:]
    rk = expand_key(key)
    h = int.from_bytes(encrypt_block(rk, b'\x00' * 16), 'big')
    j0 = iv + b'\x00\x00\x00\x01'
    pt = _ctr(rk, _inc32(j0), body)
    pad = lambda b: b + b'\x00' * (-len(b) % 16)
    s = _ghash(h, pad(aad) + pad(body) + (len(aad) * 8).to_bytes(8, 'big')
               + (len(body) * 8).to_bytes(8, 'big'))
    want = bytes(a ^ b for a, b in zip(encrypt_block(rk, j0), s.to_bytes(16, 'big')))
    if want != tag:
        raise ValueError('authentication failed')
    return pt
