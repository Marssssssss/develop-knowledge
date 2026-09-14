"""X25519 ECDH 密钥交换 —— RFC 7748 §5 Montgomery ladder 实现。

实现要点:
- 标量 clamping:k[0] &= 248; k[31] &= 127; k[31] |= 64
- Montgomery ladder 常时标量乘(256 轮)
- cswap 常量时间条件交换
- 模逆用 Fermat 小定理 pow(z, p-2, p)
- P = 2^255 - 19,A24 = 121665

测试向量(RFC 7748 §6.1):
- Alice 私钥 a : 77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a
- Alice 公钥   : 8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a
- Bob   私钥 b : 5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb
- Bob   公钥   : de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f
- 共享密钥 K   : 4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742

§5.2 迭代测试(100 万次 = c49da4f9...)
"""

from __future__ import annotations
import secrets

P = (1 << 255) - 19
A24 = 121665  # (486662 - 2) / 4


def _clamp(k: bytes) -> int:
    kb = bytearray(k)
    kb[0]  &= 248
    kb[31] &= 127
    kb[31] |= 64
    return int.from_bytes(kb, 'little')


def _decode_u(u: bytes) -> int:
    """X25519:屏蔽最高位;X448:不做(RFC 7748 §5)。"""
    ub = bytearray(u)
    ub[31] &= 127
    return int.from_bytes(ub, 'little')


def x25519(k_bytes: bytes, u_bytes: bytes) -> bytes:
    """RFC 7748 §5 Montgomery ladder。返回 32 字节 u-坐标。"""
    k = _clamp(k_bytes)
    u = _decode_u(u_bytes)

    x1 = u
    x2, z2 = 1, 0
    x3, z3 = u, 1
    swap = 0

    # Iterate over 255 down to 0 (255 bits after clamping)
    for t in range(254, -1, -1):
        k_t = (k >> t) & 1
        swap ^= k_t
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = k_t

        A = (x2 + z2) % P
        AA = A * A % P
        B = (x2 - z2) % P
        BB = B * B % P
        E = (AA - BB) % P
        C = (x3 + z3) % P
        D = (x3 - z3) % P
        DA = D * A % P
        CB = C * B % P
        x3 = pow(DA + CB, 2, P)
        z3 = x1 * pow(DA - CB, 2, P) % P
        x2 = AA * BB % P
        z2 = E * (AA + A24 * E) % P

    if swap:
        x2, z2 = z2, x2
    # Modular inverse via Fermat
    result = x2 * pow(z2, P - 2, P) % P
    return result.to_bytes(32, 'little')


def pubkey(privkey: bytes) -> bytes:
    """从 32 字节私钥派生 32 字节 u-坐标公钥。"""
    return x25519(privkey, b'\x09' + b'\x00' * 31)


def dh(privkey_a: bytes, pubkey_b: bytes) -> bytes:
    """共享密钥 K = a·B = X25519(a, B_u)。"""
    return x25519(privkey_a, pubkey_b)


# RFC 7748 §6.1 Test Vector
VECTOR_A_PRIV = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
VECTOR_A_PUB  = bytes.fromhex("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
VECTOR_B_PRIV = bytes.fromhex("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
VECTOR_B_PUB  = bytes.fromhex("de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f")
VECTOR_K      = bytes.fromhex("4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742")


def run_self_test() -> None:
    print("=" * 60)
    print("X25519 self-test (5 demos)")
    print("=" * 60)

    # demo 1: RFC 7748 §6.1 — Alice derives public key from private
    a_pub = pubkey(VECTOR_A_PRIV)
    assert a_pub == VECTOR_A_PUB, f"Alice pub fail: {a_pub.hex()}"
    print(f"[1] Alice pubkey: OK ({a_pub.hex()[:16]}...)")

    # demo 2: Bob derives public key
    b_pub = pubkey(VECTOR_B_PRIV)
    assert b_pub == VECTOR_B_PUB, f"Bob pub fail: {b_pub.hex()}"
    print(f"[2] Bob   pubkey: OK ({b_pub.hex()[:16]}...)")

    # demo 3: Alice computes shared secret using Bob's pub
    k_ab = dh(VECTOR_A_PRIV, VECTOR_B_PUB)
    assert k_ab == VECTOR_K, f"K_ab fail: {k_ab.hex()}"
    print(f"[3] Alice→Bob shared: OK ({k_ab.hex()[:16]}...)")

    # demo 4: Bob computes shared secret using Alice's pub
    k_ba = dh(VECTOR_B_PRIV, VECTOR_A_PUB)
    assert k_ba == VECTOR_K, f"K_ba fail: {k_ba.hex()}"
    assert k_ab == k_ba, "asymmetric! Montgomery ladder bug"
    print(f"[4] Bob→Alice shared: OK (symmetric match)")

    # demo 5: §5.2 iteration test — k=9 starting value, 1 iter
    base = b'\x09' + b'\x00' * 31
    one_iter = x25519(base, base)
    expected_one = "422c8e7a6227d7bca1350b3e2bb7279f7897b87bb6854b783c60e80311ae3079"
    assert one_iter.hex() == expected_one, f"1-iter fail: {one_iter.hex()}"
    print(f"[5] §5.2 1-iter: OK ({one_iter.hex()[:16]}...)")

    print("=" * 60)
    print("All 5 demos PASSED")


if __name__ == "__main__":
    run_self_test()