"""密码学原语的官方向量自检（RFC 7748 / 8439 / draft-xchacha），由 wg_test.py 调用。"""

from __future__ import annotations

from wg_crypto import (ZERO4, aead_decrypt, aead_encrypt, chacha20_block,
                       chacha20_xor, hchacha20, poly1305_mac, x25519,
                       x25519_keypair, xchacha20_xor,
                       xchacha20poly1305_decrypt, xchacha20poly1305_encrypt)


# ============================================================
# 5. 自检：每一项都对照 RFC 官方向量（真跑，不靠肉眼）
# ============================================================

def run(checks: int = 0) -> int:
    checks = 0

    # --- RFC 7748 §5.2 的 X25519 向量 ---
    a_priv = bytes.fromhex(
        "a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4")
    a_u = bytes.fromhex(
        "e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c")
    assert x25519(a_priv, a_u).hex() == \
        "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552"
    checks += 1
    # 第二组向量：结果含最高位溢出（必须被忽略）
    b_priv = bytes.fromhex(
        "4b66e9d4d1b4673c5ad22691957d6af5c11b6421e0ea01d42ca4169e7918ba0d")
    b_u = bytes.fromhex(
        "e5210f12786811d3f4b7959d0538ae2c31dbe7106fc03c3efc4cd549c715a493")
    assert x25519(b_priv, b_u).hex() == \
        "95cbde9476e8907d7aade45cb4b873f88b595a68799fa152e6f8f7647aac7957"
    checks += 1
    # DH 对称性：双方共享秘密相同
    s1, p1 = x25519_keypair(b"alice")
    s2, p2 = x25519_keypair(b"bob")
    assert x25519(s1, p2) == x25519(s2, p1) and checks is not None
    checks += 1
    # 低阶点（全零 u）输出全零 —— RFC 7748 §6.1 要求实现可检测
    assert x25519(s1, b"\x00" * 32) == b"\x00" * 32
    checks += 1

    # --- RFC 8439 §2.8.2 的 AEAD 向量 ---
    key = bytes(range(0x80, 0xA0))
    nonce = bytes.fromhex("070000004041424344454647")
    aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
    pt = (b"Ladies and Gentlemen of the class of '99: If I could offer you "
          b"only one tip for the future, sunscreen would be it.")
    sealed = aead_encrypt(key, nonce, aad, pt)
    assert sealed[:16].hex() == "d31a8d34648e60db7b86afbc53ef7ec2"
    assert sealed[-16:].hex() == "1ae10b594f09e26a7e902ecbd0600691"
    assert aead_decrypt(key, nonce, aad, sealed) == pt
    checks += 3
    # 篡改 tag 必须被拒绝
    bad = bytearray(sealed)
    bad[-1] ^= 1
    try:
        aead_decrypt(key, nonce, aad, bytes(bad))
        raise AssertionError("tampered tag must be rejected")
    except ValueError:
        checks += 1
    # AAD 也被绑定（改 AAD 即认证失败）
    try:
        aead_decrypt(key, nonce, aad + b"\x00", sealed)
        raise AssertionError("AAD must be authenticated")
    except ValueError:
        checks += 1

    # --- Poly1305 单独对照 RFC 8439 §2.5.2 ---
    assert poly1305_mac(
        bytes.fromhex("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b"),
        b"Cryptographic Forum Research Group").hex() == \
        "a8061dc1305136c6c22b8baf0c0127a9"
    checks += 1

    # --- ChaCha20 单独对照 RFC 8439 §2.3.2（counter=1 的密钥流）---
    # 密钥是 00 01 02 … 1f（不是 31 个零 + 01，这是本文件初稿踩过的错）
    ks = chacha20_block(bytes(range(32)), 1,
                        bytes.fromhex("000000090000004a00000000"))
    assert ks[:16].hex() == "10f1e7e4d13b5915500fdd1fa32071c4"
    checks += 1

    # --- HChaCha20 对照 draft-irtf-cfrg-xchacha §2.2.1 官方向量 ---
    assert hchacha20(bytes(range(32)),
                     bytes.fromhex("000000090000004a0000000031415927")).hex() == (
        "82413b4227b27bfed30e42508a877d73a0f9e4d58a74a853c12ec41326d3ecdc")
    checks += 1
    # --- XChaCha20-Poly1305：24 字节 nonce 的 AEAD 往返与绑定性 ---
    k24 = bytes(range(0x80, 0xA0))
    n24 = bytes(range(0x40, 0x58))
    sealed24 = xchacha20poly1305_encrypt(k24, n24, aad, pt)
    assert len(sealed24) == len(pt) + 16
    assert xchacha20poly1305_decrypt(k24, n24, aad, sealed24) == pt
    # 同一密钥换 nonce 前 16 字节 → 子密钥不同 → 解不开（HChaCha20 的作用）
    try:
        xchacha20poly1305_decrypt(k24, bytes([1]) + n24[1:], aad, sealed24)
        raise AssertionError("wrong nonce must fail")
    except ValueError:
        checks += 1
    # 与 ChaCha20-Poly1305 的关系：nonce 前 16 字节经 HChaCha20 派生后，
    # 后 8 字节补 4 个零字节即为内层 96 位 nonce（实现口径一致性的自证）
    assert xchacha20_xor(k24, n24, pt) == chacha20_xor(
        hchacha20(k24, n24[:16]), 1, ZERO4 + n24[16:24], pt)
    checks += 3

    return checks


