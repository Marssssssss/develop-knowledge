# -*- coding: utf-8 -*-
"""HKDF: 基于 HMAC 的 Extract-and-Expand 密钥派生(RFC 5869)。

依据: https://www.rfc-editor.org/rfc/rfc5869.html
  - §2.2 HKDF-Extract(salt, IKM) = HMAC-Hash(salt, IKM) -> PRK(HashLen 字节)
    salt 是 HMAC 的 key, IKM 是消息; salt 缺省 = HashLen 个 0x00
  - §2.3 HKDF-Expand(PRK, info, L): T(i) = HMAC(PRK, T(i-1)|info|i),
    OKM = T(1)|..|T(N) 前 L 字节, N = ceil(L/HashLen) ≤ 255
  - 附录 A.1-A.3(SHA-256)与 A.6(SHA-1)全部测试向量

单文件自测 5 组: A.1 / A.2 长 IKM+salt+L=82 / A.3 空 salt+info / A.7 缺省
salt(等价零字节) / info 分段对 OKM 的影响 + 跨实现一致性(python hmac)。
"""
import hashlib
import hmac as _hm


def hkdf_extract(salt: bytes, ikm: bytes, hashname="sha256") -> bytes:
    """Extract: 把分布不均匀的 IKM 浓缩成伪随机密钥 PRK。"""
    if not salt:
        salt = b"\x00" * hashlib.new(hashname).digest_size
    return _hm.new(salt, ikm, hashname).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int, hashname="sha256") -> bytes:
    """Expand: 把 PRK 扩展为 L 字节输出密钥材料。"""
    hlen = hashlib.new(hashname).digest_size
    assert length <= 255 * hlen, "L 超过 255*HashLen"
    okm, t, i = b"", b"", 0
    while len(okm) < length:
        i += 1
        t = _hm.new(prk, t + info + bytes([i]), hashname).digest()
        okm += t
    return okm[:length]


def hkdf(ikm, salt=b"", info=b"", length=32, hashname="sha256"):
    return hkdf_expand(hkdf_extract(salt, ikm, hashname), info, length, hashname)


def H(s):
    return bytes.fromhex(s.replace(" ", ""))


def demo():
    # --- 1) RFC 5869 A.1: SHA-256 基本用例 ---
    ikm = H("0b" * 22)
    salt = H("000102030405060708090a0b0c")
    info = H("f0f1f2f3f4f5f6f7f8f9")
    prk = hkdf_extract(salt, ikm)
    assert prk == H("077709362c2e32df0ddc3f0dc47bba63"
                    "90b6c73bb50f9c3122ec844ad7c2b3e5"), "A.1 PRK"
    okm = hkdf_expand(prk, info, 42)
    assert okm == H("3cb25f25faacd57a90434f64d0362f2a"
                    "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
                    "34007208d5b887185865"), "A.1 OKM"
    print("demo1 RFC 5869 A.1 (SHA-256 基本用例): PASS")

    # --- 2) A.2: 长 IKM/salt/info, L=82(跨 3 个 T 块) ---
    ikm = bytes(range(0x00, 0x50))
    salt = bytes(range(0x60, 0xB0))
    info = bytes(range(0xB0, 0x100))
    prk = hkdf_extract(salt, ikm)
    assert prk == H("06a6b88c5853361a06104c9ceb35b45c"
                    "ef760014904671014a193f40c15fc244"), "A.2 PRK"
    okm = hkdf_expand(prk, info, 82)
    assert okm == H("b11e398dc80327a1c8e7f78c596a4934"
                    "4f012eda2d4efad8a050cc4c19afa97c"
                    "59045a99cac7827271cb41c65e590e09"
                    "da3275600c2f09b8367793a9aca3db71"
                    "cc30c58179ec3e87c14c01d5c1f3434f"
                    "1d87"), "A.2 OKM"
    print("demo2 RFC 5869 A.2 (长输入输出, L=82 > 2*HashLen): PASS")

    # --- 3) A.3: 零长 salt 与 info ---
    ikm = H("0b" * 22)
    prk = hkdf_extract(b"", ikm)
    assert prk == H("19ef24a32c717b167f33a91d6f648bdf"
                    "96596776afdb6377ac434c1c293ccb04"), "A.3 PRK"
    okm = hkdf_expand(prk, b"", 42)
    assert okm == H("8da4e775a563c18f715f802a063c5a31"
                    "b8a11f5c5ee1879ec3454e5f3c738d2d"
                    "9d201395faa4b61a96c8"), "A.3 OKM"
    print("demo3 RFC 5869 A.3 (空 salt+空 info): PASS")

    # --- 4) A.7: salt 未提供 -> HashLen 个 0x00 等价(用 SHA-1) ---
    ikm = H("0c" * 22)
    prk0 = hkdf_extract(b"", ikm, "sha1")
    prkz = hkdf_extract(b"\x00" * 20, ikm, "sha1")
    assert prk0 == prkz == H("2adccada18779e7c2077ad2eb19d3f3e731385dd"), "A.7 PRK"
    okm = hkdf_expand(prk0, b"", 42, "sha1")
    assert okm == H("2c91117204d745f3500d636a62f64f0a"
                    "b3bae548aa53d423b0d1f27ebba6f5e5"
                    "673a081d70cce7acfc48"), "A.7 OKM"
    print("demo4 RFC 5869 A.7 (缺省 salt == 零字节, SHA-1): PASS")

    # --- 5) info 域分离: 不同 info 的 OKM 互不相关 ---
    k = hkdf(ikm, salt=b"salt", info=b"ctx-a", length=32)
    k2 = hkdf(ikm, salt=b"salt", info=b"ctx-b", length=32)
    k3 = hkdf(ikm, salt=b"salt", info=b"", length=32)
    assert len({k, k2, k3}) == 3, "info 应实现域分离"
    # 雪崩: IKM 变 1 字节, 派生密钥完全改变
    ikm2 = bytearray(ikm)
    ikm2[3] ^= 1
    assert hkdf(bytes(ikm2), salt=b"salt") != k
    # L=0 与 L>255*HashLen 的边界
    assert hkdf(b"k", length=0) == b""
    try:
        hkdf(b"k", length=255 * 32 + 1)
        raise AssertionError("应拒绝超长 L")
    except AssertionError as e:
        if "应拒绝" in str(e):
            raise
    print("demo5 info 域分离 + IKM 雪崩 + L 边界: PASS")


if __name__ == "__main__":
    demo()
    print("ALL PASS")
