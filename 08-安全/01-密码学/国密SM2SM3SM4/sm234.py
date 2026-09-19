# -*- coding: utf-8 -*-
"""国密三件套原理级实现：SM3 哈希 / SM4 分组密码 / SM2 椭圆曲线签名。

常数来源（均为实际抓取核对，不凭记忆）：
- SM3 IV、P0/P1/FF/GG/EXPAND、T_j 基值：OpenSSL crypto/sm3/sm3.c 与 sm3_local.h
- SM4 S 盒 / FK / CK：GmSSL src/sm4.c（与 OpenSSL crypto/sm4/sm4.c 的 CK、FK 一致）
- sm2p256v1 曲线参数 p/a/b/G/n：OpenSSL crypto/ec/ec_curve.c 的 _EC_sm2p256v1
- SM2 签名公式 e=H(Z||M)、r=(e+x1) mod n、s=(1+d)^-1(k-r·d) mod n：OpenSSL crypto/sm2/sm2_sign.c
"""

M32 = 0xFFFFFFFF

# ============================== SM3 ==============================
_IV = [0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
       0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E]


def _rotl(x, n):
    return ((x << n) | (x >> (32 - n))) & M32


def _p0(x):
    return x ^ _rotl(x, 9) ^ _rotl(x, 17)


def _p1(x):
    return x ^ _rotl(x, 15) ^ _rotl(x, 23)


def _ff(j, x, y, z):
    return (x ^ y ^ z) if j < 16 else ((x & y) | ((x | y) & z))


def _gg(j, x, y, z):
    return (x ^ y ^ z) if j < 16 else (z ^ (x & (y ^ z)))


def _t(j):
    """T_j：前 16 轮用 79cc4519，其后用 7a879d8a，各自循环左移 j 位"""
    base = 0x79CC4519 if j < 16 else 0x7A879D8A
    return _rotl(base, j % 32)


def sm3(data):
    h = list(_IV)
    ml = len(data) * 8
    m = bytearray(data) + b"\x80"
    m += b"\x00" * ((56 - len(m) % 64) % 64) + ml.to_bytes(8, "big")
    for off in range(0, len(m), 64):
        w = [int.from_bytes(m[off + 4 * i:off + 4 * i + 4], "big") for i in range(16)]
        for j in range(16, 68):                       # W_j：P1 变换的线性反馈
            w.append(_p1(w[j - 16] ^ w[j - 9] ^ _rotl(w[j - 3], 15))
                     ^ _rotl(w[j - 13], 7) ^ w[j - 6])
        wp = [w[j] ^ w[j + 4] for j in range(64)]     # W'_j：与 SHA-256 的关键差异
        a, b, c, d, e, f, g, hh = h
        for j in range(64):
            a12 = _rotl(a, 12)
            ss1 = _rotl((a12 + e + _t(j)) & M32, 7)
            ss2 = ss1 ^ a12
            tt1 = (_ff(j, a, b, c) + d + ss2 + wp[j]) & M32
            tt2 = (_gg(j, e, f, g) + hh + ss1 + w[j]) & M32
            d, c, b, a = c, _rotl(b, 9), a, tt1
            hh, g, f, e = g, _rotl(f, 19), e, _p0(tt2)
        # 前馈是 XOR（V^{i+1} = ABCDEFGH ⊕ V^i），不像 SHA-256 那样做加法
        h = [hi ^ vi for hi, vi in zip(h, [a, b, c, d, e, f, g, hh])]
    return b"".join(x.to_bytes(4, "big") for x in h)


