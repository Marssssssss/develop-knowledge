"""HTTP请求走私 自检。

官方向量与口径（实读 RFC 9112 全文后抄录）：
  §6.3 八条判定按优先级顺序
  §7.1 chunked-body = *chunk last-chunk trailer-section CRLF
  §11.2 请求走私 = 利用不同接收方的解析差异隐藏额外请求
"""

from smuggle import (Framing, Headers, Parser, decode_chunked,
                     determine_framing, parse_cl, parse_te, two_hop)

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


def H(*pairs):
    return Headers(list(pairs))


# ---------- §6.1/§7 transfer-coding 解析 ----------
eq("TE: chunked", parse_te("chunked"), ["chunked"])
eq("TE 大小写不敏感", parse_te("Chunked"), ["chunked"])
eq("TE 逗号列表", parse_te("gzip, chunked"), ["gzip", "chunked"])
eq("TE 带空格", parse_te("gzip ,  chunked"), ["gzip", "chunked"])

# ---------- §6.3 规则 1：HEAD/1xx/204/304 无 body ----------
f = determine_framing(H(("Content-Length", "10")), is_request=False,
                      method="HEAD", status=200)
eq("规则1 HEAD 响应无 body", (f.rule, f.kind, f.value), (1, "none", 0))
f = determine_framing(H(("Content-Length", "10"), ("Transfer-Encoding", "chunked")),
                      is_request=False, method="GET", status=204)
eq("规则1 204 即便有 CL/TE 也无 body", (f.rule, f.kind), (1, "none"))
f = determine_framing(H(("Transfer-Encoding", "chunked")), is_request=False,
                      method="GET", status=304)
eq("规则1 304 命中", f.rule, 1)

# ---------- §6.3 规则 2：CONNECT 2xx 隧道 ----------
f = determine_framing(H(("Content-Length", "10")), is_request=False,
                      method="CONNECT", status=200)
eq("规则2 CONNECT 2xx 是隧道", (f.rule, f.kind), (2, "tunnel"))
f = determine_framing(H(("Content-Length", "10")), is_request=False,
                      method="CONNECT", status=407)
ck("规则2 非 2xx 不命中", f.rule != 2, "rule=%r" % f.rule)

# ---------- §6.3 规则 3：TE 覆盖 CL ----------
f = determine_framing(H(("Content-Length", "6"), ("Transfer-Encoding", "chunked")),
                      is_request=True)
eq("规则3 TE 与 CL 并存走 TE", (f.rule, f.kind), (3, "chunked"))
ck("规则3 标记为走私嫌疑", f.suspect)
f = determine_framing(H(("Transfer-Encoding", "chunked")), is_request=True)
eq("只有 TE 时是规则4", f.rule, 4)
ck("只有 TE 时无嫌疑", not f.suspect)

# ---------- §6.3 规则 4：TE 存在但 chunked 非最终 ----------
f = determine_framing(H(("Transfer-Encoding", "gzip")), is_request=True)
eq("规则4 请求中 chunked 非最终 → 400", (f.rule, f.kind, f.value), (4, "error", 400))
f = determine_framing(H(("Transfer-Encoding", "gzip")), is_request=False, status=200)
eq("规则4 响应中 chunked 非最终 → 读到关闭", (f.rule, f.kind), (4, "close"))
f = determine_framing(H(("Transfer-Encoding", "chunked, gzip")), is_request=True)
eq("规则4 chunked 不是最后一个也算非最终", (f.kind, f.value), ("error", 400))

# ---------- §6.3 规则 5/6：Content-Length ----------
f = determine_framing(H(("Content-Length", "13")), is_request=True)
eq("规则6 合法 CL", (f.rule, f.kind, f.value), (6, "length", 13))
f = determine_framing(H(("Content-Length", "13, 13")), is_request=True)
eq("规则5 逗号列表全相同则可用", (f.rule, f.value), (6, 13))
f = determine_framing(H(("Content-Length", "13, 14")), is_request=True)
eq("规则5 列表值不同 → 400", (f.rule, f.kind), (5, "error"))
f = determine_framing(H(("Content-Length", "abc")), is_request=True)
eq("规则5 非数字 → 400", f.rule, 5)
f = determine_framing(H(("Content-Length", "")), is_request=True)
eq("规则5 空值 → 400", f.rule, 5)
eq("parse_cl 前导零仍合法", parse_cl("007"), (True, 7))

# ---------- §6.3 规则 7/8 ----------
f = determine_framing(H(("Host", "a")), is_request=True)
eq("规则7 请求无 CL/TE → 0", (f.rule, f.kind), (7, "none"))
f = determine_framing(H(("Host", "a")), is_request=False, status=200)
eq("规则8 响应无长度声明 → 读到关闭", (f.rule, f.kind), (8, "close"))

