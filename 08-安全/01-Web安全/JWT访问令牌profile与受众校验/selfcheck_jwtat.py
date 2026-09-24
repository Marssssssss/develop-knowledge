"""JWT访问令牌profile与受众校验 自检。

依据 RFC 9068（https://www.rfc-editor.org/rfc/rfc9068.txt 35141 B 全文实读）：
  §2.1 typ MUST 是 at+jwt 或 application/at+jwt；alg MUST NOT 是 none；
       AS 与 RS MUST 支持 RS256
  §2.2 七个 REQUIRED 声明：iss / exp / aud / sub / client_id / iat / jti
  §4   校验顺序 typ → 解密 → iss(精确相等) → aud(含自身资源指示值) → 验签 → exp；
       失败一律 invalid_token；exp MAY 给几分钟余量
  §5   跨 JWT 混淆：AS MUST 用互不相同的 aud 区分同一签发者发往不同资源的令牌
"""

from jwtat import (AT_JWT_TYPES, CHECK_ORDER, REQUIRED_CLAIMS, ResourceServer,
                   Token, make_access_token)

OK = 0
FAIL = []


def ck(name, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAIL.append("%s %s" % (name, detail))


def eq(name, got, want):
    ck(name, got == want, "got=%r want=%r" % (got, want))


ISS = "https://as.example.com"
RES_A = "https://api-a.example.com/"
RES_B = "https://api-b.example.com/"
NOW = 1_700_000_000

eq("REQUIRED 声明共七个", len(REQUIRED_CLAIMS), 7)
ck("七个声明含 client_id", "client_id" in REQUIRED_CLAIMS)
ck("七个声明含 jti", "jti" in REQUIRED_CLAIMS)
ck("七个声明含 iat", "iat" in REQUIRED_CLAIMS)

rs = ResourceServer(RES_A, ISS)

# ---------- 合规令牌通过 ----------
tok = make_access_token(ISS, RES_A, "user-1", "client-1", NOW)
eq("合规令牌通过", rs.validate(tok, NOW), (True, None, None))
eq("默认 typ 是 at+jwt", tok.header["typ"], "at+jwt")
eq("默认 alg 是 RS256", tok.header["alg"], "RS256")

# ---------- §2.1 / §4 第 1 步：typ ----------
eq("application/at+jwt 也接受",
   rs.validate(make_access_token(ISS, RES_A, "u", "c", NOW,
                                 typ="application/at+jwt"), NOW), (True, None, None))
# OIDC ID Token 的 typ 是 JWT → 必须被拒绝（§2.1 的立项目的）
ok, err, step = rs.validate(make_access_token(ISS, RES_A, "u", "c", NOW,
                                              typ="JWT"), NOW)
ck("ID Token 的 typ=JWT 被拒", not ok and step == "typ")
eq("typ 失败也是 invalid_token", err, "invalid_token")
ok, err, step = rs.validate(Token({"alg": "RS256"},
                                  {c: "x" for c in REQUIRED_CLAIMS}), NOW)
ck("typ 缺失被拒", not ok and step == "typ")

# ---------- §2.2 REQUIRED 声明 ----------
for missing in REQUIRED_CLAIMS:
    claims = {c: "x" for c in REQUIRED_CLAIMS}
    claims["iss"] = ISS
    claims["aud"] = RES_A
    claims["exp"] = NOW + 300
    del claims[missing]
    ok, err, step = rs.validate(Token({"typ": "at+jwt", "alg": "RS256"}, claims), NOW)
    ck("缺 %s 被拒" % missing, not ok and step == "required_claims", str(step))

# ---------- §4 第 3 步：iss 必须精确相等 ----------
ok, err, step = rs.validate(
    make_access_token("https://as.example.com/", RES_A, "u", "c", NOW), NOW)
ck("带斜杠后缀的 issuer 不等于无斜杠（精确匹配）", not ok and step == "iss")
ok, err, step = rs.validate(
    make_access_token("https://as.example.com ", RES_A, "u", "c", NOW), NOW)
ck("尾部空格导致不等", not ok and step == "iss")
ok, err, step = rs.validate(
    make_access_token("HTTPS://AS.EXAMPLE.COM", RES_A, "u", "c", NOW), NOW)
ck("大小写不同即不等", not ok and step == "iss")
eq("issuer 正确时通过",
   rs.validate(make_access_token(ISS, RES_A, "u", "c", NOW), NOW)[0], True)

# ---------- §4 第 4 步：aud 必须含自身的资源指示值 ----------
ok, err, step = rs.validate(
    make_access_token(ISS, RES_B, "u", "c", NOW), NOW)
ck("发往别的资源的令牌被拒（跨 JWT 混淆）", not ok and step == "aud")
# aud 是数组且含自身 → 通过
tok = make_access_token(ISS, [RES_B, RES_A], "u", "c", NOW)
eq("aud 数组含自身即通过", rs.validate(tok, NOW)[0], True)
eq("aud_list 展开正确", tok.aud_list(), [RES_B, RES_A])
# aud 是数组但不含自身 → 拒
ok, err, step = rs.validate(
    make_access_token(ISS, [RES_B, "https://api-c.example.com/"], "u", "c", NOW), NOW)
ck("aud 数组不含自身被拒", not ok and step == "aud")
# §5：同一签发者对不同资源必须用不同 aud
rs_b = ResourceServer(RES_B, ISS)
tok_a = make_access_token(ISS, RES_A, "u", "c", NOW)
tok_b = make_access_token(ISS, RES_B, "u", "c", NOW)
ck("A 的令牌被 B 拒", not rs_b.validate(tok_a, NOW)[0])
ck("B 的令牌被 A 拒", not rs.validate(tok_b, NOW)[0])
ck("A 的令牌被 A 收", rs.validate(tok_a, NOW)[0])

# ---------- §4 第 5 步：alg / 验签 ----------
ok, err, step = rs.validate(
    make_access_token(ISS, RES_A, "u", "c", NOW, alg="none"), NOW)
ck("alg=none 被拒", not ok and step == "alg")
ok, err, step = rs.validate(
    make_access_token(ISS, RES_A, "u", "c", NOW), NOW, signature_ok=False)
ck("验签失败被拒", not ok and step == "alg")
eq("验签失败也是 invalid_token", err, "invalid_token")

# ---------- §4 第 6 步：exp ----------
tok = make_access_token(ISS, RES_A, "u", "c", NOW, ttl=300)
eq("exp 之前通过", rs.validate(tok, NOW + 299)[0], True)
ok, err, step = rs.validate(tok, NOW + 300)
ck("恰好 exp 即失效（当前时间 MUST 早于 exp）", not ok and step == "exp")
ok, err, step = rs.validate(tok, NOW + 301)
ck("exp 之后失效", not ok and step == "exp")
# MAY 给的时钟偏移余量
rs_lw = ResourceServer(RES_A, ISS, leeway=60)
eq("余量内仍接受", rs_lw.validate(tok, NOW + 330)[0], True)
ok, err, step = rs_lw.validate(tok, NOW + 360)
ck("超出余量失效", not ok and step == "exp")

# ---------- 加密协商 ----------
rs_enc = ResourceServer(RES_A, ISS, require_encryption=True)
ok, err, step = rs_enc.validate(make_access_token(ISS, RES_A, "u", "c", NOW), NOW)
ck("协商了加密而未加密 → 拒绝", not ok and step == "encryption")
tok = make_access_token(ISS, RES_A, "u", "c", NOW)
tok.encrypted = True
eq("已加密则通过", rs_enc.validate(tok, NOW)[0], True)

# ---------- §2.2.1/§2.2.3 可选声明不影响校验 ----------
tok = make_access_token(ISS, RES_A, "u", "c", NOW,
                        extra={"auth_time": NOW - 10, "acr": "urn:mace:incommon:iap:bronze",
                               "amr": ["pwd", "mfa"], "scope": "read write"})
eq("带认证信息与 scope 的令牌通过", rs.validate(tok, NOW)[0], True)
eq("scope 被保留", tok.claims["scope"], "read write")
eq("amr 是数组", tok.claims["amr"], ["pwd", "mfa"])

# ---------- sub 口径 ----------
tok = make_access_token(ISS, RES_A, "user-42", "client-1", NOW)
eq("授权码类：sub 是资源拥有者", tok.claims["sub"], "user-42")
tok = make_access_token(ISS, RES_A, "client-1", "client-1", NOW)
eq("客户端凭证类：sub 是客户端标识", tok.claims["sub"], tok.claims["client_id"])

# ---------- 检查顺序可审计 ----------
eq("检查顺序共七步", len(CHECK_ORDER), 7)
eq("typ 是第一步", CHECK_ORDER[0], "typ")
eq("exp 是最后一步", CHECK_ORDER[-1], "exp")
ck("aud 在验签之前", CHECK_ORDER.index("aud") < CHECK_ORDER.index("alg"))
# typ 错 + aud 错 时先报 typ
bad = make_access_token(ISS, RES_B, "u", "c", NOW, typ="JWT")
eq("多错时先报 typ", rs.validate(bad, NOW)[2], "typ")

print("OK =", OK)
if FAIL:
    print("FAILED =", len(FAIL))
    for f in FAIL:
        print("  -", f)
else:
    print("ALL OK")
