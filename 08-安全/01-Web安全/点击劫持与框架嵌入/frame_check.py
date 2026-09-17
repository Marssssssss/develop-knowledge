"""点击劫持防护自检。

断言来源:
- WHATWG HTML Standard §7.7 The X-Frame-Options header(处理模型 + 官方结果表)
- OWASP Clickjacking Defense Cheat Sheet(三种独立机制、CSP frame-ancestors 优先、
  meta 标签无效、ALLOW-FROM 废弃、多值只认一个、frame-buster 的绕过手法)
"""
from frame_policy import (Candidate, SCENARIOS, check_x_frame_options, decide,
                          frame_ancestors_match, meta_delivered, parse_csp_headers,
                          split_header_values)

PASS = 0


def check(label, cond, detail=""):
    global PASS
    assert cond, f"FAIL {label} {detail}"
    PASS += 1
    print(f"  ok  {label}")


SELF = "https://bank.example"
EVIL = "https://evil.example"

print("头字段拆分(Fetch 的 get/decode/split)")
check("逗号分隔拆成多值", split_header_values(["DENY, SAMEORIGIN"]) == ["DENY", "SAMEORIGIN"])
check("尾逗号产生空串", split_header_values(["SAMEORIGIN,"]) == ["SAMEORIGIN", ""])
check("多个头字段合并", split_header_values(["DENY", "SAMEORIGIN"]) == ["DENY", "SAMEORIGIN"])
check("去空白", split_header_values(["  DENY , SAMEORIGIN "]) == ["DENY", "SAMEORIGIN"])

print("HTML 标准 §7.7 官方结果表")
table = [
    (["SAMEORIGIN", "SAMEORIGIN"], SELF, True, "单值集合 → 走 SAMEORIGIN 分支"),
    (["SAMEORIGIN", "DENY"], SELF, False, "含合法值且多值 → 拦下"),
    (["SAMEORIGIN", ""], SELF, False, "同上(空串也算一个值)"),
    (["SAMEORIGIN", "ALLOWALL"], SELF, False, "含合法值且多值 → 拦下"),
    (["SAMEORIGIN", "INVALID"], SELF, False, "含合法值且多值 → 拦下"),
    (["ALLOWALL", "INVALID"], SELF, False, "含 allowall(与 deny/sameorigin 同属'困惑'集合)→ 拦下"),
    (["ALLOWALL", ""], SELF, False, "同上,空串也算一个值"),
    (["INVALID", "INVALID"], SELF, True, "去重后单值 → 孤立非法值,等同没有"),
]
for values, ancestor, want, why in table:
    got, reason = check_x_frame_options(values, Candidate(SELF, [ancestor], True))
    check(f"{','.join(v or '<空>' for v in values):>22} → {'允许' if want else '拒绝'}({why})",
          got == want, f"got={got} reason={reason}")

print("单值语义与顶层文档")
check("DENY 拒绝任何嵌入",
      check_x_frame_options(["DENY"], Candidate(SELF, [SELF], True))[0] is False)
check("SAMEORIGIN + 同源祖先 → 允许",
      check_x_frame_options(["SAMEORIGIN"], Candidate(SELF, [SELF], True)) == (True, "sameorigin"))
check("SAMEORIGIN + 跨源祖先 → 拒绝",
      check_x_frame_options(["SAMEORIGIN"], Candidate(SELF, [EVIL], True))[0] is False)
check("大小写不敏感",
      check_x_frame_options(["deny"], Candidate(SELF, [SELF], True))[0] is False)
check("顶层文档不受约束(step 1)",
      check_x_frame_options(["DENY"], Candidate(SELF, [SELF], False)) == (True, "top-level"))
check("没有头 → 允许(默认可嵌入,这才是需要防御的默认值)",
      check_x_frame_options([], Candidate(SELF, [EVIL], True)) == (True, "absent"))
check("废弃的 ALLOW-FROM 被忽略(不再生效)",
      check_x_frame_options(["ALLOW-FROM https://partner.example"],
                            Candidate(SELF, [EVIL], True))[0] is True)
check("孤立的 ALLOWALL 也放行(遗留值,只有与合法值同时出现才拦)",
      check_x_frame_options(["ALLOWALL"], Candidate(SELF, [EVIL], True)) == (True, "lone-invalid"))

print("SAMEORIGIN 沿整条祖先链校验")
check("同源(直接父窗口)",
      check_x_frame_options(["SAMEORIGIN"], Candidate(SELF, [SELF], True))[0] is True)
check("父同源但祖父跨源 → 拒绝(OWASP:ALLOW-FROM 只看顶层,这是同源版的反面)",
      check_x_frame_options(["SAMEORIGIN"], Candidate(SELF, [EVIL, SELF], True))[0] is False)
check("两层都同源 → 允许",
      check_x_frame_options(["SAMEORIGIN"], Candidate(SELF, [SELF, SELF], True))[0] is True)