# ============================== SM4 ==============================
_SBOX = [
    0xd6, 0x90, 0xe9, 0xfe, 0xcc, 0xe1, 0x3d, 0xb7, 0x16, 0xb6, 0x14, 0xc2, 0x28, 0xfb, 0x2c, 0x05,
    0x2b, 0x67, 0x9a, 0x76, 0x2a, 0xbe, 0x04, 0xc3, 0xaa, 0x44, 0x13, 0x26, 0x49, 0x86, 0x06, 0x99,
    0x9c, 0x42, 0x50, 0xf4, 0x91, 0xef, 0x98, 0x7a, 0x33, 0x54, 0x0b, 0x43, 0xed, 0xcf, 0xac, 0x62,
    0xe4, 0xb3, 0x1c, 0xa9, 0xc9, 0x08, 0xe8, 0x95, 0x80, 0xdf, 0x94, 0xfa, 0x75, 0x8f, 0x3f, 0xa6,
    0x47, 0x07, 0xa7, 0xfc, 0xf3, 0x73, 0x17, 0xba, 0x83, 0x59, 0x3c, 0x19, 0xe6, 0x85, 0x4f, 0xa8,
    0x68, 0x6b, 0x81, 0xb2, 0x71, 0x64, 0xda, 0x8b, 0xf8, 0xeb, 0x0f, 0x4b, 0x70, 0x56, 0x9d, 0x35,
    0x1e, 0x24, 0x0e, 0x5e, 0x63, 0x58, 0xd1, 0xa2, 0x25, 0x22, 0x7c, 0x3b, 0x01, 0x21, 0x78, 0x87,
    0xd4, 0x00, 0x46, 0x57, 0x9f, 0xd3, 0x27, 0x52, 0x4c, 0x36, 0x02, 0xe7, 0xa0, 0xc4, 0xc8, 0x9e,
    0xea, 0xbf, 0x8a, 0xd2, 0x40, 0xc7, 0x38, 0xb5, 0xa3, 0xf7, 0xf2, 0xce, 0xf9, 0x61, 0x15, 0xa1,
    0xe0, 0xae, 0x5d, 0xa4, 0x9b, 0x34, 0x1a, 0x55, 0xad, 0x93, 0x32, 0x30, 0xf5, 0x8c, 0xb1, 0xe3,
    0x1d, 0xf6, 0xe2, 0x2e, 0x82, 0x66, 0xca, 0x60, 0xc0, 0x29, 0x23, 0xab, 0x0d, 0x53, 0x4e, 0x6f,
    0xd5, 0xdb, 0x37, 0x45, 0xde, 0xfd, 0x8e, 0x2f, 0x03, 0xff, 0x6a, 0x72, 0x6d, 0x6c, 0x5b, 0x51,
    0x8d, 0x1b, 0xaf, 0x92, 0xbb, 0xdd, 0xbc, 0x7f, 0x11, 0xd9, 0x5c, 0x41, 0x1f, 0x10, 0x5a, 0xd8,
    0x0a, 0xc1, 0x31, 0x88, 0xa5, 0xcd, 0x7b, 0xbd, 0x2d, 0x74, 0xd0, 0x12, 0xb8, 0xe5, 0xb4, 0xb0,
    0x89, 0x69, 0x97, 0x4a, 0x0c, 0x96, 0x77, 0x7e, 0x65, 0xb9, 0xf1, 0x09, 0xc5, 0x6e, 0xc6, 0x84,
    0x18, 0xf0, 0x7d, 0xec, 0x3a, 0xdc, 0x4d, 0x20, 0x79, 0xee, 0x5f, 0x3e, 0xd7, 0xcb, 0x39, 0x48]
_FK = [0xA3B1BAC6, 0x56AA3350, 0x677D9197, 0xB27022DC]
_CK = [0x00070E15, 0x1C232A31, 0x383F464D, 0x545B6269, 0x70777E85, 0x8C939AA1, 0xA8AFB6BD, 0xC4CBD2D9,
       0xE0E7EEF5, 0xFC030A11, 0x181F262D, 0x343B4249, 0x50575E65, 0x6C737A81, 0x888F969D, 0xA4ABB2B9,
       0xC0C7CED5, 0xDCE3EAF1, 0xF8FF060D, 0x141B2229, 0x30373E45, 0x4C535A61, 0x686F767D, 0x848B9299,
       0xA0A7AEB5, 0xBCC3CAD1, 0xD8DFE6ED, 0xF4FB0209, 0x10171E25, 0x2C333A41, 0x484F565D, 0x646B7279]


def _s(x):
    return (_SBOX[x >> 24] << 24) | (_SBOX[(x >> 16) & 0xFF] << 16) \
        | (_SBOX[(x >> 8) & 0xFF] << 8) | _SBOX[x & 0xFF]


def _l(b):
    return b ^ _rotl(b, 2) ^ _rotl(b, 10) ^ _rotl(b, 18) ^ _rotl(b, 24)


def _t_sm4(x):
    return _l(_s(x))


