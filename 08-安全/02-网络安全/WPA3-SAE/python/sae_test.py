"""
WPA3 SAE（Dragonfly）自检

RFC 7664 没有给测试向量，所以这里的断言分两类：
  A. **可由规范直接推出**的等式与结构（例如 PE 必须等于第一个种子平方后的子群元素、
     Element * PE^scalar 必须等于 PE^private），不是自造的期望值；
  B. **协议必须成立的性质**：双方推出同一 ss / kck / mk、错密码必然确认失败、
     反射攻击与非法输入必须中止。

其余部分（群参数）对照 RFC 3526 §3 的 2048 位 MODP 群原文。
"""

from __future__ import annotations

import random

from sae_exchange import Q_BYTES, SaeError, SaePeer, run_exchange
from sae_group import (G, P, P_BYTES, P_HEX, PWE_LABEL, Q, element_op, h,
                       hunting_and_pecking, inverse, is_probable_prime,
                       is_valid_element, kdf, scalar_op)

PW = b"correct horse battery staple"


def _rng(seed: int = 20260918):
    rnd = random.Random(seed)
    return lambda n: rnd.randrange(n)


def check_group() -> int:
    checks = 0
    assert P.bit_length() == 2048
    assert P_HEX == hex(P)[2:].upper()          # 与 RFC 3526 §3 的十六进制原文一致
    assert hex(P).startswith("0xffffffffffffffffc90fdaa2")   # 素数开头（含 2^1918 pi 段）
    assert hex(P).endswith("ffffffffffffffff")               # 结尾也是全 F
    assert Q == (P - 1) // 2
    checks += 5

    assert is_probable_prime(P)                 # 安全素数 p
    assert is_probable_prime(Q)                 # 且 (p-1)/2 也是素数
    checks += 2

    # G=2 的阶整除 q；q 是素数且 2 != 1，故阶恰为 q
    assert scalar_op(Q, G) == 1 and G != 1
    assert is_valid_element(G)
    assert not is_valid_element(P - 1)          # (-1)^q = -1，不在子群里
    assert not is_valid_element(1) and not is_valid_element(0) and not is_valid_element(P)
    checks += 5

    # 找一个非二次剩余作为「不属于子群」的样本
    non_residue = next(x for x in range(3, 100) if pow(x, Q, P) != 1)
    assert not is_valid_element(non_residue)
    assert is_valid_element(non_residue * non_residue % P)
    checks += 2

    for r in (G, P - 2, 12345, pow(G, 7, P)):
        assert element_op(r, inverse(r)) == 1
    checks += 1
    return checks


