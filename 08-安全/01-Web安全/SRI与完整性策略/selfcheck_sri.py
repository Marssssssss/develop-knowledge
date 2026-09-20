"""SRI与完整性策略 自检。

官方向量（实读 https://www.w3.org/TR/SRI/ 后抄录）：
  §3.1 对脚本内容 alert('Hello, world.'); 给出
    sha384-H8BRh8j48O9oYatfu5AZzq6A9RINhZO5H16dQZngK7T62em8MUt1FLm52t+eX6xO
  §3.2.1 对同一内容给出
    sha512-Q2bFTOhEALkN8hOms2FKTDLy7eugP2zFZ1T8LCvX42Fp3WoNr3bjZSAHeOsHrbV1Fu9/A0EzCinRE7Af1ofPrw==
"""

from sri import (IntegrityRequest, PolicyContainer, digest_of, do_bytes_match,
                 get_strongest_metadata, integrity_metadata, parse_metadata,
                 process_integrity_policy, should_request_be_blocked,
                 verify_subresource, VALID_ALGOS)

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


PAYLOAD = b"alert('Hello, world.');"

# ------------------------------------------------- 官方向量：标准 base64 证据
eq("sha384 官方摘要复现", digest_of(PAYLOAD, "sha384"),
   "H8BRh8j48O9oYatfu5AZzq6A9RINhZO5H16dQZngK7T62em8MUt1FLm52t+eX6xO")
eq("sha512 官方摘要复现", digest_of(PAYLOAD, "sha512"),
   "Q2bFTOhEALkN8hOms2FKTDLy7eugP2zFZ1T8LCvX42Fp3WoNr3bjZSAHeOsHrbV1Fu9/A0EzCinRE7Af1ofPrw==")
ck("sha384 官方值含 '+'（标准 base64 而非 base64url）",
   "+" in digest_of(PAYLOAD, "sha384"))
ck("sha512 官方值以 '==' 结尾（保留 padding）",
   digest_of(PAYLOAD, "sha512").endswith("=="))
eq("三算法有序集合 = sha256 < sha384 < sha512",
   list(VALID_ALGOS), ["sha256", "sha384", "sha512"])

# ------------------------------------------------------------- §3.3.2 解析
p = parse_metadata("sha384-AAA?opt=1 sha512-BBB md5-CCC")
eq("不认识的算法被跳过", len(p), 2)
eq("第 1 项算法", p[0]["alg"], "sha384")
eq("第 1 项取值去掉了 ? 选项", p[0]["val"], "AAA")
eq("第 2 项算法", p[1]["alg"], "sha512")
eq("空字符串解析为空集", parse_metadata(""), [])
eq("纯空白也解析为空集", parse_metadata("   "), [])
p2 = parse_metadata("sha256-")
eq("缺 base64 部分时 val 为空串", p2[0]["val"], "")
p3 = parse_metadata("sha256-X")
eq("连字符后只切第一段（split('-' ,1)）", p3[0]["val"], "X")

# ------------------------------------------------- §3.3.3 只保留"最强"的一批
strong = get_strongest_metadata([{"alg": "sha256", "val": "a"},
                                {"alg": "sha512", "val": "c"},
                                {"alg": "sha384", "val": "b"}])
eq("强弱混合时只留最强的", [x["alg"] for x in strong], ["sha512"])
eq("最强那批的取值", strong[0]["val"], "c")
two = get_strongest_metadata([{"alg": "sha384", "val": "a"},
                              {"alg": "sha384", "val": "b"}])
eq("同强度的两份都保留", [x["val"] for x in two], ["a", "b"])
three = get_strongest_metadata([{"alg": "sha512", "val": "a"},
                                {"alg": "sha256", "val": "b"},
                                {"alg": "sha512", "val": "c"}])
eq("顺序不影响：两份 sha512 都留下", [x["val"] for x in three], ["a", "c"])

# ----------------------------------------------------------- §3.3.4 比对
good384 = integrity_metadata(PAYLOAD, "sha384")
good512 = integrity_metadata(PAYLOAD, "sha512")
bad384 = "sha384-" + "Z" * 64
bad512 = "sha512-" + "Z" * 88

ck("正确的 sha384 通过", do_bytes_match(PAYLOAD, good384))
ck("错误的 sha384 不通过", not do_bytes_match(PAYLOAD, bad384))
ck("没有 integrity 元数据 → 直接通过（§3.3.4 空集返回 true）",
   do_bytes_match(PAYLOAD, ""))