def _t_sm4k(x):
    """密钥扩展用的 T'：L'(B) = B ⊕ (B<<<13) ⊕ (B<<<23)，与轮函数的 L 不同"""
    b = _s(x)
    return b ^ _rotl(b, 13) ^ _rotl(b, 23)


def sm4_round_keys(mk):
    k = [mk[i] ^ _FK[i] for i in range(4)]
    rk = []
    for i in range(32):
        k.append(k[i] ^ _t_sm4k(k[i + 1] ^ k[i + 2] ^ k[i + 3] ^ _CK[i]))
        rk.append(k[i + 4])
    return rk


def _sm4_block(x, rk):
    for i in range(32):
        x = [x[0] ^ _t_sm4(x[1] ^ x[2] ^ x[3] ^ rk[i]), x[1], x[2], x[3]]
        x = [x[1], x[2], x[3], x[0]]          # 非平衡 Feistel：只有 1/4 更新
    return x[3::-1]                            # 反序变换 R


def sm4_encrypt_block(mk, block):
    return b"".join(w.to_bytes(4, "big") for w in _sm4_block(
        [int.from_bytes(block[4 * i:4 * i + 4], "big") for i in range(4)], sm4_round_keys(mk)))


def sm4_decrypt_block(mk, block):
    rk = sm4_round_keys(mk)[::-1]              # 解密 = 轮密钥反序
    return b"".join(w.to_bytes(4, "big") for w in _sm4_block(
        [int.from_bytes(block[4 * i:4 * i + 4], "big") for i in range(4)], rk))


# ============================== SM2 ==============================
P = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF
A = (P - 3) % P
B = 0x28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93
N = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123
GX = 0x32C4AE2C1F1981195F9904466A39C9948FE30BBFF2660BE1715A4589334C74C7
GY = 0xBC3736A2F4F6779C59BDCEE36B692153D0A9877CC62A474002DF32E52139F0A0


def _pad_add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0] and (p[1] + q[1]) % P == 0:
        return None
    if p == q:
        lam = (3 * p[0] * p[0] + A) * pow(2 * p[1], -1, P) % P
    else:
        lam = (q[1] - p[1]) * pow(q[0] - p[0], -1, P) % P
    x = (lam * lam - p[0] - q[0]) % P
    return (x, (lam * (p[0] - x) - p[1]) % P)


def _mul(k, pt=(GX, GY)):
    r = None
    while k:
        if k & 1:
            r = _pad_add(r, pt)
        pt = _pad_add(pt, pt)
        k >>= 1
    return r


def sm2_z(id_bytes, pub):
    """Z_A = SM3(ENTL || ID || a || b || xG || yG || xA || yA)"""
    entl = len(id_bytes) * 8
    return sm3(bytes([entl >> 8, entl & 0xFF]) + id_bytes
               + A.to_bytes(32, "big") + B.to_bytes(32, "big")
               + GX.to_bytes(32, "big") + GY.to_bytes(32, "big")
               + pub[0].to_bytes(32, "big") + pub[1].to_bytes(32, "big"))


def sm2_sign(d, msg, k, id_bytes=b"1234567812345678"):
    pub = _mul(d)
    e = int.from_bytes(sm3(sm2_z(id_bytes, pub) + msg), "big")
    p1 = _mul(k)
    r = (e + p1[0]) % N
    s = (pow(1 + d, -1, N) * (k - r * d)) % N
    return r, s


def sm2_verify(pub, msg, sig, id_bytes=b"1234567812345678"):
    r, s = sig
    if not (1 <= r <= N - 1 and 1 <= s <= N - 1):
        return False
    e = int.from_bytes(sm3(sm2_z(id_bytes, pub) + msg), "big")
    t = (r + s) % N
    if t == 0:
        return False
    pt = _pad_add(_mul(s), _mul(t, pub))
    return pt is not None and (e + pt[0]) % N == r


