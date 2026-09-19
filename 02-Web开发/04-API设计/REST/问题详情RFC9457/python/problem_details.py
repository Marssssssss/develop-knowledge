# -*- coding: utf-8 -*-
"""RFC 9457 Problem Details for HTTP APIs —— 数据模型与两侧语义。

只实现规范里"可判定"的部分：
  * 3.1     成员类型不符即忽略（MUST be ignored）
  * 3.1.1   type 缺省 "about:blank"；相对 URI 按 base 解析
  * 3.1.2   status 只作参考，但生成器 MUST 与真实状态码一致
  * 3.2     客户端 MUST ignore 未识别的扩展成员
  * 4       新类型定义 MUST 文档化 type URI / title / status code
  * 4.2.1   about:blank 的 title SHOULD 等于该状态码的推荐短语
  * 附录 A  非规范性 JSON Schema：status 为 100..599 的整数
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

MEDIA_TYPE = "application/problem+json"
MEDIA_TYPE_XML = "application/problem+xml"

DEFAULT_TYPE = "about:blank"

RESERVED = ("type", "status", "title", "detail", "instance")

# RFC 9110 §15.5 / §15.6 的推荐状态短语（本 demo 只收用到的几条）
STATUS_PHRASES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    409: "Conflict",
    412: "Precondition Failed",
    415: "Unsupported Media Type",
    422: "Unprocessable Content",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


# --------------------------------------------------------------------------
# 3.1 成员类型校验：类型不符的 MUST be ignored
# --------------------------------------------------------------------------
def _is_json_string(v: Any) -> bool:
    return isinstance(v, str)


def _is_json_number(v: Any) -> bool:
    # 注意：Python 里 bool 是 int 的子类，而 JSON 的 true/false 不是 number。
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_json_integer(v: Any) -> bool:
    if not _is_json_number(v):
        return False
    return float(v).is_integer()


def is_valid_extension_name(name: str) -> bool:
    """§4：扩展名 SHOULD 以 ALPHA 开头、只含 ALPHA/DIGIT/_ 、长度 >= 3。"""
    if not isinstance(name, str) or len(name) < 3:
        return False
    if not name[0].isalpha():
        return False
    return all(ch.isalnum() or ch == "_" for ch in name)


# --------------------------------------------------------------------------
# RFC 3986 §5 相对引用解析（只覆盖 http/https 场景）
# --------------------------------------------------------------------------
def remove_dot_segments(path: str) -> str:
    out: List[str] = []
    while path:
        if path.startswith("../"):
            path = path[3:]
        elif path.startswith("./"):
            path = path[2:]
        elif path.startswith("/./"):
            path = "/" + path[3:]
        elif path == "/.":
            path = "/"
        elif path.startswith("/../"):
            path = "/" + path[4:]
            if out:
                out.pop()
        elif path == "/..":
            path = "/"
            if out:
                out.pop()
        elif path in (".", ".."):
            path = ""
        else:
            nxt = path.find("/", 1)
            if nxt < 0:
                out.append(path)
                path = ""
            else:
                out.append(path[:nxt])
                path = path[nxt:]
    return "".join(out)


def resolve_uri(base: str, ref: str) -> str:
    """把 ref 相对 base 解析成绝对 URI。ref 已是绝对 URI 则原样返回。"""
    if "://" in ref.split("/")[0]:
        return ref
    scheme, _, rest = base.partition("://")
    authority = rest.split("/", 1)[0]
    base_path = "/" + rest.split("/", 1)[1] if "/" in rest else ""
    if ref.startswith("//"):
        return scheme + ":" + ref
    if ref.startswith("/"):
        return "%s://%s%s" % (scheme, authority, remove_dot_segments(ref))
    if not ref:
        return "%s://%s%s" % (scheme, authority, remove_dot_segments(base_path))
    merged = base_path.rsplit("/", 1)[0] + "/" + ref if base_path else "/" + ref
    return "%s://%s%s" % (scheme, authority, remove_dot_segments(merged))


# --------------------------------------------------------------------------
# Problem 对象
# --------------------------------------------------------------------------
class Problem:
    """规范 §3 的 problem details 对象。"""

    __slots__ = ("type", "status", "title", "detail", "instance",
                 "extensions", "ignored", "raw")

    def __init__(self) -> None:
        self.type: str = DEFAULT_TYPE
        self.status: Optional[int] = None
        self.title: Optional[str] = None
        self.detail: Optional[str] = None
        self.instance: Optional[str] = None
        self.extensions: Dict[str, Any] = {}
        self.ignored: List[str] = []
        self.raw: Dict[str, Any] = {}

    # -- 消费者侧 -----------------------------------------------------------
    @classmethod
    def parse(cls, raw: Dict[str, Any], base_uri: Optional[str] = None) -> "Problem":
        p = cls()
        p.raw = dict(raw)
        if _is_json_string(raw.get("type")):
            p.type = raw["type"]
        elif "type" in raw:
            p.ignored.append("type")
        if _is_json_integer(raw.get("status")):
            v = int(raw["status"])
            if 100 <= v <= 599:
                p.status = v
            else:
                p.ignored.append("status")
        elif "status" in raw:
            p.ignored.append("status")
        for name in ("title", "detail", "instance"):
            if name in raw:
                if _is_json_string(raw[name]):
                    setattr(p, name, raw[name])
                else:
                    p.ignored.append(name)
        for k, v in raw.items():
            if k not in RESERVED:
                p.extensions[k] = v
        if base_uri:
            if _is_json_string(raw.get("type")) and "://" not in p.type:
                p.type = resolve_uri(base_uri, p.type)
            if p.instance and "://" not in p.instance:
                p.instance = resolve_uri(base_uri, p.instance)
        return p

    def known_extension(self, name: str) -> Optional[Any]:
        """§3.2：消费者只取自己认识的扩展，其余一律忽略。"""
        return self.extensions.get(name)

    def title_for_display(self) -> str:
        """§4.2.1：about:blank 时 title SHOULD 等于状态码推荐短语。"""
        if self.type == DEFAULT_TYPE and self.title is None and self.status:
            return STATUS_PHRASES.get(self.status, "")
        return self.title or ""

    # -- 生成器侧 -----------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        if self.type != DEFAULT_TYPE or not self.status:
            d["type"] = self.type
        if self.status is not None:
            d["status"] = self.status
        if self.title:
            d["title"] = self.title
        if self.detail:
            d["detail"] = self.detail
        if self.instance:
            d["instance"] = self.instance
        d.update(self.extensions)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# --------------------------------------------------------------------------
# 附录 B：XML 变体（application/problem+xml）
#   数组不是靠重复同名元素表示 —— 只含若干名为 "i" 的子元素才被当作数组。
# --------------------------------------------------------------------------
XML_NS = "urn:ietf:rfc:7807"


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _xml_value(v: Any) -> str:
    if isinstance(v, list):
        return "".join("<i>%s</i>" % _esc(str(x)) for x in v)
    if isinstance(v, dict):
        return "".join("<%s>%s</%s>" % (k, _xml_value(x), k) for k, x in v.items())
    return _esc(str(v))


def to_xml(p: Problem) -> str:
    body = []
    for name in ("type", "title", "detail", "status", "instance"):
        v = getattr(p, name)
        if v is not None:
            body.append("  <%s>%s</%s>" % (name, _esc(str(v)), name))
    for k, v in p.extensions.items():
        body.append("  <%s>%s</%s>" % (k, _xml_value(v), k))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<problem xmlns="%s">\n%s\n</problem>' % (XML_NS, "\n".join(body)))


def status_consistent(p: Problem, http_status: int) -> bool:
    """§3.1.2：生成器 MUST 让 status 成员与真实 HTTP 状态码一致。"""
    return p.status is None or p.status == http_status


def build(http_status: int,
          type_uri: Optional[str] = None,
          title: Optional[str] = None,
          detail: Optional[str] = None,
          instance: Optional[str] = None,
          **extensions: Any) -> Problem:
    p = Problem()
    p.status = http_status
    if type_uri:
        p.type = type_uri
    else:
        p.type = DEFAULT_TYPE
        title = title or STATUS_PHRASES.get(http_status)
    p.title = title
    p.detail = detail
    p.instance = instance
    p.extensions.update(extensions)
    return p
