# -*- coding: utf-8 -*-
"""ML-KEM-512 自检：先验证环运算，再验证 K-PKE 与 FO 变换。"""
import os
from mlkem import *

def schoolbook(a, b):
    """朴素乘法（模 X^256+1），只作为 NTT 的参照物"""
    acc = [0] * 512
    for i, ai in enumerate(a):
        if ai:
            for j, bj in enumerate(b):
                acc[i + j] += ai * bj
    for i in range(256):
        acc[i] = (acc[i] - acc[i + 256]) % Q
    return [barrett(acc[i]) for i in range(256)]


def selfcheck():
    ok = 0

    def chk(cond, msg):
        nonlocal ok
        assert cond, msg
        ok += 1

    # --- 参数与根的存在性
    chk((Q - 1) % 256 == 0, "256 | q-1（这是二次因子存在的前提）")
    chk((Q - 1) % 512 != 0, "512 ∤ q-1 ⇒ 没有 256 次本原根 ⇒ NTT 只能做到 len=2")
    chk(len(ZETAS) == 128, "ZETAS 恰好 128 项（127 个块各用 1 个，第 0 项保留）")
    chk(ZETAS[0] == -1044, "ZETAS[0] = -1044（参考实现里它就是 MONT，索引 0 不参与蝶形）")

    # --- 归约
    chk(all(abs(barrett(x)) <= (Q - 1) // 2 for x in range(-40000, 40000, 137)),
        "Barrett 归约落在中心代表元 {-(q-1)/2, …, (q-1)/2}")
    chk(all(montgomery(x * (1 << 16)) % Q == x % Q for x in (0, 1, 7, 3328, 12345)),
        "Montgomery 归约把 a·R 折回 a（R = 2^16，输出是 [-q+1, q-1] 而非 [0,q)）")

    # --- NTT
    a = [(i * 37 + 11) % Q for i in range(256)]
    b = [(i * 91 + 5) % Q for i in range(256)]
    r = pow(1 << 16, -1, Q)
    chk(all((x - y * (1 << 16)) % Q == 0 for x, y in zip(invntt(ntt(a)), a)),
        "invntt(ntt(a)) = a·R：NTT 域自带 R = 2^16 的因子")
    chk(all((x * r - y) % Q == 0 for x, y in zip(invntt(ntt(a)), a)),
        "除掉 R 之后就是原多项式")
    chk(all((x - y) % Q == 0 for x, y in zip(poly_mul(a, b), schoolbook(a, b))),
        "invntt∘basemul∘ntt = 朴素环乘法（R 被 basemul 吸收）")
    chk(all((x - y - z) % Q == 0 for x, y, z in zip(ntt(poly_add(a, b)), ntt(a), ntt(b))),
        "NTT 是线性变换")
    chk(basemul(ntt(a), ntt(b)) != [x * y % Q for x, y in zip(ntt(a), ntt(b))],
        "basemul 不是逐点相乘（每 4 个系数才是一对二次扩张乘法）")

    # --- 中心二项分布
    for eta, seed in ((2, b"\x01" * 32), (3, b"\x02" * 32)):
        p = getnoise(seed, 0, eta)
        chk(len(p) == 256 and all(-eta <= c <= eta for c in p),
            f"CBD(η={eta})：系数恒在 [-{eta}, {eta}]")
    chk(getnoise(b"\x01" * 32, 0, 2) != getnoise(b"\x03" * 32, 0, 2), "不同 seed ⇒ 不同噪声")
    big = cbd(os.urandom(256), 2)
    mean = sum(big) / len(big)
    chk(abs(mean) < 0.25, f"CBD 期望为 0（实测均值 {mean:.3f}）")

    # --- 压缩的量化误差
    for d, bound in ((DU, 2), (DV, 104)):
        xs = list(range(Q))
        err = max(min((decompress(compress([x], d), d)[0] - x) % Q,
                      (x - decompress(compress([x], d), d)[0]) % Q) for x in xs)
        chk(err == bound, f"d={d} 时解压误差上界恰为 {bound} = ⌈q/2^{d+1}⌉（d=4 实测 104，理论 105）")
    chk(any(decompress(compress([x], DU), DU)[0] != x for x in range(Q)),
        "Compress 是有损量化（不是双射）—— 这正是 ML-KEM 解密会出错的根源")

    # --- 消息编解码
    m = os.urandom(32)
    chk(tomsg(frommsg(m)) == m, "消息 ↔ 多项式（⌈q/2⌋ 编码）往返一致")
    chk(tomsg(frommsg(bytes(32))) == bytes(32), "全零消息也能还原")

    # --- K-PKE
    seed = os.urandom(32)
    ek, dk = kpke_keygen(seed)
    for _ in range(5):
        msg = os.urandom(32)
        ct = kpke_encrypt(ek, msg, os.urandom(32))
        chk(kpke_decrypt(dk, ct) == msg, "K-PKE 解密还原明文")
    ct1 = kpke_encrypt(ek, m, b"\x11" * 32)
    ct2 = kpke_encrypt(ek, m, b"\x22" * 32)
    chk(ct_bytes(ct1) != ct_bytes(ct2), "换随机数 ⇒ 密文不同（加密是随机化的）")
    chk(ct_bytes(kpke_encrypt(ek, m, b"\x11" * 32)) == ct_bytes(ct1),
        "同一 (m, coins) ⇒ 同一密文（加密是确定性的）")

    # --- ML-KEM（FO 变换）
    ek2, dk2 = mlkem_keygen(os.urandom(64))
    k1, c1 = mlkem_encaps(ek2, m)
    chk(mlkem_decaps(dk2, c1) == k1, "封装 / 解封装得到同一共享密钥")
    chk(mlkem_decaps(dk2, c1) == mlkem_decaps(dk2, c1), "解封装是确定性的")
    k3, c3 = mlkem_encaps(ek2, os.urandom(32))
    chk(k3 != k1 and ct_bytes(c3) != ct_bytes(c1), "换明文 ⇒ 密钥与密文都变")

    # 篡改密文 ⇒ 隐式拒绝（返回一个伪随机密钥，且可复现）
    tampered = ([list(x) for x in c1[0]], list(c1[1]))
    tampered[1][0] ^= 1
    kbad = mlkem_decaps(dk2, tampered)
    chk(kbad != k1, "密文被改 ⇒ 解出的密钥不是原共享密钥")
    chk(kbad == mlkem_decaps(dk2, tampered), "隐式拒绝的结果是确定性的（并非报错）")
    chk(len(kbad) == 32, "即使拒绝也返回 32 字节，不给攻击者「成功/失败」的侧信道")

    # --- FIPS 203 的尺寸（本 demo 不做位打包，这里核对参数公式）
    chk(384 * K + 32 == 800, "ML-KEM-512 封装密钥 = 384·k + 32 = 800 字节")
    chk(32 * DU * K + 32 * DV == 768, "ML-KEM-512 密文 = 32·d_u·k + 32·d_v = 768 字节")
    chk(384 * K + (384 * K + 32) + 64 == 1632, "ML-KEM-512 解封装密钥 = 1632 字节")

    print(f"ML-KEM-512 自检通过：{ok} 项断言")
    return ok


if __name__ == "__main__":
    selfcheck()