def check_pwe() -> int:
    checks = 0
    alice, bob = b"alice", b"bob"

    pe, iters = hunting_and_pecking(PW, alice, bob, k=40)
    assert is_valid_element(pe) and pe > 1
    checks += 1
    # 至少 k 次迭代（RFC 7664 §3.2 用来掩盖真实迭代数）
    assert iters == 40, iters
    checks += 1

    # 身份用 max/min 排序：双方各自视角必须算出同一个 PE
    assert pe == hunting_and_pecking(PW, bob, alice, k=1)[0]
    checks += 1
    # 不同密码 / 不同 nonce → 不同 PE
    assert pe != hunting_and_pecking(b"wrong password", alice, bob, k=1)[0]
    assert pe != hunting_and_pecking(PW, alice, bob, k=1, nonce=b"\x01")[0]
    checks += 2

    # A 类：PE 必须等于「计数器=1 的 base → KDF(len(p)+64) → mod (p-1) + 1 → ^((p-1)/q)」
    hi, lo = max(alice, bob), min(alice, bob)
    base = h(hi + lo + PW + bytes([1]))
    seed = (kdf(PWE_LABEL, base, P.bit_length() + 64) % (P - 1)) + 1
    assert pe == scalar_op((P - 1) // Q, seed)
    # 安全素数群（q=(p-1)/2）里「啄食」就是平方：任何非零 seed 的平方都在子群内，
    # 所以第一轮几乎必然命中 —— k 的意义纯粹是抗侧信道，不是「找不到」
    assert pe == pow(seed, 2, P)
    checks += 2
    return checks


def check_commit_algebra() -> int:
    checks = 0
    a = SaePeer(b"alice", PW, b"bob", k=5, rng=_rng())
    scalar, element = a.commit()
    assert 2 <= scalar < Q                            # scalar = (private+mask) mod q
    assert 2 <= a.private < Q
    assert a.mask is None                             # mask 必须已销毁
    assert is_valid_element(element)
    checks += 4

    # A 类：element * PE^scalar == PE^private（把定义代进去即可验证）
    assert element_op(element, scalar_op(scalar, a.pe)) == scalar_op(a.private, a.pe)
    checks += 1
    # 反射检查与标量校验都要求先拿到对端值，这里只验证自己生成的值不同
    assert element != a.pe and scalar != a.private
    checks += 1
    return checks


def check_exchange() -> int:
    checks = 0
    a, b = run_exchange(PW, PW, rng=_rng())
    assert a.ss == b.ss and a.ss is not None
    assert a.kck == b.kck and a.mk == b.mk
    assert a.kck != a.mk
    assert len(a.kck.to_bytes(P_BYTES, "big")) == P_BYTES == 256
    assert len(b.mk.to_bytes(P_BYTES, "big")) == 256
    checks += 5

    # 双方确认值不同（发送方标识不同），但各自都能验证对方那一个；
    # 顺序是「发送方在前」，所以拿自己的确认值去 verify 自己必然失败
    ca, cb = a.confirm(), b.confirm()
    assert ca != cb and len(ca) == 32
    assert b.verify(ca) and a.verify(cb)
    assert not a.verify(ca) and not b.verify(cb)
    checks += 4
    return checks


def check_wrong_password() -> int:
    checks = 0
    a, b = run_exchange(PW, b"wrong password", rng=_rng())
    assert a.ss != b.ss
    assert not b.verify(a.confirm()) and not a.verify(b.confirm())
    checks += 3
    # 大小写/空白不同也必须失败（不做任何「宽松规范化」）
    c, d = run_exchange(PW, PW.upper(), rng=_rng())
    assert c.ss != d.ss
    checks += 1
    return checks


def check_rejections() -> int:
    checks = 0

    def fresh():
        return SaePeer(b"alice", PW, b"bob", k=2, rng=_rng())

    a = fresh()
    s, e = a.commit()
    try:
        a.absorb(s, e)                      # 对端原样回送 → 反射攻击
        raise AssertionError("反射攻击必须被拒绝")
    except SaeError:
        checks += 1

    for bad_scalar in (0, 1, Q, Q + 5):
        b = fresh()
        b.commit()
        try:
            b.absorb(bad_scalar, e)
            raise AssertionError(f"非法标量 {bad_scalar} 必须被拒绝")
        except SaeError:
            checks += 1

    for bad_element in (0, 1, P - 1, P):
        c = fresh()
        c.commit()
        try:
            c.absorb(s, bad_element)
            raise AssertionError(f"非法元素 {bad_element} 必须被拒绝")
        except SaeError:
            checks += 1

    # 未 commit 就吸收对端提交
    d = fresh()
    try:
        d.absorb(s, e)
        raise AssertionError("未 commit 就 absorb 必须报错")
    except SaeError:
        checks += 1

    # 未拿到共享密钥就生成确认值
    e_peer = fresh()
    e_peer.commit()
    try:
        e_peer.confirm()
        raise AssertionError("没有 kck 就不能生成 confirm")
    except SaeError:
        checks += 1
    return checks


def check_mask_destroyed() -> int:
    """mask 是唯一能把 Element 还原成 PE 的值，用完必须消失。"""
    checks = 0
    a = SaePeer(b"alice", PW, b"bob", k=2, rng=_rng())
    a.commit()
    assert a.mask is None
    # 再 commit 一次会生成新的 private/mask，旧值不会残留（此处只验证可重复调用）
    first_private = a.private
    a.commit()
    assert a.private != first_private
    checks += 2
    return checks


def check_vectors() -> int:
    """跨语言一致性向量：C 与 Go 版用同一组固定标量跑同一套运算，必须逐位相同。

    这不是 RFC 给出的官方向量（RFC 7664 没有），而是**本仓库三个实现之间的契约**：
    只要三边的 sha256 摘要一致，就说明 H / KDF / 序列化宽度 / 拼接顺序全对齐了。
    """
    checks = 0
    import hashlib

    from sae_group import derive_pwe, KEY_LABEL as _KL
    priv_a, mask_a = 0x0102030405060708090A0B0C0D0E0F10, 0x1112131415161718191A1B1C1D1E1F20
    priv_b, mask_b = 0x2122232425262728292A2B2C2D2E2F30, 0x3132333435363738393A3B3C3D3E3F40
    pw, ida, idb = b"password", b"alice", b"bob"

    pe = derive_pwe(pw, ida, idb, 40)
    assert hashlib.sha256(pe.to_bytes(P_BYTES, "big")).hexdigest() == \
        "3fc647ccc7eb193dd5cb02744ab43ad28d059803187b9c31425f560ef3c49e2e"
    checks += 1

    class Fixed:
        """按 (private, mask) 顺序喂出固定标量的 rng 替身。"""

        def __init__(self, priv, mask):
            self.vals = (priv - 2, mask - 2)
            self.i = 0

        def __call__(self, n):
            v = self.vals[self.i % 2]
            self.i += 1
            assert 0 <= v < n
            return v

    a = SaePeer(ida, pw, idb, 40, rng=Fixed(priv_a, mask_a))
    b = SaePeer(idb, pw, ida, 40, rng=Fixed(priv_b, mask_b))
    sa, ea = a.commit()
    sb, eb = b.commit()
    assert sa == (priv_a + mask_a) % Q == 0x121416181A1C1E20222426282A2C2E30
    assert sb == (priv_b + mask_b) % Q == 0x525456585A5C5E60626466686A6C6E70
    checks += 2
    # 元素只比摘要（2048 位常量写进测试里没有意义）
    assert ea == inverse(scalar_op(mask_a, pe))
    assert eb == inverse(scalar_op(mask_b, pe))
    checks += 2

    a.absorb(sb, eb)
    b.absorb(sa, ea)
    assert a.ss == b.ss
    dig = lambda v: hashlib.sha256(v.to_bytes(P_BYTES, "big")).hexdigest()
    assert dig(a.ss) == "2b0064354b3485159da05a2d8c09fd75efe23e0b6e065c1662cd22eca6168eae"
    kck_digest = "eaa108559fa7ef14e65d4c0076d558374d5bf59cdeb596ac8ba542ae5ad6a0e2"
    assert dig(a.kck) == kck_digest and dig(b.kck) == kck_digest
    checks += 3
    assert a.confirm().hex() == \
        "60c111a2a5f1821ce0541eb45cddf321950354a0f3912a25d07977af7c2ad402"
    assert b.confirm().hex() == \
        "39a4888b1951f08081acc00c53952170e1fa7f1979d1a0bd44839eccaf95d28e"
    assert b.verify(a.confirm()) and a.verify(b.confirm())
    checks += 3

    # 同一交换里 kck 与 mk 必须不同（长度 2*len(p) 的 KDF 输出被对半切开）
    assert a.mk != a.kck and a.mk == b.mk
    checks += 1
    return checks


def main() -> int:
    total = 0
    for name, fn in (("群参数（RFC 3526 §3）", check_group),
                     ("密码元素狩猎与啄食", check_pwe),
                     ("提交交换的代数关系", check_commit_algebra),
                     ("共享密钥与确认值", check_exchange),
                     ("错密码必然失败", check_wrong_password),
                     ("非法输入与反射攻击", check_rejections),
                     ("mask 销毁语义", check_mask_destroyed),
                     ("跨语言一致性向量", check_vectors)):
        got = fn()
        total += got
        print(f"  {name}: {got} checks")
    print(f"sae_test: {total} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
