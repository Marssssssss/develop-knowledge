"""OAuth 2.0 授权码 + PKCE 自检脚本。

每条断言都对应一份权威材料的具体条款,断言失败 = 实现与规范不符:
- RFC 7636 附录 B 的 S256 向量是唯一的"字符串级"黄金基准
- 其余断言来自 RFC 6749 §4.1.2/§4.1.3/§3.1.2.4 与 RFC 9700 §2.1.1/§4.2.4/§4.4.2.1
"""
import hashlib

from oauth_pkce_core import (CODE_TTL, Client, AuthServer, OAuthError, b64url,
                             s256, verifier_syntax_ok)

PASS = 0


def check(label, cond, detail=""):
    global PASS
    assert cond, f"FAIL {label} {detail}"
    PASS += 1
    print(f"  ok  {label}")


def err(fn, want):
    try:
        fn()
    except OAuthError as e:
        return e.code
    return None


def fresh(now=0, public=True, cid="c1", uris=("https://client.example.com/cb",)):
    asrv = AuthServer("https://as.example", now=now)
    asrv.register(cid, list(uris), public=public)
    return asrv


def flow(now=0, public=True, redirect="https://client.example.com/cb"):
    asrv = fresh(now=now, public=public, uris=(redirect,))
    cli = Client("c1", redirect, "https://as.example")
    req = cli.start(hashlib.sha256(b"seed-1").digest())
    req["client_id"] = "c1"
    req["redirect_uri"] = redirect
    loc = asrv.authorize(req)
    q = dict(kv.split("=", 1) for kv in loc.split("?", 1)[1].split("&"))
    tokreq = cli.callback(q, "https://as.example")
    return asrv, cli, tokreq, q