ck("官方双 sha384 例子：命中任意一份即通过",
   do_bytes_match(PAYLOAD, bad384 + " " + good384))

# 关键反直觉点：只验最强的那一批
ck("弱的对 + 强的错 → 失败（弱的那份根本不看）",
   not do_bytes_match(PAYLOAD, good384 + " " + bad512))
ck("弱的错 + 强的对 → 通过", do_bytes_match(PAYLOAD, bad384 + " " + good512))

ck("大小写敏感匹配：把摘要最后一个字符改写即失败",
   not do_bytes_match(PAYLOAD, good384[:-1] + ("A" if good384[-1] != "A" else "B")))

# ------------------------------------------- §3.3.4 note：SRI 需要 CORS
ok_a, why_a = verify_subresource(PAYLOAD, good384, "https://cdn.example",
                                 "https://example.com", True)
ck("跨源 + crossorigin → 通过", ok_a, why_a)
ok_b, why_b = verify_subresource(PAYLOAD, good384, "https://cdn.example",
                                 "https://example.com", False)
ck("跨源 + 无 crossorigin → 失败（SRI 需要 CORS）", not ok_b, why_b)
ok_c, why_c = verify_subresource(PAYLOAD, good384, "https://example.com",
                                 "https://example.com", False)
ck("同源 + 无 crossorigin → 通过", ok_c, why_c)

# --------------------------------------------- §3.8 Integrity-Policy 解析
pol = process_integrity_policy({"blocked-destinations": ["script"],
                                "endpoints": ["integrity-endpoint"]})
eq("sources 缺省即 inline", pol.sources, ["inline"])
eq("blocked-destinations 解析", pol.blocked_destinations, ["script"])
eq("endpoints 解析", pol.endpoints, ["integrity-endpoint"])
pol_sty = process_integrity_policy({"sources": ["inline"],
                                    "blocked-destinations": ["script", "style"]})
eq("两个 destination 都能收", pol_sty.blocked_destinations, ["script", "style"])
eq("显式给 sources 也只收 inline",
   process_integrity_policy({"sources": ["inline"]}).sources, ["inline"])
empty_pol = process_integrity_policy({})
ck("空字典 → sources 仍是 inline 但没有 blocked destinations",
   empty_pol.sources == ["inline"] and empty_pol.blocked_destinations == [])

# ------------------------------------------- §3.8.2 是否阻断请求
container = PolicyContainer(policy=pol)
v = []
ext = IntegrityRequest("https://cdn.example/a.js", "script", "no-cors", "")
eq("外部脚本无 integrity 且被策略点名 → Blocked",
   should_request_be_blocked(ext, container, violations=v), "Blocked")
eq("并产生一条违规", len(v), 1)
eq("违规的 blockedURL", v[0].blocked_url, "https://cdn.example/a.js")

with_int = IntegrityRequest("https://cdn.example/a.js", "script", "cors",
                            good384)
eq("带 integrity 且 mode=cors → Allowed",
   should_request_be_blocked(with_int, container, violations=v), "Allowed")

no_cors_mode = IntegrityRequest("https://cdn.example/a.js", "script", "no-cors",
                                good384)
eq("有 integrity 但 mode=no-cors → 不被①豁免，仍按策略 Blocked",
   should_request_be_blocked(no_cors_mode, container, violations=v), "Blocked")

style_req = IntegrityRequest("https://cdn.example/a.css", "style", "no-cors", "")
eq("destination 不在 blocked-destinations → Allowed",
   should_request_be_blocked(style_req, container, violations=v), "Allowed")

local_req = IntegrityRequest("data:text/javascript,1", "script", "no-cors", "",
                             local=True)
eq("local URL → Allowed",
   should_request_be_blocked(local_req, container, violations=v), "Allowed")

empty_container = PolicyContainer()
eq("两个策略都空 → Allowed",
   should_request_be_blocked(ext, empty_container, violations=v), "Allowed")

# report-only 命中：报违规但不阻断
ro = PolicyContainer(report_only=pol)
v2 = []
eq("report-only 命中 → 不阻断",
   should_request_be_blocked(ext, ro, violations=v2), "Allowed")
eq("report-only 仍产生违规", len(v2), 1)
ck("违规带 reportOnly=true", v2[0].report_only)

print("OK =", OK)
if FAIL:
    print("FAILED =", len(FAIL))
    for f in FAIL:
        print("  -", f)
else:
    print("ALL OK")
