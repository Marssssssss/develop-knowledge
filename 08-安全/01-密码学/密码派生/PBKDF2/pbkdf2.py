# -*- coding: utf-8 -*-
"""PBKDF2: 基于口令的密钥派生(RFC 8018 §5.2), 向量取自 RFC 6070。

依据:
  - RFC 8018 §5.2: DK = PBKDF2(P,S,c,dkLen)
    F(P,S,c,i) = U1 ⊕ U2 ⊕ ... ⊕ Uc
    U1 = PRF(P, S || INT(i)), Uj = PRF(P, U(j-1)); PRF=HMAC-SHA-1
  - RFC 6070: PBKDF2-HMAC-SHA1 全部 6 组官方测试向量(含 NUL 字节用例)
  - RFC 8018 §4: 盐长度建议 ≥ 8 字节且随机; §B.1.2 HMAC 填充规则

单文件自测 5 组:
  1) RFC 6070 c=1 / c=2 基本向量
  2) RFC 6070 c=4096 标准迭代向量
  3) RFC 6070 长口令+长盐 dkLen=25 跨块向量
  4) RFC 6070 含 NUL 字节的口令与盐(\\0 转义陷阱)
  5) 迭代次数与 dkLen 的安全性: 输出互异/确定/口令雪崩
"""
import hashlib
import hmac


def pbkdf2(password: bytes, salt: bytes, iterations: int, dklen: int,
           hashname: str = "sha1") -> bytes:
    prf_len = hashlib.new(hashname).digest_size
    blocks = (dklen + prf_len - 1) // prf_len
    dk = b""
    for i in range(1, blocks + 1):
        u = hmac.new(password, salt + i.to_bytes(4, "big"), hashname).digest()
        f = u
        for _ in range(iterations - 1):
            u = hmac.new(password, u, hashname).digest()
            f = bytes(a ^ b for a, b in zip(f, u))
        dk += f
    return dk[:dklen]


def H(s):
    return bytes.fromhex(s.replace(" ", ""))


def demo():
    # --- 1) RFC 6070 c=1 / c=2 ---
    assert pbkdf2(b"password", b"salt", 1, 20) == \
        H("0c60c80f961f0e71f3a9b524af6012062fe037a6"), "c=1"
    assert pbkdf2(b"password", b"salt", 2, 20) == \
        H("ea6c014dc72d6f8ccd1ed92ace1d41f0d8de8957"), "c=2"
    print("demo1 RFC 6070 c=1 / c=2 基本向量: PASS")

    # --- 2) c=4096 标准迭代 ---
    dk = pbkdf2(b"password", b"salt", 4096, 20)
    assert dk == H("4b007901b765489abead49d926f721d065a429c1"), "c=4096"
    assert dk == hashlib.pbkdf2_hmac("sha1", b"password", b"salt", 4096, 20)
    print("demo2 RFC 6070 c=4096 标准迭代(含 hashlib 对照): PASS")

    # --- 3) 长口令+长盐 dkLen=25(跨块) ---
    dk = pbkdf2(b"passwordPASSWORDpassword",
                b"saltSALTsaltSALTsaltSALTsaltSALTsalt", 4096, 25)
    assert dk == H("3d2eec4fe41c849b80c8d83662c0e44a8b291a964cf2f07038"), "dkLen=25"
    assert dk == hashlib.pbkdf2_hmac(
        "sha1", b"passwordPASSWORDpassword",
        b"saltSALTsaltSALTsaltSALTsaltSALTsalt", 4096, 25)
    print("demo3 RFC 6070 长口令长盐 dkLen=25 跨块: PASS")

    # --- 4) 含 NUL 字节(RFC 6070 陷阱用例) ---
    dk = pbkdf2(b"pass\0word", b"sa\0lt", 4096, 16)
    assert dk == H("56fa6aa75548099dcc37d7f03425e0c3"), "NUL 用例"
    assert dk == hashlib.pbkdf2_hmac("sha1", b"pass\0word", b"sa\0lt", 4096, 16)
    print("demo4 RFC 6070 含 NUL 字节口令/盐: PASS")

    # --- 5) 确定性 / 迭代敏感性 / 口令雪崩 ---
    assert pbkdf2(b"pw", b"salt", 100, 20) == pbkdf2(b"pw", b"salt", 100, 20)
    assert pbkdf2(b"pw", b"salt", 100, 20) != pbkdf2(b"pw", b"salt", 101, 20)
    assert pbkdf2(b"pw", b"salt", 100, 20) != pbkdf2(b"px", b"salt", 100, 20)
    # 盐不同 -> 派生结果完全不同(防彩虹表)
    assert pbkdf2(b"pw", b"salt", 100, 20) != pbkdf2(b"pw", b"slat", 100, 20)
    # dkLen 大于 PRF 输出 -> 多块拼接(40 字节 SHA-1 = 2 块)
    dk40 = pbkdf2(b"pw", b"salt", 10, 40)
    assert len(dk40) == 40 and dk40[:20] != dk40[20:]
    assert dk40 == hashlib.pbkdf2_hmac("sha1", b"pw", b"salt", 10, 40)
    print("demo5 确定性/迭代敏感/口令雪崩/盐隔离/多块拼接: PASS")


if __name__ == "__main__":
    demo()
    print("ALL PASS")