# ============================== 自检 ==============================
def selfcheck():
    ok = 0

    def chk(cond, msg):
        nonlocal ok
        assert cond, msg
        ok += 1

    # --- SM3 官方向量
    chk(sm3(b"abc").hex() == "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0",
        "SM3(abc)")
    chk(sm3(b"abcd" * 16).hex() ==
        "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732", "SM3 512-bit 向量")
    chk(len(sm3(b"")) == 32 and sm3(b"a") != sm3(b"b"), "SM3 输出 32 字节且抗碰撞方向正确")

    # --- SM4 官方向量
    mk = [0x01234567, 0x89ABCDEF, 0xFEDCBA98, 0x76543210]
    rk = sm4_round_keys(mk)
    chk(rk[0] == 0xF12186F9 and rk[31] == 0x9124A012, "SM4 轮密钥首/末向量")
    pt = bytes.fromhex("0123456789abcdeffedcba9876543210")
    ct = sm4_encrypt_block(mk, pt)
    chk(ct.hex() == "681edf34d206965e86b3e94f536e4246", "SM4 加密向量")
    chk(sm4_decrypt_block(mk, ct) == pt, "SM4 解密还原")
    chk(sm4_decrypt_block(mk, pt) != ct, "SM4 解密 ≠ 加密（轮密钥反序而非同序）")
    chk(sm4_round_keys(mk)[::-1] == sm4_round_keys(mk)[::-1], "轮密钥序列确定")

    # --- SM2 曲线与签名
    g = (GX, GY)
    chk((g[1] * g[1] - g[0] ** 3 - A * g[0] - B) % P == 0, "G 在曲线上")
    chk(_mul(N, g) is None, "nG = 无穷远点")
    d = 0x128B2FA8BD433C6C068C8D803DFF7979B89CA6C1C41E7E36C4B0E77C8D6E1A5
    pub = _mul(d, g)
    chk((pub[1] ** 2 - pub[0] ** 3 - A * pub[0] - B) % P == 0, "公钥在曲线上")
    chk(pub != g, "dG ≠ G")

    k = 0x6CB28D99385C175C94F94E934817663FC176D925DD72B727260DBAAE1FB2F96F
    sig = sm2_sign(d, b"message digest", k)
    r, s = sig
    chk(1 <= r <= N - 1 and 1 <= s <= N - 1, "r,s ∈ [1,n-1]")
    chk(sm2_verify(pub, b"message digest", sig), "SM2 签名自验通过")
    chk(not sm2_verify(pub, b"message digese", sig), "消息改一字节 ⇒ 验证失败")
    chk(not sm2_verify(pub, b"message digest", (r, (s + 1) % N)), "s 篡改 ⇒ 验证失败")
    chk(not sm2_verify(pub, b"message digest", ((r + 1) % N, s)), "r 篡改 ⇒ 验证失败")

    # Z_A 把身份标识绑进摘要：换 ID 后立即失效
    sig2 = sm2_sign(d, b"message digest", k, id_bytes=b"ALICE123")
    chk(sig2 != sig, "换用户 ID ⇒ 签名不同（Z_A 参与摘要）")
    chk(not sm2_verify(pub, b"message digest", sig, id_bytes=b"ALICE123"), "验签方 ID 不一致 ⇒ 失败")

    # 随机数 k 重用 ⇒ 私钥直接可解
    m1, m2 = b"order-001", b"order-002"
    r1, s1 = sm2_sign(d, m1, k)
    r2, s2 = sm2_sign(d, m2, k)
    # SM2 的 r 掺了消息摘要（r = e + x1），所以复用 k 时 r 不相等，
    # 但 r 之差恰等于两条消息的 e 之差 —— 这就是 SM2 的 k 重用泄漏信号
    z = sm2_z(b"1234567812345678", pub)
    e1 = int.from_bytes(sm3(z + m1), "big")
    e2 = int.from_bytes(sm3(z + m2), "big")
    chk((r2 - r1) % N == (e2 - e1) % N, "k 重用时 r 之差 = 消息摘要之差（SM2 特有的泄漏特征）")
    chk(r1 != r2, "SM2 复用 k 不会像 ECDSA 那样露出 r 相同")
    rec = (s1 - s2) * pow(r2 - r1 - s1 + s2, -1, N) % N
    chk(rec == d, "k 重用可反解私钥 d = (s1-s2)/(r2-r1-s1+s2)")

    # (1+d) 必须可逆：d = n-1 时退化
    chk((1 + (N - 1)) % N == 0, "d = n-1 时 (1+d) ≡ 0，SM2 该分支下无逆元")
    chk(sm2_sign(0x1234, b"x", k)[0] != 0, "正常参数下 r ≠ 0")

    print(f"国密 SM2/SM3/SM4 自检通过：{ok} 项断言")
    return ok


if __name__ == "__main__":
    selfcheck()
