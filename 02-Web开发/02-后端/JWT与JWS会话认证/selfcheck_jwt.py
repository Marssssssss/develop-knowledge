"""JWT / JWS —— 自检。

断言策略：**RFC 7515 附录 A.1 / A.5 的官方向量逐字节对拍**（base64url 串与 HMAC 结果），
声明校验断言**误报集与漏报集**（哪些时刻/受众该通过、哪些不该），
算法白名单断言「alg=none 与 alg 替换都必须被拒」。
"""

from main import (NONE_HEADER_B64, OFFICIAL_HEADER, OFFICIAL_HEADER_B64,
                  OFFICIAL_JWK_K, OFFICIAL_PAYLOAD, OFFICIAL_PAYLOAD_B64,
                  OFFICIAL_SIGNATURE_B64, JwtError, b64url_decode,
                  b64url_encode, encode_jwt, official_key, parse_compact,
                  serialize, sign_hs256, signing_input, validate_claims,
                  verify_hs256)

PASS = [0]


def ok(name, cond, extra=""):
    if cond:
        PASS[0] += 1
        print(f"  PASS  {name}{(' -> ' + extra) if extra else ''}")
    else:
        raise AssertionError(f"FAIL  {name}{(' -> ' + extra) if extra else ''}")


def expect_error(name, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except JwtError:
        ok(name, True)
        return
    raise AssertionError(f"FAIL  {name}（本应抛 JwtError 却通过了）")


print("== 1. base64url：标准 base64 的 -_ 变体且无 padding ==")
ok("编码不含 = padding", "=" not in b64url_encode(b"\xfb\xff\xfe"),
   b64url_encode(b"\xfb\xff\xfe"))
ok("标准 base64 的 +//+ 被替换成 -__-",
   b64url_encode(b"\xfb\xff\xfe") == "-__-", b64url_encode(b"\xfb\xff\xfe"))
ok("解码能还原原始字节", b64url_decode("-__-") == b"\xfb\xff\xfe")
for n in range(1, 12):
    raw = bytes(range(n))
    assert b64url_decode(b64url_encode(raw)) == raw, n
ok("1..11 字节全部往返一致", True)

print("== 2. RFC 7515 附录 A.1：官方向量逐字节对拍 ==")
ok("官方 Protected Header 原文含 CRLF 与行首空格",
   OFFICIAL_HEADER[13:17] == "\r\n \"", repr(OFFICIAL_HEADER[13:17]))
ok("header 的 base64url 与官方一致",
   b64url_encode(OFFICIAL_HEADER.encode("utf-8")) == OFFICIAL_HEADER_B64,
   b64url_encode(OFFICIAL_HEADER.encode("utf-8")))
ok("payload 的 base64url 与官方一致",
   b64url_encode(OFFICIAL_PAYLOAD.encode("utf-8")) == OFFICIAL_PAYLOAD_B64,
   b64url_encode(OFFICIAL_PAYLOAD.encode("utf-8"))[:40] + "...")
sig = sign_hs256(OFFICIAL_HEADER_B64, OFFICIAL_PAYLOAD_B64, official_key())
ok("HMAC-SHA256 结果与官方签名一致", sig == OFFICIAL_SIGNATURE_B64, sig)
ok("JWK 的 k 是 base64url，往返可还原",
   b64url_encode(official_key()) == OFFICIAL_JWK_K,
   f"{len(official_key())} 字节 / k 长 {len(OFFICIAL_JWK_K)} 字符")
token = serialize(OFFICIAL_HEADER_B64, OFFICIAL_PAYLOAD_B64, OFFICIAL_SIGNATURE_B64)
ok("紧凑表示恰好两个句点", token.count(".") == 2)
h, p, s = parse_compact(token)
ok("三段与官方一致", (h, p, s) == (OFFICIAL_HEADER_B64, OFFICIAL_PAYLOAD_B64,
                                   OFFICIAL_SIGNATURE_B64))
ok("签名输入是 ASCII(header.payload)，长度 = len(h)+1+len(p)",
   signing_input(h, p) == f"{h}.{p}".encode("ascii")
   and len(signing_input(h, p)) == len(h) + 1 + len(p))

print("== 3. 校验：签名、alg 白名单与 alg=none ==")
claims = verify_hs256(token, official_key())
ok("官方 token 通过校验", claims["iss"] == "joe", str(claims))
ok("exp 是数字 1300819380", claims["exp"] == 1300819380)
ok("自定义 claim 保留", claims["http://example.com/is_root"] is True)
tampered = serialize(OFFICIAL_HEADER_B64, OFFICIAL_PAYLOAD_B64,
                     "AAAA" + OFFICIAL_SIGNATURE_B64[4:])
expect_error("签名被改 → 拒绝", verify_hs256, tampered, official_key())
wrong_key = verify_hs256.__globals__["b64url_encode"](b"x" * 32)
expect_error("密钥不对 → 拒绝", verify_hs256, token, b"x" * 32)

# 算法替换攻击：把 alg 换成 none 并清空签名
none_header = b64url_encode(b'{"typ":"JWT","alg":"none"}')
unsecured = serialize(none_header, OFFICIAL_PAYLOAD_B64, "")
expect_error("alg=none 且签名为空 → 默认白名单拒绝", verify_hs256, unsecured,
             official_key())
try:
    verify_hs256(unsecured, official_key(), allowed_algs=["none"])
    ok("显式允许 none 时可通过（这正是必须配白名单的原因）", True)
except JwtError as e:
    raise AssertionError(f"FAIL 显式允许 none 时应通过：{e}")
expect_error("alg=none 但签名非空 → 拒绝", verify_hs256,
             serialize(none_header, OFFICIAL_PAYLOAD_B64, "AAAA"), official_key(),
             allowed_algs=["none"])

print("== 4. RFC 7515 附录 A.5：无保护 JWS ==")
ok("{\"alg\":\"none\"} 的 base64url 与官方一致",
   b64url_encode(b'{"alg":"none"}') == NONE_HEADER_B64, NONE_HEADER_B64)
ok("A.5 的紧凑表示以句点结尾（签名为空字节串）",
   serialize(NONE_HEADER_B64, OFFICIAL_PAYLOAD_B64, "").endswith("."))

print("== 5. RFC 7519 §4.1 exp：严格小于 ==")
base = {"iss": "joe", "sub": "u1"}
validate_claims(dict(base, exp=1000), now=999)
ok("now < exp 通过", True)
expect_error("now == exp 已过期（MUST NOT be accepted on or after）",
             validate_claims, dict(base, exp=1000), 1000)
expect_error("now > exp 已过期", validate_claims, dict(base, exp=1000), 1001)
validate_claims(dict(base, exp=1000), now=1005, leeway=10)
ok("leeway=10 时 now=1005 仍通过（规范允许少量时钟偏移）", True)

print("== 6. RFC 7519 §4.1 nbf：大于等于 ==")
validate_claims(dict(base, nbf=1000), now=1000)
ok("now == nbf 通过（after or equal to）", True)
expect_error("now < nbf 尚未生效", validate_claims, dict(base, nbf=1000), 999)

print("== 7. RFC 7519 §4.1 aud：缺席即 MUST be rejected ==")
validate_claims(dict(base, aud="svc-a"), now=0, audience="svc-a")
ok("单值 aud 且匹配 → 通过", True)
validate_claims(dict(base, aud=["svc-a", "svc-b"]), now=0, audience="svc-b")
ok("数组 aud 且命中其一 → 通过", True)
expect_error("aud 不含本方标识 → 拒绝", validate_claims,
             dict(base, aud="svc-a"), 0, audience="svc-c")
expect_error("未声明 audience 但有 aud → 拒绝", validate_claims,
             dict(base, aud="svc-a"), 0)
validate_claims(dict(base), now=0)
ok("没有 aud 声明时不校验受众（该 claim 是 OPTIONAL）", True)

print("== 8. iss 与完整链路 ==")
expect_error("iss 不符 → 拒绝", validate_claims, dict(base, iss="other"), 0,
             issuer="joe")
k = b"k" * 32
now = 1_700_000_000
tok = encode_jwt({"alg": "HS256", "typ": "JWT"},
                 {"iss": "auth", "sub": "42", "aud": "api", "iat": now,
                  "exp": now + 3600, "nbf": now - 60, "jti": "abc"}, k)
got = verify_hs256(tok, k, allowed_algs=["HS256"])
validate_claims(got, now=now, audience="api", issuer="auth")
ok("自建 token 签名 + 声明全链路通过", got["sub"] == "42", str(got))
expect_error("同一 token 换受众 → 拒绝", validate_claims, got, now, "other")
expect_error("过期后 → 拒绝", validate_claims, got, now + 3600)

print(f"\n全部 {PASS[0]} 项断言通过")
