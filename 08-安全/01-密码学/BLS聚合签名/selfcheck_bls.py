from bls_aggregate import *

# ---------------------------------------------------------------- 自检
def selfcheck():
    ok = 0

    def chk(cond, msg):
        nonlocal ok
        assert cond, msg
        ok += 1

    # --- 配对本身的性质
    e0 = pairing(G, G)
    chk(e0 != (1, 0), "e(G,G) ≠ 1（非退化）")
    chk(f2_pow(e0, R) == (1, 0), "e(G,G)^r = 1（落到 μ_r）")
    a, b = 12345, 6789
    chk(pairing(pt_mul(a, G), pt_mul(b, G)) == f2_pow(e0, a * b % R),
        "双线性：e(aG, bG) = e(G,G)^{ab}")
    chk(pairing(pt_add(pt_mul(11, G), pt_mul(22, G)), G)
        == f2_mul(pairing(pt_mul(11, G), G), pairing(pt_mul(22, G), G)),
        "第一自变量可加")
    chk(pairing(G, pt_neg(G)) == f2_inv(e0), "e(G, -G) = e(G,G)^{-1}")
    # --- hash_to_point
    hp = hash_to_point(b"hello")
    chk(on_curve(hp), "hash_to_point 落点满足曲线方程")
    chk(pt_mul(R, hp) is None, "乘共因子后落在 r 阶子群")
    chk(hash_to_point(b"hello") == hp, "确定性")
    chk(hash_to_point(b"hellp") != hp, "消息改一字节 ⇒ 落点不同")

    # --- 单签
    sk = 0x2A3B4C
    pk = keygen(sk)
    chk(on_curve(pk) and pt_mul(R, pk) is None, "公钥是 r 阶子群里的点")
    sig = core_sign(sk, b"m1")
    chk(core_verify(pk, b"m1", sig), "CoreVerify 通过")
    chk(not core_verify(pk, b"m2", sig), "换消息 ⇒ 失败")
    chk(not core_verify(pt_mul(2, pk), b"m1", sig), "换公钥 ⇒ 失败")
    chk(not core_verify(pk, b"m1", pt_add(sig, G)), "签名加一个 G ⇒ 失败")

    # --- 聚合：n 个签名压成一个群元素
    sks = [0x1111, 0x2222, 0x3333]
    pks = [keygen(s) for s in sks]
    msgs = [b"tx-1", b"tx-2", b"tx-3"]
    sigs = [core_sign(s, m) for s, m in zip(sks, msgs)]
    agg = aggregate(sigs)
    chk(core_aggregate_verify(pks, msgs, agg), "聚合签名一次验全通过")
    chk(aggregate_verify(pks, msgs, agg), "Basic scheme 的 AggregateVerify 也通过")
    chk(not core_aggregate_verify(pks, msgs, pt_add(agg, G)), "聚合签名被改 ⇒ 失败")
    chk(not core_aggregate_verify(pks[::-1], msgs, agg), "公钥与消息错位 ⇒ 失败")
    chk(agg is not None and on_curve(agg), "聚合结果仍是单个椭圆曲线点（不是列表）")
    # 聚合的签名数量与长度无关：再加两个仍然是一个点
    agg5 = aggregate(sigs + [core_sign(0x4444, b"tx-4"), core_sign(0x5555, b"tx-5")])
    chk(on_curve(agg5) and agg5 != agg, "5 个签名聚合后仍是 1 个点")

    # --- 重复消息会被 Basic scheme 拒绝
    chk(not aggregate_verify(pks[:2], [b"same", b"same"], aggregate(sigs[:2])),
        "消息重复 ⇒ AggregateVerify 直接拒绝")

    # --- Rogue-key 攻击
    sk_v, sk_a = 0x7777, 0x8888
    pk_v, pk_a = keygen(sk_v), keygen(sk_a)
    pk_rogue = pt_add(pk_a, pt_neg(pk_v))            # 注册一个“凭空捏造”的公钥
    m = b"evil"
    forged = pt_mul(sk_a, hash_to_point(m))          # 攻击者只知道 sk_a
    chk(aggregate_verify([pk_v, pk_rogue], [m, m], forged) is False,
        "Basic scheme 强制消息互异 ⇒ 伪造被拒")
    chk(core_aggregate_verify([pk_v, pk_rogue], [m, m], forged) is True,
        "去掉互异检查 ⇒ 伪造通过（rogue-key 攻击成立）")
    chk(pt_add(pk_v, pk_rogue) == pk_a, "rogue 公钥与受害者公钥之和 = 攻击者公钥")

    # --- 消息增强：把公钥塞进被签消息
    sigs_aug = [augment_sign(s, p, mm) for s, p, mm in zip(sks, pks, msgs)]
    chk(augment_verify(pks, msgs, aggregate(sigs_aug)), "增强方案：正常聚合通过")
    chk(not augment_verify([pk_v, pk_rogue], [m, m], forged),
        "增强方案：同一条 rogue 伪造不再成立")

    # --- 所有权证明（PoP）：攻击者无法为 rogue 公钥造出 PoP
    def pop_prove(sk_i, pk_i):
        return core_sign(sk_i, b"POP" + pk_bytes(pk_i))

    def pop_verify(pk_i, pop):
        return pairing(pop, G) == pairing(hash_to_point(b"POP" + pk_bytes(pk_i)), pk_i)

    pop_v = pop_prove(sk_v, pk_v)
    chk(pop_verify(pk_v, pop_v), "诚实方的 PoP 通过")
    chk(not pop_verify(pk_rogue, pop_prove(sk_a, pk_rogue)),
        "攻击者用 sk_a 造的 PoP 对 rogue 公钥无效（它并不知道 rogue 的私钥）")

    print(f"BLS 聚合签名自检通过：{ok} 项断言")
    return ok


if __name__ == "__main__":
    selfcheck()
