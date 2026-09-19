# -*- coding: utf-8 -*-
"""RFC 9457 自检：逐条断言规范里可判定的条款。运行：python check.py"""

import sys

import problem_details as pd

FAIL = 0
COUNT = 0


def ck(label, cond):
    global FAIL, COUNT
    COUNT += 1
    if not cond:
        FAIL += 1
        print("FAIL: %s" % label)


# --- §3.1 成员类型不符 MUST be ignored -------------------------------------
p = pd.Problem.parse({"status": 404, "title": "Not Found"})
ck("缺省 type 回落 about:blank", p.type == "about:blank")

p = pd.Problem.parse({"status": "404"})
ck("status 是字符串 → 忽略", p.status is None and "status" in p.ignored)

p = pd.Problem.parse({"status": True})
ck("JSON true 不是 number → status 忽略", p.status is None)

p = pd.Problem.parse({"status": 404.0})
ck("404.0 是整数值 → 接受", p.status == 404)

p = pd.Problem.parse({"status": 404.5})
ck("非整数 number → 忽略", p.status is None)

p = pd.Problem.parse({"status": 700})
ck("超出 100..599 → 忽略", p.status is None)

p = pd.Problem.parse({"status": 99})
ck("低于 100 → 忽略", p.status is None)

p = pd.Problem.parse({"type": 12345})
ck("type 非字符串 → 忽略并回落 about:blank",
   p.type == "about:blank" and "type" in p.ignored)

p = pd.Problem.parse({"title": ["a"]})
ck("title 非字符串 → 忽略", p.title is None and "title" in p.ignored)

# --- §3.1.1 相对 type URI 按 base 解析（规范原文示例） ----------------------
a = pd.Problem.parse({"type": "example-problem"},
                     base_uri="https://api.example.org/foo/bar/123")
b = pd.Problem.parse({"type": "example-problem"},
                     base_uri="https://api.example.org/widget/456")
ck("相对 type 解析结果 A", a.type == "https://api.example.org/foo/bar/example-problem")
ck("相对 type 解析结果 B", b.type == "https://api.example.org/widget/example-problem")
ck("同一相对 type 在不同 base 下不等价", a.type != b.type)

ck("绝对路径型 type",
   pd.Problem.parse({"type": "/types/123"}, base_uri="https://api.example.org/x/y")
   .type == "https://api.example.org/types/123")
ck("remove_dot_segments 去掉 ..",
   pd.remove_dot_segments("/a/b/../c") == "/a/c")
ck("remove_dot_segments 去掉 .",
   pd.remove_dot_segments("/a/./b") == "/a/b")

# --- §3.2 扩展成员 ---------------------------------------------------------
raw = {"type": "https://example.com/probs/out-of-credit",
       "title": "You do not have enough credit.",
       "detail": "Your current balance is 30, but that costs 50.",
       "instance": "/account/12345/msgs/abc",
       "balance": 30,
       "accounts": ["/account/12345", "/account/67890"]}
p = pd.Problem.parse(raw)
ck("扩展 balance 保留", p.extensions.get("balance") == 30)
ck("扩展 accounts 是列表", p.extensions.get("accounts") == ["/account/12345", "/account/67890"])
ck("未识别扩展一律留在 extensions 里由消费方忽略",
   p.known_extension("nope") is None and "nope" not in p.extensions)
ck("保留成员不进 extensions", all(k not in p.extensions for k in pd.RESERVED))

# §4 扩展命名 SHOULD 规则
ck("扩展名 balance 合规", pd.is_valid_extension_name("balance"))
ck("扩展名 a_b 合规", pd.is_valid_extension_name("a_b"))
ck("扩展名 ab 太短", not pd.is_valid_extension_name("ab"))
ck("扩展名 _x 非字母开头", not pd.is_valid_extension_name("_x"))
ck("扩展名 a-b 含非法字符", not pd.is_valid_extension_name("a-b"))
ck("扩展名 1ab 数字开头", not pd.is_valid_extension_name("1ab"))

# --- §3.1.2 status 只作参考，但生成器 MUST 与真实状态码一致 -----------------
ck("status 与真实状态码一致", pd.status_consistent(pd.Problem.parse({"status": 404}), 404))
ck("status 与真实状态码不一致 → 检出",
   not pd.status_consistent(pd.Problem.parse({"status": 422}), 404))
ck("status 缺失视为可接受", pd.status_consistent(pd.Problem.parse({}), 404))

# --- §4.2.1 about:blank ----------------------------------------------------
ck("about:blank 常量", pd.DEFAULT_TYPE == "about:blank")
ck("about:blank + 404 的 title 取推荐短语",
   pd.Problem.parse({"status": 404}).title_for_display() == "Not Found")
ck("about:blank + 409 的 title 取推荐短语",
   pd.Problem.parse({"status": 409}).title_for_display() == "Conflict")
ck("显式 title 不被覆盖",
   pd.Problem.parse({"status": 404, "title": "自定义"}).title_for_display() == "自定义")
ck("非 about:blank 不套用状态短语",
   pd.Problem.parse({"type": "https://x/e", "status": 404}).title_for_display() == "")

built = pd.build(404)
ck("build(404) 缺省 type=about:blank", built.type == "about:blank")
ck("build(404) 自动填 title", built.title == "Not Found")
ck("build 输出的 status 成员与实参一致", built.to_dict().get("status") == 404)
built2 = pd.build(403, type_uri="https://example.com/probs/out-of-credit",
                  title="You do not have enough credit.",
                  detail="Your current balance is 30, but that costs 50.",
                  balance=30, accounts=["/a/1", "/a/2"])
ck("显式 type 被保留", built2.type == "https://example.com/probs/out-of-credit")
ck("扩展进入序列化结果", built2.to_dict()["balance"] == 30)
ck("媒体类型", pd.MEDIA_TYPE == "application/problem+json")
ck("XML 媒体类型", pd.MEDIA_TYPE_XML == "application/problem+xml")

# --- 附录 B：XML 数组用 <i> 子元素 ------------------------------------------
xml = pd.to_xml(built2)
ck("XML 命名空间", 'xmlns="urn:ietf:rfc:7807"' in xml)
ck("XML 数组用 <i> 表示", "<accounts><i>/a/1</i><i>/a/2</i></accounts>" in xml)
ck("XML 标量扩展直出", "<balance>30</balance>" in xml)

# --- §3 多问题时 RECOMMENDED 只报最相关/最紧急 ------------------------------
URGENCY = {"https://x/payment-declined": 3, "https://x/bad-zip": 1,
           "https://x/quota": 2}


def most_urgent(candidates):
    return max(candidates, key=lambda c: URGENCY.get(c.type, 0))


cands = [pd.build(422, type_uri="https://x/bad-zip"),
         pd.build(402, type_uri="https://x/payment-declined"),
         pd.build(429, type_uri="https://x/quota")]
ck("多问题时挑最紧急的那个", most_urgent(cands).type == "https://x/payment-declined")

print("assertions=%d fail=%d" % (COUNT, FAIL))
sys.exit(1 if FAIL else 0)
