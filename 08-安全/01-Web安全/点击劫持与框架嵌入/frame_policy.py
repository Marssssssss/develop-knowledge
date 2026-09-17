"""框架嵌入策略:HTML 标准 §7.7 的 X-Frame-Options 处理模型 + CSP frame-ancestors。

这个算法值得单独实现,因为它有一处**反直觉**的官方结论:
"多个同样的合法值"允许嵌入,"多个非法值"也允许嵌入(等同头被省略),
而"一个合法值 + 任意其它值"**一律拒绝** —— HTML 标准称之为
"block any attempts at applying X-Frame-Options which were trying to do something
valid, but appear confused"。

来源:WHATWG HTML Standard §7.7 The X-Frame-Options header(含官方结果表)、
OWASP Clickjacking Defense Cheat Sheet。
"""
from __future__ import annotations

ALLOWALL = "allowall"
VALID = {"deny", "sameorigin"}


class Candidate:
    """一次"某祖先窗口想把某页面嵌进 frame"的判定请求。"""

    def __init__(self, destination_origin: str, ancestor_origins: list[str],
                 is_child_navigable: bool = True):
        self.destination_origin = destination_origin        # 被嵌页面(子文档)的源
        self.ancestor_origins = ancestor_origins            # 自外向内(最后一个是最内层父窗口)
        self.is_child_navigable = is_child_navigable


def split_header_values(raw_values: list[str]) -> list[str]:
    """Fetch 的 get/decode/split:按 "," 拆并去空白(尾逗号会产生空串)。"""
    out = []
    for v in raw_values:
        for piece in v.split(","):
            out.append(piece.strip())
    return out


def check_x_frame_options(values: list[str], cand: Candidate) -> tuple[bool, str]:
    """返回 (是否允许嵌入, 判定依据)。values 是同一响应里 XFO 头的**所有**值。"""
    # step 1:顶层文档不受 XFO 约束
    if not cand.is_child_navigable:
        return True, "top-level"
    opts = {v.lower() for v in split_header_values(values)}
    # step 5:"困惑"的用法一律拦下
    if len(opts) > 1 and (opts & (VALID | {ALLOWALL})):
        return False, "confused-multiple-values"
    # step 6:多个非法值等同没有这个头
    if len(opts) > 1:
        return True, "all-invalid"
    if not opts:
        return True, "absent"
    only = next(iter(opts))
    # step 7
    if only == "deny":
        return False, "deny"
    # step 8:SAMEORIGIN 要求**每一个**祖先都与子文档同源
    if only == "sameorigin":
        for origin in cand.ancestor_origins:
            if origin != cand.destination_origin:
                return False, "sameorigin-ancestor-mismatch"
        return True, "sameorigin"
    # step 9:孤立的非法值(含废弃的 ALLOW-FROM / ALLOWALL)→ 当作没有
    return True, "lone-invalid"


def parse_csp_headers(headers: dict[str, list[str]]) -> list[dict]:
    """把 Content-Security-Policy(-Report-Only) 头解析成策略列表。"""
    policies = []
    for name, disposition in (("content-security-policy", "enforce"),
                              ("content-security-policy-report-only", "report")):
        for raw in headers.get(name, []):
            directives: dict[str, list[str]] = {}
            for part in raw.split(";"):
                tokens = part.split()
                if not tokens:
                    continue
                directives[tokens[0].lower()] = tokens[1:] if len(tokens) > 1 else []
            policies.append({"disposition": disposition, "directives": directives,
                             "raw": raw})
    return policies


def _split_origin(origin: str) -> tuple[str, str]:
    scheme, sep, rest = origin.partition("://")
    if not sep:
        return "https", origin.split(":")[0]
    return scheme.lower(), rest.split(":")[0].lower()


def frame_ancestors_match(sources: list[str], ancestor_origin: str, self_origin: str) -> bool:
    """CSP frame-ancestors 源列表匹配(覆盖 'none' / 'self' / '*' / host-source)。"""
    if not sources:
        return True                                  # 空列表语法无效,按不限制处理
    if len(sources) == 1 and sources[0].lower() == "'none'":
        return False
    a_scheme, a_host = _split_origin(ancestor_origin)
    for src in sources:
        s = src.lower()
        if s == "'none'":
            continue
        if s == "*":
            return True
        if s == "'self'":
            if ancestor_origin == self_origin:
                return True
            continue
        s_scheme, s_host = _split_origin(s)
        if "://" not in s:
            s_scheme = _split_origin(self_origin)[0]  # 无 scheme → 继承受保护页面的 scheme
        if s_scheme != a_scheme:
            continue
        if s_host.startswith("*."):
            base = s_host[2:]
            if a_host == base or a_host.endswith("." + base):
                return True
        elif a_host == s_host:
            return True
    return False


def decide(headers: dict[str, list[str]], cand: Candidate) -> tuple[bool, str]:
    """完整判定(HTML §7.7 step 2 的"有 frame-ancestors 则忽略 XFO")。"""
    policies = parse_csp_headers(headers)
    for pol in policies:
        if pol["disposition"] != "enforce":
            continue
        if "frame-ancestors" in pol["directives"]:
            srcs = pol["directives"]["frame-ancestors"]
            # 每个祖先都必须匹配(逐层校验,不是只看最内层)
            for origin in reversed(cand.ancestor_origins):
                if not frame_ancestors_match(srcs, origin, cand.destination_origin):
                    return False, f"csp:frame-ancestors({' '.join(srcs) or '<empty>'})"
            return True, f"csp:frame-ancestors({' '.join(srcs) or '<empty>'})"
    return check_x_frame_options(headers.get("x-frame-options", []), cand)


def meta_delivered(headers: dict[str, list[str]]) -> dict[str, list[str]]:
    """<meta http-equiv> 里写的 XFO / CSP frame-ancestors 一律无效(OWASP 明示:
    `frame-ancestors` 必须作为 HTTP 响应头配置,写在 meta 里不生效)。"""
    dropped = dict(headers)
    for key in ("x-frame-options", "content-security-policy",
                "content-security-policy-report-only"):
        dropped.pop(key, None)
    return dropped


# ---------------- 攻击手法 × 防护手段(OWASP Clickjacking Cheat Sheet)----------------
SCENARIOS = [
    ("嵌套的双重 frame", "frame-buster 里访问 parent.location 触发跨源限制,无法跳出",
     "XFO/CSP 在浏览器层拦下,不依赖脚本"),
    ("<iframe sandbox> 禁用子框架 JS", "frame-buster 根本不执行,页面照样显示",
     "XFO/CSP 不依赖 JS"),
    ("JavaScript 被用户禁用/被拦截", "frame-buster 完全不执行",
     "XFO/CSP 不依赖 JS(反过来说:纯脚本方案此刻是 fail-open)"),
    ("点击劫持 + 拖拽/双击变体", "frame-buster 只针对导航",
     "XFO/CSP 与交互方式无关"),
    ("代理剥掉 X-Frame-Options", "头丢失即失效",
     "XFO/CSP 同时下发:即使 XFO 被剥,CSP frame-ancestors 仍在"),
    ("跨站请求带上 session cookie", "iframe 内的请求照样携带 cookie",
     "SameSite=Lax/Strict 阻止跨站请求携带 cookie(与 XFO/CSP 相互独立)"),
]
