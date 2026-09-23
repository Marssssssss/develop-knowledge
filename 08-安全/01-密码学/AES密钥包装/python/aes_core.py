"""AES 分组密码（FIPS 197），支持 128/192/256 位密钥。

约定（与 FIPS 197 一致）：
  * 输入 16 字节按列填充状态矩阵：state[r][c] = block[4*c + r]
  * ShiftRows 第 r 行循环左移 r 字节：s'[r][c] = s[r][(c+r) % 4]
  * 轮密钥字 w[round*4+c] 的第 r 字节参与 state[r][c]

S-box 不在代码里硬编码，而是由 GF(2^8) 乘法逆 + 仿射变换现算，
这样"常数表"这一层也有可验证的来源。
"""

NB = 4
RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def xtime(a: int) -> int:
    """GF(2^8) 上乘 x，模多项式 x^8+x^4+x^3+x+1 (0x11b)。"""
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


def gmul(a: int, b: int) -> int:
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        b >>= 1
        a = xtime(a)
    return r


def _build_tables():
    inv = [0] * 256
    for x in range(256):
        if x == 0:
            inv[0] = 0
            continue
        for y in range(1, 256):
            if gmul(x, y) == 1:
                inv[x] = y
                break
    sbox = [0] * 256
    for x in range(256):
        b = inv[x]
        s = b
        for k in (1, 2, 3, 4):
            s ^= ((b << k) | (b >> (8 - k))) & 0xFF
        sbox[x] = s ^ 0x63
    inv_sbox = [0] * 256
    for x in range(256):
        inv_sbox[sbox[x]] = x
    return sbox, inv_sbox


SBOX, INV_SBOX = _build_tables()


def key_expansion(key: bytes):
    """返回 (轮密钥字列表 w, 轮数 Nr)；w[i] 是 4 字节列表。"""
    nk = len(key) // 4
    if nk not in (4, 6, 8):
        raise ValueError("AES key must be 16/24/32 bytes")
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, NB * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]                    # RotWord
            t = [SBOX[b] for b in t]             # SubWord
            t[0] ^= RCON[i // nk - 1]            # Rcon
        elif nk > 6 and i % nk == 4:
            t = [SBOX[b] for b in t]             # 256 位密钥独有
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return w, nr


def _add_round_key(st, w, rnd):
    for c in range(4):
        word = w[rnd * 4 + c]
        for r in range(4):
            st[r][c] ^= word[r]


def _sub_bytes(st):
    for r in range(4):
        for c in range(4):
            st[r][c] = SBOX[st[r][c]]


def _inv_sub_bytes(st):
    for r in range(4):
        for c in range(4):
            st[r][c] = INV_SBOX[st[r][c]]


def _shift_rows(st):
    for r in range(1, 4):
        st[r] = st[r][r:] + st[r][:r]


def _inv_shift_rows(st):
    for r in range(1, 4):
        st[r] = st[r][4 - r:] + st[r][:4 - r]


def _mix_columns(st):
    for c in range(4):
        a = [st[r][c] for r in range(4)]
        st[0][c] = gmul(a[0], 2) ^ gmul(a[1], 3) ^ a[2] ^ a[3]
        st[1][c] = a[0] ^ gmul(a[1], 2) ^ gmul(a[2], 3) ^ a[3]
        st[2][c] = a[0] ^ a[1] ^ gmul(a[2], 2) ^ gmul(a[3], 3)
        st[3][c] = gmul(a[0], 3) ^ a[1] ^ a[2] ^ gmul(a[3], 2)


def _inv_mix_columns(st):
    for c in range(4):
        a = [st[r][c] for r in range(4)]
        st[0][c] = gmul(a[0], 14) ^ gmul(a[1], 11) ^ gmul(a[2], 13) ^ gmul(a[3], 9)
        st[1][c] = gmul(a[0], 9) ^ gmul(a[1], 14) ^ gmul(a[2], 11) ^ gmul(a[3], 13)
        st[2][c] = gmul(a[0], 13) ^ gmul(a[1], 9) ^ gmul(a[2], 14) ^ gmul(a[3], 11)
        st[3][c] = gmul(a[0], 11) ^ gmul(a[1], 13) ^ gmul(a[2], 9) ^ gmul(a[3], 14)


def _flat(st):
    return bytes(st[r][c] for c in range(4) for r in range(4))


def _flat_w(w, rnd):
    return bytes(w[rnd * 4 + c][r] for c in range(4) for r in range(4))


def encrypt_block(key: bytes, block: bytes, trace=None) -> bytes:
    """trace 非空时逐轮记录 (轮号, 阶段名, 状态)，用于与 FIPS 197 附录 C 对拍。"""
    w, nr = key_expansion(key)
    st = [[block[4 * c + r] for c in range(4)] for r in range(4)]
    if trace is not None:
        trace.append((0, "k_sch", _flat_w(w, 0)))
    _add_round_key(st, w, 0)
    for rnd in range(1, nr + 1):
        if trace is not None:
            trace.append((rnd, "start", _flat(st)))
        _sub_bytes(st)
        if trace is not None:
            trace.append((rnd, "s_box", _flat(st)))
        _shift_rows(st)
        if trace is not None:
            trace.append((rnd, "s_row", _flat(st)))
        if rnd != nr:
            _mix_columns(st)
            if trace is not None:
                trace.append((rnd, "m_col", _flat(st)))
        if trace is not None:
            trace.append((rnd, "k_sch", _flat_w(w, rnd)))
        _add_round_key(st, w, rnd)
    return _flat(st)


def decrypt_block(key: bytes, block: bytes) -> bytes:
    """等价逆密码（FIPS 197 5.3 的直译，不是等价解密变换）。"""
    w, nr = key_expansion(key)
    st = [[block[4 * c + r] for c in range(4)] for r in range(4)]
    _add_round_key(st, w, nr)
    for rnd in range(nr - 1, 0, -1):
        _inv_shift_rows(st)
        _inv_sub_bytes(st)
        _add_round_key(st, w, rnd)
        _inv_mix_columns(st)
    _inv_shift_rows(st)
    _inv_sub_bytes(st)
    _add_round_key(st, w, 0)
    return bytes(st[r][c] for c in range(4) for r in range(4))
