# -*- coding: utf-8 -*-
"""ML-KEM（FIPS 203，原 Kyber）原理级实现 —— ML-KEM-512 参数集。

算法与常数对照两份权威来源：
- FIPS 203《Module-Lattice-Based Key-Encapsulation Mechanism Standard》
- pq-crystals/kyber 参考实现 ref/ 目录（params.h / ntt.c / poly.c / cbd.c /
  reduce.c / indcpa.c / kem.c），经 jsDelivr 镜像逐行读取

参数（KYBER_K = 2，即 ML-KEM-512）：
    q = 3329, n = 256, k = 2, η₁ = 3, η₂ = 2, d_u = 10, d_v = 4

关于 SHAKE：本仓库 `哈希/SHA-3-Keccak/` 已从零实现过 Keccak-f[1600]，这里不再重复一份
（重复实现是 bug 温床），直接复用标准库 hashlib 的 shake_128/256；FIPS 203 的
XOF/PRF/H/G/J 全部建在 SHAKE128/SHAKE256 之上。
"""

import hashlib

Q, N, K = 3329, 256, 2
ETA1, ETA2, DU, DV = 3, 2, 10, 4
MONT, QINV = -1044, -3327          # 2^16 mod q ； q^-1 mod 2^16
F_INVNTT = 1441                    # mont^2 / 128

ZETAS = [
    -1044, -758, -359, -1517, 1493, 1422, 287, 202, -171, 622, 1577, 182, 962, -1202, -1474, 1468,
    573, -1325, 264, 383, -829, 1458, -1602, -130, -681, 1017, 732, 608, -1542, 411, -205, -1571,
    1223, 652, -552, 1015, -1293, 1491, -282, -1544, 516, -8, -320, -666, -1618, -1162, 126, 1469,
    -853, -90, -271, 830, 107, -1421, -247, -951, -398, 961, -1508, -725, 448, -1065, 677, -1275,
    -1103, 430, 555, 843, -1251, 871, 1550, 105, 422, 587, 177, -235, -291, -460, 1574, 1653,
    -246, 778, 1159, -147, -777, 1483, -602, 1119, -1590, 644, -872, 349, 418, 329, -156, -75,
    817, 1097, 603, 610, 1322, -1285, -1465, 384, -1215, -136, 1218, -1335, -874, 220, -1187, -1659,
    -1185, -1530, -1278, 794, -1510, -854, -870, 478, -108, -308, 996, 991, 958, -1460, 1522, 1628]


def shake128(data, n):
    return hashlib.shake_128(data).digest(n)


def shake256(data, n):
    return hashlib.shake_256(data).digest(n)


def prf(seed, nonce, n):
    return shake256(seed + bytes([nonce]), n)


