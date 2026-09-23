"""FROST 门限签名演示（RFC 9591）。跑法：`python main.py`"""

import frost_group as grp
import frost as F

Q = grp.Q
FIXED_H = [bytes([0xA0 + i] * 32) for i in range(8)]
FIXED_B = [bytes([0xB0 + i] * 32) for i in range(8)]


def demo_shamir():
    print("== 1. Shamir 秘密共享（附录 C.1）==")
    s = 0xC0FFEE
    shares, coeffs = F.secret_share_shard(s, [0x1111, 0x2222], 5)
    print(f"  秘密 s = {hex(s)}，5 份份额：")
    for i, y in shares:
        print(f"    f({i}) = {hex(y)}")
    got = F.secret_share_combine(shares[:3], 3)
    print(f"  任取 3 份还原：{hex(got)}  {'✓' if got == s else '✗'}")
    print(f"  只给 2 份还原：{hex(F.secret_share_combine(shares[:2], 2))}  （信息不足，不等于 s）")
    lam = [F.derive_interpolating_value([1, 2, 3], i) for i in (1, 2, 3)]
    print(f"  拉格朗日系数之和 = {sum(lam) % Q}（应为 1，这是能还原常数项的原因）")


def demo_frost():
    print("\n== 2. 两轮协议（§5.1-5.3）==")
    shares, _ = F.secret_share_shard(0x1234, [0xAA, 0xBB], 5)
    pk = grp.scalar_base_mult(0x1234)
    sk = dict(shares)
    pk_i = {i: grp.scalar_base_mult(y) for i, y in shares}
    msg = b"round two"
    signers = [1, 2, 3]
    nonces, comms = {}, {}
    for k, i in enumerate(signers):
        nonces[i], comms[i] = F.commit(sk[i], FIXED_H[k], FIXED_B[k])
    clist = sorted((i, comms[i][0], comms[i][1]) for i in signers)
    print(f"  参与者 {signers}（门限 3）")
    bfl = F.compute_binding_factors(pk, clist, msg)
    for ident, rho in bfl:
        print(f"    绑定因子 rho_{ident} = {hex(rho)[:18]}...")
    r = F.compute_group_commitment(clist, bfl)
    zs = [F.sign(i, sk[i], pk, nonces[i], msg, clist) for i in signers]
    for i, z in zip(signers, zs):
        print(f"    份额 z_{i} = {hex(z)[:18]}...")
    rr, z = F.aggregate(clist, msg, pk, zs)
    print(f"  聚合签名 (R, z)：验签 {'通过' if F.schnorr_verify(rr, z, pk, msg) else '失败'}")

    bad = list(zs)
    bad[1] = (bad[1] + 1) % Q
    _, zb = F.aggregate(clist, msg, pk, bad)
    print(f"  篡改参与者 2 的份额后：验签 {'通过' if F.schnorr_verify(rr, zb, pk, msg) else '失败'}")
    verdict = [F.verify_signature_share(i, pk_i[i], comms[i], bad[k], clist, pk, msg)
               for k, i in enumerate(signers)]
    print(f"  逐个校验份额：{verdict}  -> 精确定位到参与者 {signers[1]}")


def demo_nonce_reuse():
    print("\n== 3. 绑定因子为什么存在：nonce 复用对照 ==")
    shares, _ = F.secret_share_shard(0x1234, [0xAA, 0xBB], 5)
    sk = dict(shares)
    pk = grp.scalar_base_mult(0x1234)
    i, m1, m2 = 1, b"first", b"second"
    nonce = F.nonce_generate(sk[i], FIXED_H[0])
    d = grp.scalar_base_mult(nonce)
    r_naive = F.naive_group_commitment([d])
    z1 = F.naive_sign_share(i, sk[i], nonce, pk, m1, r_naive)
    z2 = F.naive_sign_share(i, sk[i], nonce, pk, m2, r_naive)
    c1 = F.compute_challenge(r_naive, pk, m1)
    c2 = F.compute_challenge(r_naive, pk, m2)
    rec = F.recover_sk_from_reused_nonce(z1, z2, c1, c2)
    print(f"  朴素方案（R 与消息无关）：复用 nonce 后解出 sk_1 = {hex(rec)[:22]}...")
    print(f"    与实际 sk_1 相同？{'是 —— 私钥直接泄漏' if rec == sk[i] else '否'}")
    # FROST 侧
    signers = [1, 2, 3]
    nonces, comms = {}, {}
    for k, j in enumerate(signers):
        nonces[j], comms[j] = F.commit(sk[j], FIXED_H[k], FIXED_B[k])
    cl = sorted((j, comms[j][0], comms[j][1]) for j in signers)
    b1 = F.compute_binding_factors(pk, cl, m1)
    b2 = F.compute_binding_factors(pk, cl, m2)
    h, b = nonces[i]
    r1 = F.compute_group_commitment(cl, b1)
    r2 = F.compute_group_commitment(cl, b2)
    fz1 = F.sign(i, sk[i], pk, nonces[i], m1, cl)
    fz2 = F.sign(i, sk[i], pk, nonces[i], m2, cl)
    cc1 = F.compute_challenge(r1, pk, m1)
    cc2 = F.compute_challenge(r2, pk, m2)
    frec = F.recover_sk_from_reused_nonce(fz1, fz2, cc1, cc2)
    print(f"  FROST（rho 随消息变）：同一式子解出 {hex(frec)[:22]}...")
    print(f"    与实际 sk_1 相同？{'是' if frec == sk[i] else '否 —— 解不出来'}")


def main():
    demo_shamir()
    demo_frost()
    demo_nonce_reuse()
    print("\n全部演示完成。断言版见 `python selfcheck_frost.py`。")


if __name__ == "__main__":
    main()
