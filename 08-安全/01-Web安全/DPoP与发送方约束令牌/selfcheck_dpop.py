"""DPoP与发送方约束令牌 自检。

官方向量（实读 RFC 9449 / RFC 7638 / RFC 8705 原文后抄录）：
  * RFC 9449 Figure 9：示例 DPoP 公钥的 cnf.jkt = 0ZcOCORZNYy-DWpqq30jZyJGHTN0d2HglBV3uiguA4I
  * RFC 9449 Figure 14：令牌 "Kz~8mXK1EalYznwH-LC-1fBAo.4Ljp~zsPE_NeO.gxU" 的
    ath = fUHyO2r2Z3DZ53EsNrWBb0xWXoaNy59IiKCAqksmQEo
"""

from dpop import (KeyPair, Request, DPoPServer, ath_of, jwk_public,
                  jwk_thumbprint, make_proof, strip_query_fragment,
                  x5t_s256, mtls_resource_check, jws_verify)

OK = 0
FAIL = []


def ck(name, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAIL.append("%s  %s" % (name, detail))


def eq(name, got, want):
    ck(name, got == want, "got=%r want=%r" % (got, want))


def why(ok_r, want_prefix):
    return (not ok_r[0]) and ok_r[1].startswith(want_prefix)


# ------------------------------------------- 官方 jkt / ath 向量（跨算法独立核对）
official_jwk = {
    "crv": "P-256",
    "kty": "EC",
    "x": "l8tFrhx-34tV3hRICRDY9zCkDlpBhF42UQUfWVAWBFs",
    "y": "9VE4jf_Ok_o64zbTTlcuNJajHmt6v9TDVrU0CdvGRDA",
}
eq("jkt 复现 RFC9449 Figure 9", jwk_thumbprint(official_jwk),
   "0ZcOCORZNYy-DWpqq30jZyJGHTN0d2HglBV3uiguA4I")
eq("ath 复现 RFC9449 Figure 14",
   ath_of("Kz~8mXK1EalYznwH-LC-1fBAo.4Ljp~zsPE_NeO.gxU"),
   "fUHyO2r2Z3DZ53EsNrWBb0xWXoaNy59IiKCAqksmQEo")

# ------------------------------------------------------------ 基本工具与签名
kp = KeyPair(seed=20260920)
proof = make_proof(kp, "jti-1", "POST", "https://server.example.com/token", 1562262616)
ok_v, why_v, head, pl = jws_verify(proof)
ck("自签 proof 能被公钥验过", ok_v, why_v)
eq("typ 是 dpop+jwt", head["typ"], "dpop+jwt")
eq("jwk 只有 kty/e/n", sorted(head["jwk"]), ["e", "kty", "n"])
eq("htu 去掉 query", strip_query_fragment("https://a/x?q=1"), "https://a/x")
eq("htu 去掉 fragment", strip_query_fragment("https://a/x#f"), "https://a/x")

forged = proof[:proof.rfind(".") + 1] + "AAAA"
ok_f, why_f, _h, _p = jws_verify(forged)
ck("篡改签名被拒", (not ok_f) and "签名" in why_f, why_f)

# ------------------------------------------------------------ §4.3 逐条校验
AT1 = "Kz~8mXK1EalYznwH-LC-1fBAo.4Ljp~zsPE_NeO.gxU"
AT2 = " rotated-token-value-with-different-bytes "
jkt1 = jwk_thumbprint(jwk_public(kp))
rs = DPoPServer("resource", now=1562262620)
res_uri = "https://resource.example.org/protectedresource"

good = make_proof(kp, "jti-A", "GET", res_uri, 1562262618, ath=ath_of(AT1))
ok_r = rs.check_proof(Request("GET", res_uri + "?a=1", [good],
                              authorization="DPoP " + AT1, access_token=AT1),
                      expected_token=AT1, expected_jkt=jkt1)
ck("正常 DPoP 请求通过（htu 可带 query）", ok_r[0], ok_r[1])

ck("E1 两个 DPoP 头被拒",
   why(rs.check_proof(Request("GET", res_uri, [good, good]),
                      expected_token=AT1, expected_jkt=jkt1), "E1"))
ck("E1 完全没有 DPoP 头被拒（裸 Bearer 不可用）",
   why(rs.check_proof(Request("GET", res_uri, [],
                              authorization="Bearer " + AT1),
                      expected_token=AT1, expected_jkt=jkt1), "E1"))

ck("E4 typ 不是 dpop+jwt 被拒",
   why(rs.check_proof(Request("GET", res_uri,
                              [make_proof(kp, "j", "GET", res_uri,
                                          1562262618, typ="JWT")]),
                      expected_token=AT1, expected_jkt=jkt1), "E4"))

ck("E5 alg=HS256 被拒",
   why(rs.check_proof(Request("GET", res_uri,
                              [make_proof(kp, "j", "GET", res_uri,
                                          1562262618, alg="HS256")]),
                      expected_token=AT1, expected_jkt=jkt1), "E5"))
ck("E5 alg=none 被拒",
   why(rs.check_proof(Request("GET", res_uri,
                              [make_proof(kp, "j", "GET", res_uri,
                                          1562262618, alg="none")]),
                      expected_token=AT1, expected_jkt=jkt1), "E5"))

ck("E7 jwk 里带私钥 d 被拒",
   why(rs.check_proof(Request("GET", res_uri,
                              [make_proof(kp, "j", "GET", res_uri,
                                          1562262618, leak_d=True)]),
                      expected_token=AT1, expected_jkt=jkt1), "E7"))

ck("E8 htm 不匹配被拒（GET 证明用在 POST 上）",
   why(rs.check_proof(Request("POST", res_uri, [good]),
                      expected_token=AT1, expected_jkt=jkt1), "E8"))
ck("E9 htu 不匹配被拒（换路径）",
   why(rs.check_proof(Request("GET", "https://resource.example.org/other",
                              [good]), expected_token=AT1,
                      expected_jkt=jkt1), "E9"))

old = make_proof(kp, "jti-old", "GET", res_uri, 1562262618 - 600,
                 ath=ath_of(AT1))
ck("E11 iat 超窗被拒",
   why(rs.check_proof(Request("GET", res_uri, [old]),
                      expected_token=AT1, expected_jkt=jkt1), "E11"))

# §7 AT1/AT2 替换：同一把钥匙两个令牌，抓到的签名被换到另一个令牌上
ck("E12a proof 换到另一个访问令牌上被拒（ath 绑定）",
   why(rs.check_proof(Request("GET", res_uri, [good],
                              authorization="DPoP " + AT2, access_token=AT2),
                      expected_token=AT2, expected_jkt=jkt1), "E12a"))

kp2 = KeyPair(seed=777)
other = make_proof(kp2, "jti-B", "GET", res_uri, 1562262618, ath=ath_of(AT1))
ck("E12b 另一把钥匙签的 proof 被拒（jkt 绑定）",
   why(rs.check_proof(Request("GET", res_uri, [other]),
                      expected_token=AT1, expected_jkt=jkt1), "E12b"))
ck("无 ath 的 proof 访问资源被拒",
   why(rs.check_proof(Request("GET", res_uri,
                              [make_proof(kp, "jti-C", "GET", res_uri,
                                          1562262618)]),
                      expected_token=AT1, expected_jkt=jkt1), "E12a"))

# -------------------------------------------------- §11.1 jti 单用（防重放）
ck("jti 首次可用", rs.single_use("jti-A"))
ck("jti 二次被拒（单用检查）", not rs.single_use("jti-A"))

# ---------------------------------------------------------- §8 nonce 机制
asrv = DPoPServer("as", require_nonce=True, now=1562262600)
no_nonce = make_proof(kp, "jti-n1", "POST", "https://server.example.com/token",
                      1562262600)
ck("E10 需要 nonce 时缺 nonce 被拒",
   why(asrv.check_proof(Request("POST", "https://server.example.com/token",
                                [no_nonce])), "E10"))
n1 = asrv.issue_nonce()
with_n1 = make_proof(kp, "jti-n2", "POST", "https://server.example.com/token",
                     1562262600, nonce=n1)
ck("带上新下发的 nonce 通过",
   asrv.check_proof(Request("POST", "https://server.example.com/token",
                            [with_n1]))[0])
stale = make_proof(kp, "jti-n3", "POST", "https://server.example.com/token",
                   1562262600, nonce="N-not-issued-by-server")
ck("E10 陈旧/伪造 nonce 被拒",
   why(asrv.check_proof(Request("POST", "https://server.example.com/token",
                                [stale])), "E10"))
# §9 明确 AS 与 RS 各自下发 nonce：把 AS 的 nonce 交给同样 require_nonce 的 RS，
# 该值不在 RS 的近期窗口里，必须被拒 —— 这是「两套 nonce 独立」的可观测后果。
rs_n = DPoPServer("resource-n", require_nonce=True, now=1562262600)
rs_own = rs_n.issue_nonce()
with_rs = make_proof(kp, "jti-n4", "GET", res_uri, 1562262600, nonce=rs_own)
ck("RS 自己的 nonce 在 RS 上通过",
   rs_n.check_proof(Request("GET", res_uri, [with_rs]))[0])
ck("AS 下发的 nonce 被 RS 拒绝（两套 nonce 独立）",
   why(rs_n.check_proof(Request("GET", res_uri,
                                [make_proof(kp, "jti-n5", "GET", res_uri,
                                            1562262600, nonce=n1)])), "E10"))

# ---------------------------------------------------- §10 dpop_jkt 授权码绑定
def token_request_accepts(authorization_request_jkt, proof_compact):
    """§10：AS 计算 proof 公钥指纹，与授权请求里的 dpop_jkt 比对，不一致就拒绝。"""
    ok_v, _why_v, head, _pl = jws_verify(proof_compact)
    if not ok_v:
        return False
    return jwk_thumbprint(head["jwk"]) == authorization_request_jkt


dpop_jkt = jwk_thumbprint(jwk_public(kp))
ck("dpop_jkt 与 proof 公钥一致时 AS 受理",
   token_request_accepts(dpop_jkt, good))
ck("dpop_jkt 与 proof 公钥不一致时 AS 必须拒绝",
   not token_request_accepts(dpop_jkt, other))

# ------------------------------------------------------- RFC 8705 mTLS 对比
der_a = bytes.fromhex("3082010a0282010100") + b"\x01" * 240
der_b = bytes.fromhex("3082010a0282010100") + b"\x02" * 240
x5t = x5t_s256(der_a)
ck("x5t#S256 无尾随 '='", "=" not in x5t)
eq("x5t#S256 长度确定（256 bit → 43 字符）", len(x5t), 43)
ck("mTLS 证书一致放行",
   mtls_resource_check(x5t, der_a)[0])
res = mtls_resource_check(x5t, der_b)
ck("mTLS 证书不一致按 RFC6750 返回 invalid_token",
   (not res[0]) and res[1] == "invalid_token", str(res))
ck("mTLS 未绑定证书时不做比对（普通 Bearer 路径）",
   mtls_resource_check(None, der_b)[0])

print("OK =", OK)
if FAIL:
    print("FAILED =", len(FAIL))
    for f in FAIL:
        print("  -", f)
else:
    print("ALL OK")