print("RFC 7636 附录 B 的 S256 向量(基准)")
V = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
check("verifier 长度 43", len(V) == 43, len(V))
check("S256 challenge", s256(V) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", s256(V))
check("base64url 无填充", "=" not in s256(V) and b64url(bytes([3, 236, 255, 224, 193])) == "A-z_4ME")
check("verifier 语法窗口", verifier_syntax_ok(V) and not verifier_syntax_ok("a" * 42)
      and not verifier_syntax_ok("a" * 129) and not verifier_syntax_ok(V + "!"))

print("RFC 6749 §4.1 正常路径")
asrv, cli, tokreq, q = flow()
check("授权响应带 code/state/iss", q["code"].startswith("code-") and q["state"] == "st-1"
      and q["iss"] == "https://as.example")
check("授权请求用 S256 且不回传 verifier",
      "code_verifier" not in q and "code_challenge" not in q)
res = asrv.token(tokreq)
check("令牌响应字段", res["access_token"] and res["token_type"] == "Bearer"
      and res["expires_in"] == 3600 and res["refresh_token"])

print("PKCE 校验(RFC 7636 §4.6 / RFC 9700 §2.1.1)")
asrv = fresh()
req = {"response_type": "code", "client_id": "c1",
       "redirect_uri": "https://client.example.com/cb", "state": "s1",
       "code_challenge": s256(V), "code_challenge_method": "S256"}
loc = asrv.authorize(req)
code = loc.split("code=")[1].split("&")[0]
base = {"grant_type": "authorization_code", "code": code, "client_id": "c1",
        "redirect_uri": "https://client.example.com/cb"}
check("verifier 错配 → invalid_grant",
      err(lambda: asrv.token({**base, "code_verifier": V[:-1] + "X"}), None) == "invalid_grant")
check("缺 verifier 但码绑了 challenge → invalid_request",
      err(lambda: asrv.token(dict(base)), None) == "invalid_request")
check("verifier 语法非法 → invalid_grant",
      err(lambda: asrv.token({**base, "code_verifier": "short"}), None) == "invalid_grant")
ok = asrv.token({**base, "code_verifier": V})
check("verifier 正确 → 发令牌", ok["access_token"])

print("授权码单次使用与生命周期(RFC 6749 §4.1.2 / RFC 9700 §4.2.4)")
asrv = fresh()
loc = asrv.authorize({**req, "state": "s1"})
code = loc.split("code=")[1].split("&")[0]
b2 = {**base, "code": code, "code_verifier": V}
first = asrv.token(dict(b2))
replay_code = err(lambda: asrv.token(dict(b2)), None)
check("重放 → invalid_grant", replay_code == "invalid_grant")
check("重放撤销先前令牌", first["access_token"] in asrv.revoked,
      sorted(asrv.revoked))

asrv2 = fresh(now=0)
loc = asrv2.authorize({**req, "state": "s2"})
c2 = loc.split("code=")[1].split("&")[0]
asrv2.now = CODE_TTL + 1
check(f"{CODE_TTL}s 后过期 → invalid_grant",
      err(lambda: asrv2.token({**base, "code": c2, "code_verifier": V}), None) == "invalid_grant")
asrv2.now = CODE_TTL
check("恰好 600s 仍有效(闭区间)",
      asrv2.token({**base, "code": c2, "code_verifier": V})["access_token"])

print("redirect_uri 精确匹配(RFC 6749 §3.1.2.3/§3.1.2.4、RFC 9700 §2.1/§2.6)")
asrv = fresh(uris=("https://client.example.com/cb", "http://127.0.0.1:8123/cb"))
check("未注册 URI → 不重定向",
      err(lambda: asrv.authorize({**req, "redirect_uri": "https://client.example.com/cb/"}), None)
      == "invalid_request")
check("大小写不匹配 → 拒绝",
      err(lambda: asrv.authorize({**req, "redirect_uri": "https://Client.example.com/cb"}), None)
      == "invalid_request")
check("前缀相同但多余后缀 → 拒绝",
      err(lambda: asrv.authorize({**req, "redirect_uri": "https://client.example.com/cb.evil.com"}),
          None) == "invalid_request")
check("http scheme(非回环)→ 拒绝",
      err(lambda: asrv.authorize({**req, "redirect_uri": "http://client.example.com/cb"}), None)
      == "invalid_request")
check("原生应用回环 URI 允许",
      asrv.authorize({**req, "redirect_uri": "http://127.0.0.1:8123/cb"}).startswith("http://127.0.0.1:8123/cb?code="))
asrv3 = fresh(uris=("https://client.example.com/cb?a=1",))
loc = asrv3.authorize({**req, "redirect_uri": "https://client.example.com/cb?a=1", "attach_fragment": True})
check("已带 query 时用 & 拼接 + 附加 #_ 片段", "?a=1&code=" in loc and loc.endswith("#_"), loc)

print("PKCE 降级防御(RFC 9700 §2.1.1、§4.8.2)")
asrv = fresh(public=False)   # 机密客户端:授权请求没带 challenge
loc = asrv.authorize({**req, "code_challenge": None, "code_challenge_method": None})
c3 = loc.split("code=")[1].split("&")[0]
check("带 verifier 但授权时无 challenge → invalid_grant",
      err(lambda: asrv.token({"grant_type": "authorization_code", "code": c3, "client_id": "c1",
                              "redirect_uri": "https://client.example.com/cb",
                              "client_secret": "s3cret-c1", "code_verifier": V}), None)
      == "invalid_grant")
check("不带 verifier 则正常(降级只在客户端主动发 verifier 时被识别)",
      asrv.token({"grant_type": "authorization_code", "code": c3, "client_id": "c1",
                  "redirect_uri": "https://client.example.com/cb",
                  "client_secret": "s3cret-c1"})["access_token"])
check("公共客户端无 challenge → invalid_request",
      err(lambda: fresh().authorize({**req, "code_challenge": None}), None) == "invalid_request")
check("未知 challenge 方法 → invalid_request",
      err(lambda: fresh().authorize({**req, "code_challenge_method": "S512"}), None) == "invalid_request")
asrv = fresh()
loc = asrv.authorize({**req, "code_challenge": V, "code_challenge_method": "plain"})
c4 = loc.split("code=")[1].split("&")[0]
check("plain 方法按 §4.6 明文比较",
      asrv.token({**base, "code": c4, "code_verifier": V})["access_token"])

print("state / CSRF 与 mix-up(RFC 9700 §2.1、§4.2.4、§4.4.2.1)")
asrv, cli, tokreq, q = flow()      # flow() 内部已消费 state
check("state 复用 → 中止",
      err(lambda: cli.callback(q, "https://as.example"), None) == "state_mismatch")
asrv, cli, tokreq, q = flow()
check("state 未知 → 中止",
      err(lambda: cli.callback({"code": q["code"], "state": "st-99"}, "https://as.example"), None)
      == "state_mismatch")

honest = AuthServer("https://honest.example", 0)
honest.register("c1", ["https://client.example.com/cb"])
attacker = AuthServer("https://evil.example", 0)
attacker.register("c1", ["https://client.example.com/cb"])
cli = Client("c1", "https://client.example.com/cb", "https://honest.example")
req = cli.start(hashlib.sha256(b"seed-2").digest())
req["client_id"] = "c1"
req["redirect_uri"] = "https://client.example.com/cb"
hq = dict(kv.split("=", 1) for kv in honest.authorize(req).split("?", 1)[1].split("&"))
aq = dict(kv.split("=", 1) for kv in attacker.authorize(req).split("?", 1)[1].split("&"))
check("诚实 AS 的 iss 通过", cli.callback(dict(hq), "https://honest.example")["code"] == hq["code"])
cli2 = Client("c1", "https://client.example.com/cb", "https://honest.example")
r2 = cli2.start(hashlib.sha256(b"seed-3").digest())
r2["client_id"], r2["redirect_uri"] = "c1", "https://client.example.com/cb"
hq2 = dict(kv.split("=", 1) for kv in honest.authorize(r2).split("?", 1)[1].split("&"))
hq2["iss"] = "https://evil.example"      # 攻击者改写 iss
check("iss 被改写 → mix_up 中止",
      err(lambda: cli2.callback(dict(hq2), "https://honest.example"), None) == "mix_up")
cli3 = Client("c1", "https://client.example.com/cb", "https://honest.example")
r3 = cli3.start(hashlib.sha256(b"seed-4").digest())
r3["client_id"], r3["redirect_uri"] = "c1", "https://client.example.com/cb"
hq3 = dict(kv.split("=", 1) for kv in honest.authorize(r3).split("?", 1)[1].split("&"))
check("令牌端点 issuer 不符 → mix_up 中止(不同重定向 URI 防御的等价检查)",
      err(lambda: cli3.callback(dict(hq3), "https://evil.example"), None) == "mix_up")

print("授权端点错误响应用例(RFC 6749 §4.1.2.1)")
loc = fresh().deny({"redirect_uri": "https://client.example.com/cb", "state": "s9"}, "access_denied")
check("错误响应回填 state", loc.endswith("error=access_denied&state=s9"), loc)

print(f"\n{PASS} 项断言全部通过 (OAuth 2.0 + PKCE)")
