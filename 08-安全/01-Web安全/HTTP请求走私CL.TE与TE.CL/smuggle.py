"""HTTP/1.1 请求走私最小模型。

依据 RFC 9112（https://www.rfc-editor.org/rfc/rfc9112.txt ，109913 B 全文实读）：
  §6.3 消息体长度由以下之一确定（**按优先级顺序**）：
    1. HEAD 响应 / 1xx / 204 / 304 → 首空行即结束，无 body
    2. CONNECT 的 2xx 响应 → 隧道，忽略 CL 与 TE
    3. TE 与 CL 同时出现 → **TE 覆盖 CL**；可能是走私尝试，ought to be 报错；
       中介转发前 MUST 先删掉收到的 Content-Length
    4. TE 存在且 chunked 为最终编码 → 按 chunked 读；
       响应中 chunked 非最终 → 读到连接关闭；
       **请求中 chunked 非最终 → 400 并关闭连接**
    5. 无 TE 且 CL 非法 → 不可恢复错误；除非能按逗号列表解析、全部合法且全部相同
    6. 无 TE 且 CL 合法 → 十进制定长
    7. 请求且以上都不成立 → 长度为 0
    8. 否则（响应）→ 读到连接关闭
  §7.1 chunked ABNF：chunk = chunk-size [ chunk-ext ] CRLF chunk-data CRLF
       last-chunk = 1*("0") [ chunk-ext ] CRLF ；随后 trailer-section CRLF
  §7.1.1 chunk-ext 必须被忽略（不认识的）
  §11.2 请求走私：利用不同接收方的解析差异隐藏额外请求
"""

import re

CRLF = b"\r\n"
TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")
DIGITS_RE = re.compile(r"^[0-9]+$")


class Framing:
    """§6.3 的一条判定结果。"""

    def __init__(self, rule, kind, value=None, suspect=False):
        self.rule = rule
        self.kind = kind          # none/tunnel/chunked/close/error/length
        self.value = value        # 长度或错误码
        self.suspect = suspect    # TE 与 CL 同时出现（§11.2 走私嫌疑）

    def __repr__(self):
        return "Framing(rule=%d, kind=%s, value=%r, suspect=%s)" % (
            self.rule, self.kind, self.value, self.suspect)


class Headers:
    """保序、大小写不敏感的头部集合。"""

    def __init__(self, pairs):
        self.pairs = list(pairs)

    def get_all(self, name):
        return [v for (n, v) in self.pairs if n.lower() == name.lower()]

    def get(self, name):
        vs = self.get_all(name)
        return ", ".join(vs) if vs else None


def parse_te(value):
    """§6.1/§7：transfer-coding 名字大小写不敏感，逗号分隔。返回小写编码列表。"""
    out = []
    for part in value.split(","):
        tok = part.strip()
        if not tok:
            continue
        if not TOKEN_RE.match(tok):
            return None          # 非法 token
        out.append(tok.lower())
    return out


def parse_cl(value):
    """§6.3 规则 5/6：逗号列表，全部合法且全部相同才可用。返回 (ok, n)。"""
    parts = [p.strip() for p in value.split(",")]
    if not parts or any(p == "" for p in parts):
        return (False, None)
    nums = []
    for p in parts:
        if not DIGITS_RE.match(p):
            return (False, None)
        nums.append(int(p))
    if len(set(nums)) != 1:
        return (False, None)
    return (True, nums[0])


def determine_framing(h, is_request=True, method="GET", status=None):
    """§6.3 八条判定，按优先级顺序。"""
    te_raw = h.get("Transfer-Encoding")
    cl_raw = h.get("Content-Length")
    te = parse_te(te_raw) if te_raw is not None else None

    if not is_request:
        if method == "HEAD":
            return Framing(1, "none", 0)
        if status is not None and (100 <= status < 200 or status in (204, 304)):
            return Framing(1, "none", 0)
    if (not is_request and method == "CONNECT" and status is not None
            and 200 <= status < 300):
        return Framing(2, "tunnel", 0)

    if te:
        suspect = cl_raw is not None
        if te[-1] == "chunked":
            return Framing(3 if suspect else 4, "chunked", None, suspect)
        if is_request:
            return Framing(4, "error", 400, suspect)
        return Framing(4, "close", None, suspect)

    if cl_raw is not None:
        ok, n = parse_cl(cl_raw)
        if not ok:
            return Framing(5, "error", 400)
        return Framing(6, "length", n)

    if is_request:
        return Framing(7, "none", 0)
    return Framing(8, "close", None)