# ============================== 模算术 ==============================
def barrett(a):
    """Barrett 归约：把 a 折回中心代表元 {-(q-1)/2, …, (q-1)/2}"""
    v = ((1 << 26) + Q // 2) // Q
    t = ((v * a + (1 << 25)) >> 26) * Q
    return a - t


def montgomery(a):
    """Montgomery 归约：返回 a·R^{-1} mod q，R = 2^16"""
    t = (a * QINV) & 0xFFFF
    if t > 0x7FFF:
        t -= 0x10000
    return (a - t * Q) >> 16


def fqmul(a, b):
    return montgomery(a * b)


# ============================== NTT ==============================
def ntt(r):
    """不完全 NTT：X^256+1 在 Z_q 上只分解成 128 个二次因子，递归到 len=2 就停。

    注意 NTT 域自带一个 Montgomery 因子 R = 2^16：invntt(ntt(a)) == a·R mod q。
    这是参考实现的既有设计（basemul 会把 R 吸收掉），见 README §4。
    """
    r = [x % Q for x in r]
    k = 1
    length = 128
    while length >= 2:
        start = 0
        while start < 256:
            zeta = ZETAS[k] % Q
            k += 1
            for j in range(start, start + length):
                t = fqmul(zeta, r[j + length])
                r[j + length] = (r[j] - t) % Q
                r[j] = (r[j] + t) % Q
            start += 2 * length          # 一个蝶形块跨 2·len 个系数
        length >>= 1
    return r


def invntt(r):
    r = [x % Q for x in r]
    k = 127
    length = 2
    while length <= 128:
        start = 0
        while start < 256:
            zeta = ZETAS[k] % Q
            k -= 1
            for j in range(start, start + length):
                t = r[j]
                r[j] = (t + r[j + length]) % Q
                r[j + length] = fqmul(zeta, (r[j + length] - t) % Q)
            start += 2 * length
        length <<= 1
    return [fqmul(x, F_INVNTT) % Q for x in r]


def basemul(a, b):
    """NTT 域乘法：每 4 个系数 = 两次 Z_q[X]/(X^2 - ζ) 上的乘法。
    invntt(basemul(ntt(a), ntt(b))) 恰好等于朴素乘法结果（R 被吸收）。"""
    out = [0] * 256
    for i in range(0, 256, 4):
        z = ZETAS[64 + i // 4] % Q
        for s, zz in ((0, z), (2, (-z) % Q)):
            a0, a1, b0, b1 = a[i + s], a[i + s + 1], b[i + s], b[i + s + 1]
            out[i + s] = (fqmul(a0, b0) + fqmul(fqmul(a1, b1), zz)) % Q
            out[i + s + 1] = (fqmul(a0, b1) + fqmul(a1, b0)) % Q
    return out


def poly_mul(a, b):
    """环 R_q = Z_q[X]/(X^256+1) 上的乘法"""
    return invntt(basemul(ntt(a), ntt(b)))


# ============================== 多项式 ==============================
def poly_add(a, b):
    return [(x + y) % Q for x, y in zip(a, b)]


def poly_sub(a, b):
    return [(x - y) % Q for x, y in zip(a, b)]


def poly_reduce(a):
    return [barrett(x) for x in a]


def cbd(buf, eta):
    """中心二项分布：a - b，a/b 各是 η 个比特的 popcount"""
    out = []
    if eta == 2:
        for i in range(N // 8):
            t = int.from_bytes(buf[4 * i:4 * i + 4], "little")
            d = t & 0x55555555
            d += (t >> 1) & 0x55555555
            for j in range(8):
                out.append(((d >> (4 * j + 0)) & 0x3) - ((d >> (4 * j + 2)) & 0x3))
    else:
        for i in range(N // 4):
            t = int.from_bytes(buf[3 * i:3 * i + 3], "little")
            d = t & 0x00249249
            d += (t >> 1) & 0x00249249
            d += (t >> 2) & 0x00249249
            for j in range(4):
                out.append(((d >> (6 * j + 0)) & 0x7) - ((d >> (6 * j + 3)) & 0x7))
    return out


def getnoise(seed, nonce, eta):
    return cbd(prf(seed, nonce, (N * eta) // 4), eta)


def compress(a, d):
    return [(((x << d) + Q // 2) // Q) % (1 << d) for x in a]


def decompress(a, d):
    return [((x * Q) + (1 << (d - 1))) >> d for x in a]


def frommsg(m):
    out = []
    for i in range(N // 8):
        for j in range(8):
            out.append((Q + 1) // 2 if (m[i] >> j) & 1 else 0)
    return out


def tomsg(a):
    out = bytearray(N // 8)
    for i in range(N // 8):
        for j in range(8):
            out[i] |= ((((a[8 * i + j] << 1) + Q // 2) // Q) & 1) << j
    return bytes(out)


# ============================== 矩阵与向量 ==============================
def gen_matrix(rho):
    """Â[i][j] = 用 SHAKE128(rho ‖ j ‖ i) 拒绝采样出的多项式（已是 NTT 域输入）"""
    a = [[None] * K for _ in range(K)]
    for i in range(K):
        for j in range(K):
            coeffs, buf, idx = [], shake128(rho + bytes([j, i]), 3 * N), 0
            while len(coeffs) < N:
                if idx + 3 > len(buf):
                    buf, idx = shake128(rho + bytes([j, i]) + bytes([len(coeffs)]), 3 * N), 0
                d1 = buf[idx] | ((buf[idx + 1] & 0xF) << 8)
                d2 = (buf[idx + 1] >> 4) | (buf[idx + 2] << 4)
                idx += 3
                if d1 < Q:
                    coeffs.append(d1)
                if d2 < Q and len(coeffs) < N:
                    coeffs.append(d2)
            a[i][j] = coeffs
    return a


def vec_ntt(v):
    return [ntt(x) for x in v]


def dot(a_hat, b_hat):
    acc = basemul(a_hat[0], b_hat[0])
    for j in range(1, K):
        acc = poly_add(acc, basemul(a_hat[j], b_hat[j]))
    return acc


# ============================== K-PKE（IND-CPA 层） ==============================
def mat_ntt(rho):
    """Â[i][j] = NTT(A_ij)，A_ij 由 SHAKE128(rho ‖ j ‖ i) 拒绝采样而来"""
    return [[ntt(x) for x in row] for row in gen_matrix(rho)]


def kpke_keygen(seed64):
    """t = A·s + e（噪声在**正常域**里加，不能加在 NTT 域里再 invntt）"""
    rho_sig = shake256(seed64, 64)
    rho, sigma = rho_sig[:32], rho_sig[32:]
    a_ntt = mat_ntt(rho)
    s = [getnoise(sigma, i, ETA1) for i in range(K)]
    e = [getnoise(sigma, K + i, ETA1) for i in range(K)]
    s_hat = vec_ntt(s)
    t = [poly_reduce(poly_add(invntt(dot(a_ntt[i], s_hat)), e[i])) for i in range(K)]
    return (t, rho), s


def kpke_encrypt(ek, m, coins):   # u = Aᵀ·r + e₁；v = tᵀ·r + e₂ + ⌈q/2⌋·m
    t, rho = ek
    a_ntt = mat_ntt(rho)
    t_hat = vec_ntt(t)
    r = [getnoise(coins, i, ETA1) for i in range(K)]
    e1 = [getnoise(coins, K + i, ETA2) for i in range(K)]
    e2 = getnoise(coins, 2 * K, ETA2)
    r_hat = vec_ntt(r)
    # 转置：u_i = Σ_j A_ji · r_j
    u = [poly_reduce(poly_add(
        invntt(dot([a_ntt[j][i] for j in range(K)], r_hat)), e1[i])) for i in range(K)]
    v = poly_reduce(poly_add(poly_add(invntt(dot(t_hat, r_hat)), e2), frommsg(m)))
    return [compress(x, DU) for x in u], compress(v, DV)


def kpke_decrypt(dk, ct):
    """m' = Compress(v − sᵀ·u)，噪声足够小时能还原回 m"""
    u_c, v_c = ct
    u = [decompress(x, DU) for x in u_c]
    v = decompress(v_c, DV)
    s_hat = vec_ntt(dk)
    mp = poly_reduce(poly_sub(v, invntt(dot(s_hat, vec_ntt(u)))))
    return tomsg(mp)


# ============================== ML-KEM（FO 变换） ==============================
def mlkem_keygen(seed):
    """seed = d(32) ‖ z(32)；z 是隐式拒绝用的后备随机"""
    d, z = seed[:32], seed[32:64]
    ek, dk_pke = kpke_keygen(d)
    return ek, (dk_pke, ek, z)


def mlkem_encaps(ek, m):
    h_ek = shake256(ek[1], 32)
    g = shake256(m + h_ek, 64)
    kbar, r = g[:32], g[32:]
    ct = kpke_encrypt(ek, m, r)
    return kbar, ct


def ct_bytes(ct):   # 把密文规范编码成字节，供重加密比较与 J(·) 使用
    u_c, v_c = ct
    buf = b""
    for poly in u_c:                      # d_u = 10 位，两个字节装一个系数
        for x in poly:
            buf += bytes([x & 0xFF, x >> 8])
    for x in v_c:                         # d_v = 4 位，一个字节装一个系数
        buf += bytes([x & 0xFF])
    return buf


def mlkem_decaps(dk, ct):
    dk_pke, ek, z = dk
    h_ek = shake256(ek[1], 32)
    m2 = kpke_decrypt(dk_pke, ct)
    g = shake256(m2 + h_ek, 64)
    kbar2, r2 = g[:32], g[32:]
    ct2 = kpke_encrypt(ek, m2, r2)
    if ct_bytes(ct) != ct_bytes(ct2):
        kbar2 = shake256(z + ct_bytes(ct), 32)   # 隐式拒绝：返回伪随机而非「解密失败」
    return kbar2