# ---------- §7.1.3 chunked 解码 ----------
body, nxt, err = decode_chunked(b"5\r\nhello\r\n0\r\n\r\n")
eq("chunked 单块", body, b"hello")
eq("chunked 消费到末尾", nxt, 15)
body, nxt, err = decode_chunked(b"5;a=b\r\nhello\r\n0\r\n\r\n")
eq("chunk-ext 被忽略", body, b"hello")
body, nxt, err = decode_chunked(b"3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n")
eq("chunked 多块拼接", body, b"abcde")
body, nxt, err = decode_chunked(b"0\r\nX: y\r\n\r\n")
eq("last-chunk 带 trailer", (body, nxt), (b"", 11))
body, nxt, err = decode_chunked(b"z\r\n")
ck("非法 chunk-size 报错", err is not None and body is None)
body, nxt, err = decode_chunked(b"5\r\nabc")
ck("chunk-data 不足报错", err is not None)
body, nxt, err = decode_chunked(b"0000005\r\nhello\r\n0\r\n\r\n")
eq("chunk-size 前导零", body, b"hello")

# ---------- CL.TE：前端信 CL，后端信 TE ----------
clte = (b"POST / HTTP/1.1\r\n"
        b"Host: a\r\n"
        b"Content-Length: 6\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"\r\n"
        b"0\r\n\r\nG")
front = Parser("front", "cl")
back = Parser("back", "te")
r1, p1, r2, p2, left, err = two_hop(clte, front, back)
ck("CL.TE 前端解析成功", r1 is not None, str(err))
eq("CL.TE 前端按 CL=6 取 body", r1.body, b"0\r\n\r\nG")
eq("CL.TE 前端吞掉整段", p1, len(clte))
ck("CL.TE 后端解析成功", r2 is not None, str(err))
eq("CL.TE 后端按 chunked 取到空 body", r2.body, b"")
eq("CL.TE 走私出一个字节", left, b"G")

# ---------- TE.CL：前端信 TE，后端信 CL ----------
tecl = (b"POST / HTTP/1.1\r\n"
        b"Host: a\r\n"
        b"Content-Length: 4\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"\r\n"
        b"10\r\nGPOST / HTTP/1.1\r\n0\r\n\r\n")
r1, p1, r2, p2, left, err = two_hop(tecl, Parser("f", "te"), Parser("b", "cl"))
ck("TE.CL 前端解析成功", r1 is not None, str(err))
eq("TE.CL 前端按 chunked 取到整块", r1.body, b"GPOST / HTTP/1.1")
eq("TE.CL 前端吞掉整段", p1, len(tecl))
ck("TE.CL 后端解析成功", r2 is not None, str(err))
eq("TE.CL 后端按 CL=4 只吃掉块长行", r2.body, b"10\r\n")
eq("TE.CL 走私出请求前缀", left, b"GPOST / HTTP/1.1\r\n0\r\n\r\n")
eq("TE.CL 走私前缀长度", len(left), 23)

# ---------- 同一字节流，两种策略给出不同边界 ----------
buf = (b"POST /x HTTP/1.1\r\nHost: a\r\n"
       b"Content-Length: 3\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\nZZ")
rcl, pcl = Parser("cl", "cl").parse_one(buf, 0)
rte, pte = Parser("te", "te").parse_one(buf, 0)
# 头部末段不含结束 CRLF：16+2+7+2+17+2+26 = 72，body 从 76 起
eq("头部结束位置", buf.find(b"\r\n\r\n"), 72)
eq("cl 策略只吃 CL 声明的 3 字节", rcl.body, b"0\r\n")
eq("cl 策略边界", pcl, 79)
eq("te 策略按 chunked 取到空 body", rte.body, b"")
eq("te 策略边界", pte, 81)
eq("两策略边界相差 2 字节", pte - pcl, 2)
ck("两策略边界不同", pcl != pte, "cl=%r te=%r" % (pcl, pte))

# ---------- 无分歧时不应走私 ----------
clean = b"GET / HTTP/1.1\r\nHost: a\r\nContent-Length: 0\r\n\r\n"
r1, p1, r2, p2, left, err = two_hop(clean, front, back)
eq("干净请求无残留", left, b"")

# ---------- 头部解析 ----------
h = Headers([("Host", "a"), ("Transfer-Encoding", "chunked")])
eq("头部大小写不敏感", h.get("transfer-encoding"), "chunked")
eq("缺失头部返回 None", h.get("X-Nope"), None)
r, _ = Parser("p", "cl").parse_one(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n")
eq("请求行方法", r.method, "GET")
eq("请求行目标", r.target, "/")

print("OK =", OK)
if FAIL:
    print("FAILED =", len(FAIL))
    for f in FAIL:
        print("  -", f)
else:
    print("ALL OK")