def decode_chunked(buf, pos=0):
    """§7.1.3 解码 chunked。返回 (body, next_pos, err)。"""
    out = bytearray()
    while True:
        i = buf.find(CRLF, pos)
        if i < 0:
            return (None, pos, "truncated: chunk-size 后无 CRLF")
        line = buf[pos:i].decode("latin-1")
        semi = line.find(";")
        size_tok = (line[:semi] if semi >= 0 else line).strip()
        if not HEX_RE.match(size_tok):
            return (None, pos, "bad chunk-size %r" % size_tok)
        size = int(size_tok, 16)
        pos = i + 2
        if size == 0:
            # last-chunk 之后是 trailer-section：*( field-line CRLF ) CRLF
            while True:
                j = buf.find(CRLF, pos)
                if j < 0:
                    return (None, pos, "truncated: trailer 未终止")
                if j == pos:
                    return (bytes(out), pos + 2, None)
                pos = j + 2
        if len(buf) < pos + size + 2:
            return (None, pos, "truncated: chunk-data 不足")
        out += buf[pos:pos + size]
        pos += size
        if buf[pos:pos + 2] != CRLF:
            return (None, pos, "chunk-data 后不是 CRLF")
        pos += 2


class Request:
    def __init__(self, method, target, headers, body):
        self.method = method
        self.target = target
        self.headers = headers
        self.body = body

    def __repr__(self):
        return "Request(%s %s, %d B body)" % (self.method, self.target, len(self.body))


class Parser:
    """一个接收方。policy='cl' 时 CL 优先，policy='te' 时 TE 优先。

    两者对同一字节流的分歧正是 §11.2 请求走私的根因。
    """

    def __init__(self, name, policy):
        assert policy in ("cl", "te")
        self.name = name
        self.policy = policy

    def _choose(self, h):
        """返回 ('chunked'|'length'|'none', 参数)。"""
        te_raw = h.get("Transfer-Encoding")
        cl_raw = h.get("Content-Length")
        codings = parse_te(te_raw) if te_raw is not None else None
        chunked_ok = bool(codings) and codings[-1] == "chunked"
        if self.policy == "te":
            if chunked_ok:
                return ("chunked", None)
            if cl_raw is not None:
                ok, n = parse_cl(cl_raw)
                return ("length", n if ok else None)
            return ("none", 0)
        if cl_raw is not None:
            ok, n = parse_cl(cl_raw)
            if ok:
                return ("length", n)
        if chunked_ok:
            return ("chunked", None)
        return ("none", 0)

    def parse_one(self, buf, pos=0):
        """从 buf[pos:] 读一个请求。返回 (Request, next_pos) 或 (None, err)。"""
        end = buf.find(CRLF + CRLF, pos)
        if end < 0:
            return (None, "%s: 头部未终止" % self.name)
        head = buf[pos:end].decode("latin-1")
        body_at = end + 4
        lines = head.split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3:
            return (None, "%s: 请求行不合法" % self.name)
        method, target, _ver = parts
        pairs = []
        for ln in lines[1:]:
            k, _, v = ln.partition(":")
            pairs.append((k.strip(), v.strip()))
        h = Headers(pairs)

        kind, arg = self._choose(h)
        if kind == "none":
            return (Request(method, target, h, b""), body_at)
        if kind == "length":
            if arg is None:
                return (None, "%s: Content-Length 非法" % self.name)
            if len(buf) < body_at + arg:
                return (None, "%s: body 不足 %d 字节" % (self.name, arg))
            return (Request(method, target, h, buf[body_at:body_at + arg]),
                    body_at + arg)
        body, nxt, err = decode_chunked(buf, body_at)
        if err:
            return (None, "%s: %s" % (self.name, err))
        return (Request(method, target, h, body), nxt)


def two_hop(buf, front, back):
    """前端读一个请求后把「它认为的完整请求」原样转发给后端。

    返回 (front_req, front_end, back_req, back_end, leftover)；
    leftover 是后端连接上残留的字节 —— 即被走私的前缀。
    """
    r1, p1 = front.parse_one(buf, 0)
    if r1 is None:
        return (None, 0, None, 0, b"", p1)
    forwarded = buf[:p1]
    r2, p2 = back.parse_one(forwarded, 0)
    if r2 is None:
        return (r1, p1, None, p2, b"", p2)
    return (r1, p1, r2, p2, forwarded[p2:], None)
