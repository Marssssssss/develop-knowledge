# -*- coding: utf-8 -*-
"""Groth16 自检：QAP 的可整除性 → 证明/验证 → 零知识 → 有毒废料的威力。"""
import random
from groth16_qap import *


def cube_circuit():
    """证明「我知道 x，使得 x³ + x + 5 = out」，out 是公开输入。
    赋值向量 a = [1, out, x, t1, t2]，其中 t1 = x², t2 = x³。"""
    #           1  out  x   t1  t2
    u = [[0, 0, 1, 0, 0],
         [0, 0, 0, 1, 0],
         [5, 0, 1, 0, 1]]
    v = [[0, 0, 1, 0, 0],
         [0, 0, 1, 0, 0],
         [1, 0, 0, 0, 0]]
    w = [[0, 0, 0, 1, 0],
         [0, 0, 0, 0, 1],
         [0, 1, 0, 0, 0]]
    return R1CS(u, v, w, n_public=1)


def selfcheck():
    ok = 0

    def chk(cond, msg):
        nonlocal ok
        assert cond, msg
        ok += 1

    rnd = random.Random(20260919)
    cs = cube_circuit()
    x = 3
    out = x ** 3 + x + 5
    a = [1, out, x, x * x, x ** 3]

    # --- R1CS 本身
    chk(cs.check(a), "合法赋值通过 R1CS 检查")
    chk(not cs.check([1, out + 1, x, x * x, x ** 3]), "公开输出改 1 ⇒ R1CS 失败")
    chk(not cs.check([1, out, x, x * x + 1, x ** 3]), "中间变量改 1 ⇒ R1CS 失败")

    # --- QAP：合法赋值 ⇒ (U·V − W) 被 t(X) 整除
    roots = [F(rnd.randrange(1, P)) for _ in range(cs.n)]
    us, vs, ws, t = qap_from_r1cs(cs, [r.v for r in roots])
    chk(len(us) == cs.m + 1, "每个变量一条 u_i(X)")
    chk(len(t) == cs.n + 1, f"t(X) 的次数 = 约束条数 = {cs.n}")
    for r in roots:
        chk(p_eval(t, r) == 0, "t(X) 在每个根处为 0")
        U, V, W = (p_eval(p, r) for p in qap_witness(us, vs, ws, a))
        chk(U * V - W == F(0), "每条约束在插值点上满足 U·V = W")

    h, rem = qap_h(us, vs, ws, t, a)
    chk(not rem, "合法赋值 ⇒ (U·V − W) / t(X) 余式为 0")
    chk(len(h) == max(0, cs.n - 1) or len(h) <= cs.n - 1, "h(X) 次数 ≤ n-2")
    _, rem_bad = qap_h(us, vs, ws, t, [1, out, x, x * x + 1, x ** 3])
    chk(rem_bad, "非法赋值 ⇒ 余式非零（这正是『证明不出来』的代数原因）")

    # --- trusted setup 与证明
    secrets = [F(rnd.randrange(2, P)) for _ in range(5)]
    srs = SRS(us, vs, ws, t, cs.ell, secret=None, randoms=secrets)
    r, s = F(rnd.randrange(2, P)), F(rnd.randrange(2, P))
    PUB = a[:cs.ell + 1]          # 公开输入是 a_0..a_ℓ（a_0 恒为 1）
    BAD = [1, out + 1]
    proof = srs.prove(a, h, r, s)
    chk(len(proof) == 3, "证明恰好 3 个群元素（A, B, C）")
    counts = []
    chk(srs.verify(a[:cs.ell + 1], proof, counts), "合法证明通过验证")
    chk(counts == [3], "验证只用了 3 次配对（论文强调的『3 个配对』）")

    # --- 验证只读公开输入
    chk(not srs.verify(BAD, proof), "公开输入改 1 ⇒ 验证失败")
    chk(srs.verify(PUB, proof), "同一证明对同一公开输入可重复验证")

    # --- 篡改证明
    A, B, C = proof
    chk(not srs.verify(PUB, (A + F(1), B, C)), "A 加 1 ⇒ 验证失败")
    chk(not srs.verify(PUB, (A, B + F(1), C)), "B 加 1 ⇒ 验证失败")
    chk(not srs.verify(PUB, (A, B, C + F(1))), "C 加 1 ⇒ 验证失败")
    # 符号群是交换的，所以 A、B 互换仍会通过 —— 这正是本模型的局限，
    # 真实 Type-3 配对下 A∈G1、B∈G2 属于不同群，互换根本不合法（见 README §7）
    chk(srs.verify(PUB, (B, A, C)), "符号群交换 ⇒ A、B 互换仍通过（模型局限，真实配对下不合法）")

    # --- 零知识：换个随机数就得到完全不同的证明，且仍然合法
    r2, s2 = F(rnd.randrange(2, P)), F(rnd.randrange(2, P))
    proof2 = srs.prove(a, h, r2, s2)
    chk(proof2 != proof, "(r, s) 不同 ⇒ 证明完全不同")
    chk(srs.verify(PUB, proof2), "另一个随机化证明同样通过验证")
    chk(proof2[0] != A and proof2[1] != B and proof2[2] != C, "三个分量都被随机化了")

    # --- 证明大小与电路规模无关
    big = R1CS(cs.u + cs.u, cs.v + cs.v, cs.w + cs.w, n_public=1)
    roots2 = [F(rnd.randrange(1, P)) for _ in range(big.n)]
    us2, vs2, ws2, t2 = qap_from_r1cs(big, [q.v for q in roots2])
    srs2 = SRS(us2, vs2, ws2, t2, big.ell, secret=None,
               randoms=[F(rnd.randrange(2, P)) for _ in range(5)])
    a2 = [1, out, x, x * x, x ** 3]
    h2, _ = qap_h(us2, vs2, ws2, t2, a2)
    p2 = srs2.prove(a2, h2, F(rnd.randrange(2, P)), F(rnd.randrange(2, P)))
    chk(len(p2) == len(proof), "约束数翻倍，证明仍然是 3 个元素（这就是 SNARK 的『 succinct 』）")

    # --- 有毒废料：拿到 τ = (α, β, δ) 就能伪造
    forged = srs.forge(PUB)
    chk(srs.verify(PUB, forged), "持有 τ 可以凭空造出通过验证的证明 —— 所以 τ 必须销毁")

    print(f"Groth16 自检通过：{ok} 项断言")
    return ok


if __name__ == "__main__":
    selfcheck()
