"""JOSE 自检：RFC 7515 §3.3 / RFC 7516 A.3 / FIPS 197 的官方向量 + 头规则与安全防线。"""

import hashlib
import hmac
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aes
import jose

PASS = 0
FAIL = 0
BAD = []


def ck(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        BAD.append(msg)


def raises(fn, msg):
    try:
        fn()
    except Exception as e:
        ck(True, msg)
        return e
    ck(False, msg + " —— 期望抛错但没有")
    return None


# =========================================== A. base64url 与 JSON 编码
for raw in (b"", b"f", b"fo", b"foo", b"foob", b"\xff\x00\x10"):
    ck(jose.unb64u(jose.b64u(raw)) == raw, "base64url 往返 %r" % raw)
    ck("=" not in jose.b64u(raw), "base64url 不带填充 %r" % raw)
    ck(all(c not in jose.b64u(raw) for c in "+/"), "base64url 用 URL 安全字符表")
ck(jose.josecompact_json({"alg": "HS256"}) == b'{"alg":"HS256"}',
   "JOSE 的 JSON 紧凑无空白（空白会改变签名输入）")
ck(jose.josecompact_json({"a": 1, "b": 2}) !=
   json.dumps({"a": 1, "b": 2}).encode(), "默认 json.dumps 带空格，不能直接用于 JOSE")

# ================================================ B. AES-128 / RFC 3394
k128 = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
pt16 = bytes.fromhex("00112233445566778899aabbccddeeff")
ck(aes.AES128(k128).encrypt_block(pt16).hex() == "69c4e0d86a7b0430d8cdb78070b4c55a",
   "FIPS 197 附录 C.1 的 AES-128 向量")
ck(aes.AES128(k128).decrypt_block(aes.AES128(k128).encrypt_block(pt16)) == pt16,
   "AES 加解密往返")
ck(aes.SBOX[0x53] == 0xED and aes.SBOX[0x00] == 0x63, "S 盒两个官方取值 S(0x53)=ED, S(0)=63")
ck(aes.AES128._inv_mix_columns and True, "InvMixColumns 存在（CBC 解密要用）")
ck(aes.pkcs7_pad(b"hello", 16)[-1] == 11, "PKCS#7 补 11 个字节的 0x0b")
ck(aes.pkcs7_unpad(aes.pkcs7_pad(b"hello")) == b"hello", "PKCS#7 去填充往返")
raises(lambda: aes.pkcs7_unpad(b"\x00" * 16), "填充字节为 0 时报错")
raises(lambda: aes.aes_key_unwrap(bytes(16), b"\x00" * 24), "包装密钥错误时 IV 校验失败")

# ============ C. RFC 7516 A.3：A128KW + A128CBC-HS256 的完整官方向量
KEK = jose.unb64u("GawgguFyGrWKav7AX4VKUg")
CEK = bytes([4, 211, 31, 197, 84, 157, 252, 254, 11, 100, 157, 250, 63, 170, 106,
             206, 107, 124, 212, 45, 111, 107, 9, 219, 200, 177, 0, 240, 143, 156,
             44, 207])
IV = bytes([3, 22, 60, 12, 43, 67, 104, 105, 108, 108, 105, 99, 111, 116, 104, 101])
A3_TOKEN = ("eyJhbGciOiJBMTI4S1ciLCJlbmMiOiJBMTI4Q0JDLUhTMjU2In0."
            "6KB707dM9YTIgHtLvtgWQ8mKwboJW3of9locizkDTHzBC2IlrT1oOQ."
            "AxY8DCtDaGlsbGljb3RoZQ."
            "KDlTtXchhZTGufMYmOYGS4HffxPSUrfmqCHXaI9wOGY."
            "U0m_YmjN04DJvceFICbCVQ")
ck(len(KEK) == 16, "RFC 7516 A.3 的 KEK 是 16 字节")
ck(aes.aes_key_wrap(KEK, CEK) == jose.unb64u(
    "6KB707dM9YTIgHtLvtgWQ8mKwboJW3of9locizkDTHzBC2IlrT1oOQ"),
   "RFC 3394 密钥包装结果与 RFC 7516 A.3 的 Encrypted Key 一致")
ck(aes.aes_key_unwrap(KEK, aes.aes_key_wrap(KEK, CEK)) == CEK, "包装/解包往返")
mine = jose.jwe_compact({"alg": "A128KW", "enc": "A128CBC-HS256"},
                        b"Live long and prosper.", kek=KEK, cek=CEK, iv=IV)
ck(mine == A3_TOKEN, "整串紧凑 JWE 与 RFC 7516 A.3 的官方串逐字节相同")
ck(jose.jwe_decrypt(A3_TOKEN, kek=KEK) == b"Live long and prosper.",
   "用 RFC 7516 A.3 的官方串解密得到明文")
ck(len(A3_TOKEN.split(".")) == 5, "紧凑 JWE 是 5 段（JWS 是 3 段）")
ck(jose.unb64u(A3_TOKEN.split(".")[0]) ==
   b'{"alg":"A128KW","enc":"A128CBC-HS256"}', "Protected 头解出 alg 与 enc")
# AAD 必须是 ASCII(BASE64URL(Protected))：换成别的形式就验不过
aad = A3_TOKEN.split(".")[0].encode("ascii")
ct = jose.unb64u(A3_TOKEN.split(".")[3])
tag = jose.unb64u(A3_TOKEN.split(".")[4])
ck(len(tag) == 16, "A128CBC-HS256 的 tag 是 HMAC 截断到 16 字节")
ct2, tag2 = aes.a128cbc_hs256_encrypt(CEK, IV, b"x" * len(aad), b"Live long and prosper.")
ck(tag2 != tag, "AAD 换成等长的其它字节 → tag 立刻不同（AAD 参与认证）")
ct3, _ = aes.a128cbc_hs256_encrypt(CEK, IV, aad, b"Live long and prosper!")
ck(ct3 != ct, "明文改一个字符 → 密文完全不同（CBC 扩散）")
raises(lambda: jose.jwe_decrypt(A3_TOKEN, kek=bytes(16)), "KEK 错误 → 解包 IV 校验失败")
tampered = A3_TOKEN.split(".")
tampered[3] = jose.b64u(bytes([ct[0] ^ 1]) + ct[1:])
raises(lambda: jose.jwe_decrypt(".".join(tampered), kek=KEK), "密文被改 → tag 校验失败")
# alg=dir：加密密钥段为空串
direct = jose.jwe_compact({"alg": "dir", "enc": "A128CBC-HS256"},
                          b"payload", cek=CEK, iv=IV)
ck(direct.split(".")[1] == "", "alg=dir 时 JWE Encrypted Key 是**空串**而不是省略分隔符")
ck(jose.jwe_decrypt(direct, cek=CEK) == b"payload", "dir 模式解密往返")

# ================================================== D. JWS（RFC 7515 §3.3）
JWS_A1 = ("eyJ0eXAiOiJKV1QiLA0KICJhbGciOiJIUzI1NiJ9."
          "eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6Ly9leGFt"
          "cGxlLmNvbS9pc19yb290Ijp0cnVlfQ."
          "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
JWK_K = jose.unb64u("AyM1SysPpbyDfgZld3umj1qzKObwVMkoqQ-EstJQLr_T-1qS0gZH75"
                    "aKtMN3Yj0iPS4hcgUuTwjAzZr1Z9CAow")
ck(len(JWK_K) == 64, "RFC 7515 A.1 的 oct 密钥是 64 字节")
ck(jose.jws_verify(JWS_A1, JWK_K) == jose.unb64u(JWS_A1.split(".")[1]),
   "RFC 7515 §3.3 官方 JWS 用官方密钥验证通过")
ck(JWS_A1.split(".")[2] == "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
   "官方签名串逐字符相同")
si = (JWS_A1.split(".")[0] + "." + JWS_A1.split(".")[1]).encode("ascii")
ck(jose.b64u(hmac.new(JWK_K, si, hashlib.sha256).digest()) == JWS_A1.split(".")[2],
   "签名输入是 ASCII(BASE64URL(头) ‖ '.' ‖ BASE64URL(载荷))")
ck(json.loads(jose.unb64u(JWS_A1.split(".")[0]))["typ"] == "JWT",
   "A.1 的头带 typ=JWT（RFC 8725 §3.11 显式类型）")
raises(lambda: jose.jws_verify(JWS_A1, bytes(64)), "错密钥 → 签名不匹配")
bad = JWS_A1.split(".")
payload = bytearray(jose.unb64u(bad[1]))
payload[-1] ^= 0x01                      # 末字节是 '}'（0x7D），翻成 0x7C
bad[1] = jose.b64u(bytes(payload))
raises(lambda: jose.jws_verify(".".join(bad), JWK_K), "改载荷 → 签名不匹配")
# alg 必须在允许集合里（RFC 8725 §3.1）
raises(lambda: jose.jws_verify(JWS_A1, JWK_K, allowed_algs=("RS256",)),
       "alg 不在调用方允许集合 → 拒绝（不许跟着 token 走）")
raises(lambda: jose.jws_verify(JWS_A1, JWK_K, key_alg="RS256"),
       "密钥自带 alg 与头部不一致 → 拒绝（挡密钥/算法混淆）")
# alg=none
none_tok, _ = jose.jws_sign({"alg": "none"}, b'{"x":1}', b"")
ck(none_tok.endswith("."), "alg=none 的签名段是空串")
ck(len(none_tok.split(".")) == 3, "alg=none 仍然是 3 段（不能省掉分隔符）")
raises(lambda: jose.jws_verify(none_tok, b""), "默认不允许 alg=none（RFC 8725 §3.2）")
ck(jose.jws_verify(none_tok, b"", allowed_algs=("none",)) == b'{"x":1}',
   "显式允许 none 时才接受")
raises(lambda: jose.jws_verify(none_tok[:-1] + "A", b"", allowed_algs=("none",)),
       "alg=none 但签名段非空 → 拒绝")

# ============================================= E. 头部规则与 JSON 序列化
raises(lambda: jose.jws_sign({"alg": "HS256", "crit": ["exp"]}, b"p", b"k"),
       "crit 引用了不在头里的名字 → 拒绝（RFC 7515 §4.1.11）")
e = raises(lambda: jose.jws_sign({"alg": "HS256", "crit": ["x"], "x": 1}, b"p", b"k"),
           "crit 引用了本端不认识的扩展头 → 拒绝")
ck(jose.jws_sign({"alg": "HS256", "crit": ["x"], "x": 1}, b"p", b"k",
                 crit_known=("x",))[0].count(".") == 2,
   "crit 的扩展头被理解且同在头里 → 允许")
gen = jose.jws_general(b"shared payload", [])
jose.jws_general_add(gen, {"alg": "HS256"}, JWK_K, unprotected={"kid": "k1"})
jose.jws_general_add(gen, {"alg": "HS256"}, bytes(16), unprotected={"kid": "k2"})
ck(len(gen["signatures"]) == 2, "General JSON 可以带多个签名")
ck(jose.jws_general_verify(gen, JWK_K)["kid"] == "k1",
   "按密钥挑出对应签名，并合出完整 JOSE 头（protected + unprotected）")
raises(lambda: jose.jws_general_add(gen, {"alg": "HS256", "kid": "a"}, bytes(16),
                                    unprotected={"kid": "b"}),
       "protected 与 unprotected 成员名重叠 → 拒绝")
ck(len(jose.jws_general(b"p", [])) == 2, "General 序列化顶层是 payload + signatures")
ck(jose.jws_general_verify(gen, bytes(16))["kid"] == "k2", "另一个密钥命中第二个签名")

print("PASS=%d FAIL=%d" % (PASS, FAIL))
for m in BAD[:12]:
    print("  FAIL:", m)
sys.exit(1 if FAIL else 0)
