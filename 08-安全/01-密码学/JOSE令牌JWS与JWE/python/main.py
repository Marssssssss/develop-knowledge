"""JOSE 演示：官方向量复现 + 三条最容易踩的坑。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jose

BAR = "-" * 64


def main():
    print("1. 官方 JWS（RFC 7515 §3.3）：头里带 CRLF 与一个空格")
    print(BAR)
    tok = ("eyJ0eXAiOiJKV1QiLA0KICJhbGciOiJIUzI1NiJ9."
           "eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6Ly9leGFt"
           "cGxlLmNvbS9pc19yb290Ijp0cnVlfQ."
           "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
    key = jose.unb64u("AyM1SysPpbyDfgZld3umj1qzKObwVMkoqQ-EstJQLr_T-1qS0gZH75"
                      "aKtMN3Yj0iPS4hcgUuTwjAzZr1Z9CAow")
    print("   header :", jose.unb64u(tok.split(".")[0]).decode())
    print("   payload:", jose.unb64u(tok.split(".")[1]).decode())
    print("   验签   :", jose.jws_verify(tok, key).decode())
    print("   —— JSON 里多一个空格，签名就全变：这就是必须按字节复现原文的原因")

    print()
    print("2. 官方 JWE（RFC 7516 A.3）：A128KW + A128CBC-HS256")
    print(BAR)
    kek = jose.unb64u("GawgguFyGrWKav7AX4VKUg")
    cek = bytes([4, 211, 31, 197, 84, 157, 252, 254, 11, 100, 157, 250, 63, 170, 106,
                 206, 107, 124, 212, 45, 111, 107, 9, 219, 200, 177, 0, 240, 143, 156,
                 44, 207])
    iv = bytes([3, 22, 60, 12, 43, 67, 104, 105, 108, 108, 105, 99, 111, 116, 104, 101])
    jwe = jose.jwe_compact({"alg": "A128KW", "enc": "A128CBC-HS256"},
                           b"Live long and prosper.", kek=kek, cek=cek, iv=iv)
    for name, part in zip(["Protected", "Encrypted Key", "IV", "Ciphertext", "Tag"],
                          jwe.split(".")):
        print("   %-14s %s" % (name + ":", part))
    print("   解密   :", jose.jwe_decrypt(jwe, kek=kek).decode())
    print("   AAD    :", jwe.split(".")[0], "（是 ASCII(BASE64URL(Protected))，不是整串）")

    print()
    print("3. 三个坑")
    print(BAR)
    # 坑一：alg 跟着 token 走
    try:
        jose.jws_verify(tok, key, allowed_algs=("RS256",))
    except jose.VerifyError as e:
        print("   ① alg 必须在调用方白名单里      ->", e)
    # 坑二：密钥与算法不匹配
    try:
        jose.jws_verify(tok, key, key_alg="RS256")
    except jose.VerifyError as e:
        print("   ② 密钥自带 alg 与头部不一致      ->", e)
    # 坑三：alg=none
    none_tok, _ = jose.jws_sign({"alg": "none"}, b'{"admin":true}', b"")
    print("   ③ alg=none 的 token             ->", none_tok)
    try:
        jose.jws_verify(none_tok, b"")
        print("      默认竟然接受了（不该发生）")
    except jose.VerifyError as e:
        print("      默认拒绝                      ->", e)

    print()
    print("4. General JSON 序列化：一份载荷、多个签名")
    print(BAR)
    gen = jose.jws_general(b"shared payload", [])
    jose.jws_general_add(gen, {"alg": "HS256"}, key, unprotected={"kid": "key-1"})
    jose.jws_general_add(gen, {"alg": "HS256"}, bytes(16), unprotected={"kid": "key-2"})
    import json
    print("  ", json.dumps(gen, indent=None)[:120], "…")
    print("   用 key-1 验:", jose.jws_general_verify(gen, key)["kid"])
    print("   紧凑序列化做不到这点 —— 它只有一份 protected 头、一个签名。")


if __name__ == "__main__":
    main()
