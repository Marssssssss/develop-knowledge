"""ML-DSA 伪随机采样：XOF 流 / 拒绝采样 / 挑战多项式。

对应 pq-crystals/dilithium `ref/poly.c` 的
`poly_uniform` + `poly_uniform_eta`/`rej_eta`、`poly_uniform_gamma1`/`polyz_unpack`、
`poly_challenge`（即 FIPS 204 Algorithm 29 SampleInBall）。
SHAKE128/256 直接用标准库 `hashlib`（就是 FIPS 202 的 XOF，语义等价）。
"""

import hashlib

from mldsa_params import Q, N

SHAKE128_RATE = 168
SHAKE256_RATE = 136


class Xof:
    """增量式 XOF 流。`digest(n)` 是前缀稳定的，因此重复拉长即可续流。"""

    def __init__(self, seed, nonce=None, shake256=True):
        self.st = hashlib.shake_256() if shake256 else hashlib.shake_128()
        self.st.update(bytes(seed))
        if nonce is not None:
            self.st.update(bytes([nonce & 0xFF, (nonce >> 8) & 0xFF]))
        self.buf = b""
        self.pos = 0
        self.rate = SHAKE256_RATE if shake256 else SHAKE128_RATE

    def _refill(self):
        self.buf = self.st.digest(len(self.buf) + self.rate)[-self.rate:]
        self.pos = 0

    def read(self, n):
        out = bytearray()
        while len(out) < n:
            if self.pos >= len(self.buf):
                self._refill()
            take = min(n - len(out), len(self.buf) - self.pos)
            out += self.buf[self.pos:self.pos + take]
            self.pos += take
        return bytes(out)

    def byte(self):
        if self.pos >= len(self.buf):
            self._refill()
        b = self.buf[self.pos]
        self.pos += 1
        return b


def rej_eta(buf, need, eta, out, ctr):
    """ref/poly.c rej_eta —— 每个字节切两个 4 位 nibble 做拒绝采样。

    ETA=2：接受 nibble < 15，系数 = 2 - (t mod 5)，落在 [-2, 2]。
    ETA=4：接受 nibble < 9，系数 = 4 - t，落在 [-4, 4]。
    """
    pos = 0
    while ctr < need and pos < len(buf):
        t0 = buf[pos] & 0x0F
        t1 = buf[pos] >> 4
        pos += 1
        if eta == 2:
            if t0 < 15:
                t0 = t0 - (205 * t0 >> 10) * 5
                out[ctr] = 2 - t0
                ctr += 1
            if t1 < 15 and ctr < need:
                t1 = t1 - (205 * t1 >> 10) * 5
                out[ctr] = 2 - t1
                ctr += 1
        else:
            if t0 < 9:
                out[ctr] = 4 - t0
                ctr += 1
            if t1 < 9 and ctr < need:
                out[ctr] = 4 - t1
                ctr += 1
    return ctr


def poly_uniform_eta(seed, nonce, eta):
    """ref/poly.c poly_uniform_eta —— 反复 squeeze 直到凑满 256 个系数。"""
    out = [0] * N
    ctr = 0
    st = Xof(seed, nonce)
    while ctr < N:
        ctr = rej_eta(st.read(SHAKE256_RATE), N, eta, out, ctr)
    return out


def polyz_unpack(buf, gamma1):
    """ref/packing.c polyz_unpack —— gamma1=2^17 用 18 位、2^19 用 20 位。"""
    bits = 18 if gamma1 == (1 << 17) else 20
    out = [0] * N
    if bits == 18:
        for i in range(0, N, 4):
            v = int.from_bytes(buf[9 * (i // 4):9 * (i // 4) + 9], "little")
            out[i + 0] = v & 0x3FFFF
            out[i + 1] = (v >> 18) & 0x3FFFF
            out[i + 2] = (v >> 36) & 0x3FFFF
            out[i + 3] = (v >> 54) & 0x3FFFF
    else:
        for i in range(0, N, 2):
            v = int.from_bytes(buf[5 * (i // 2):5 * (i // 2) + 5], "little")
            out[i + 0] = v & 0xFFFFF
            out[i + 1] = (v >> 20) & 0xFFFFF
    return [gamma1 - c for c in out]


def poly_uniform_gamma1(seed, nonce, gamma1):
    """ref/poly.c —— 系数落在 (gamma1 - 2^bits, gamma1]，无拒绝、直接打包。"""
    bits = 18 if gamma1 == (1 << 17) else 20
    nbytes = (N * bits + 7) // 8
    st = Xof(seed, nonce)
    return polyz_unpack(st.read(nbytes), gamma1)


def poly_challenge(seed, tau):
    """ref/poly.c poly_challenge（FIPS 204 Algorithm 29 SampleInBall）。

    Fisher-Yates 洗牌的变体：恰好 tau 个非零系数，每个为 ±1。
    `do b = next() while (b > i)` 是拒绝采样，使 b 在 [0, i] 上均匀。
    """
    st = Xof(seed, None)
    signs = int.from_bytes(st.read(8), "little")
    c = [0] * N
    for i in range(N - tau, N):
        while True:
            b = st.byte()
            if b <= i:
                break
        c[i] = c[b]
        c[b] = 1 - 2 * (signs & 1)
        signs >>= 1
    return c


def poly_uniform(seed, nonce=0):
    """ref/poly.c poly_uniform —— 矩阵 A 的元素：SHAKE128 流 + 拒绝采样到 [0, Q)。"""
    out = [0] * N
    st = Xof(seed, nonce, shake256=False)
    ctr = 0
    while ctr < N:
        t = int.from_bytes(st.read(3), "little") & 0x7FFFFF
        if t < Q:
            out[ctr] = t
            ctr += 1
    return out