print("CSP frame-ancestors")
check("'none' 拒绝任何来源",
      frame_ancestors_match(["'none'"], EVIL, SELF) is False)
check("'self' 仅同源",
      frame_ancestors_match(["'self'"], SELF, SELF) is True
      and frame_ancestors_match(["'self'"], EVIL, SELF) is False)
check("'*' 放行",
      frame_ancestors_match(["*"], EVIL, SELF) is True)
check("主机列表精确匹配",
      frame_ancestors_match(["https://partner.example"], "https://partner.example", SELF) is True
      and frame_ancestors_match(["https://partner.example"], "https://other.example", SELF) is False)
check("通配子域 *.somesite.com",
      frame_ancestors_match(["*.somesite.com"], "https://a.somesite.com", SELF) is True
      and frame_ancestors_match(["*.somesite.com"], "https://somesite.com", SELF) is True
      and frame_ancestors_match(["*.somesite.com"], "https://notsomesite.com", SELF) is False)
check("scheme 不匹配 → 不命中",
      frame_ancestors_match(["http://partner.example"], "https://partner.example", SELF) is False)
check("多个祖先必须全部匹配",
      decide({"content-security-policy": ["frame-ancestors 'self'"]},
             Candidate(SELF, [EVIL, SELF], True))[0] is False)

print("CSP 与 XFO 的关系(HTML §7.7 step 2)")
check("有 enforce 的 frame-ancestors → XFO 被完全忽略(即使 XFO 更宽松)",
      decide({"x-frame-options": ["DENY"],
              "content-security-policy": ["frame-ancestors 'self'"]},
             Candidate(SELF, [SELF], True))[0] is True)
check("有 enforce 的 frame-ancestors → XFO 被忽略(即使 XFO 更严格)",
      decide({"x-frame-options": ["DENY"],
              "content-security-policy": ["frame-ancestors *"]},
             Candidate(SELF, [EVIL], True))[0] is True)
check("Report-Only 不算 enforce,回落到 XFO",
      decide({"x-frame-options": ["DENY"],
              "content-security-policy-report-only": ["frame-ancestors *"]},
             Candidate(SELF, [EVIL], True)) == (False, "deny"))
check("CSP 里没有 frame-ancestors 时回落到 XFO",
      decide({"x-frame-options": ["SAMEORIGIN"],
              "content-security-policy": ["default-src 'self'"]},
             Candidate(SELF, [EVIL], True))[0] is False)
check("两个头都下发且一致 → 拒绝",
      decide({"x-frame-options": ["DENY"],
              "content-security-policy": ["frame-ancestors 'none'"]},
             Candidate(SELF, [EVIL], True))[0] is False)
check("判定依据可追溯", decide({"content-security-policy": ["frame-ancestors 'none'"]},
                            Candidate(SELF, [EVIL], True))[1].startswith("csp:frame-ancestors"))

print("CSP 头解析")
pol = parse_csp_headers({"content-security-policy": ["default-src 'self'; frame-ancestors 'none'; report-uri /r"]})
check("disposition=enforce", pol[0]["disposition"] == "enforce")
check("指令被拆开", pol[0]["directives"]["default-src"] == ["'self'"]
      and pol[0]["directives"]["frame-ancestors"] == ["'none'"])
pol2 = parse_csp_headers({"content-security-policy-report-only": ["frame-ancestors *"]})
check("Report-Only disposition", pol2[0]["disposition"] == "report")

print("meta 标签无效(OWASP 明示)")
check("meta 里的 XFO 被丢弃",
      decide(meta_delivered({"x-frame-options": ["DENY"]}), Candidate(SELF, [EVIL], True))[0] is True)
check("meta 里的 CSP 也无效(必须走 HTTP 头)",
      decide(meta_delivered({"content-security-policy": ["frame-ancestors 'none'"]}),
             Candidate(SELF, [EVIL], True)) == (True, "absent"))

print("三种机制相互独立(OWASP:应叠加使用)")
check("场景表覆盖 frame-buster 的已知绕过", len(SCENARIOS) >= 5)
check("每条场景都写明『frame-buster 为何失效』与『什么机制拦得住』",
      all(len(s) == 3 and s[1] and s[2] for s in SCENARIOS))
check("多数场景靠 XFO/CSP 在浏览器层拦下,不依赖脚本",
      sum("XFO/CSP" in s[2] for s in SCENARIOS) >= 4, SCENARIOS)
check("SameSite 被列为独立于 XFO 的第三道机制",
      any("SameSite" in s[2] for s in SCENARIOS))
check("双重 frame 依赖跨源导航限制",
      any("跨源限制" in s[1] for s in SCENARIOS))

print(f"\n{PASS} 项断言全部通过 (X-Frame-Options + CSP frame-ancestors)")
