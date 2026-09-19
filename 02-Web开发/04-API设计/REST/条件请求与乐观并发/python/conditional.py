# -*- coding: utf-8 -*-
"""RFC 9110 §13 条件请求 + §8.8.3 校验器比较 —— 可判定的那部分。

覆盖：
  * §8.8.3.2 强/弱比较表（Table 3 四行）
  * §13.1.1  If-Match      —— MUST 用强比较
  * §13.1.2  If-None-Match —— MUST 用弱比较
  * §13.1.3  If-Modified-Since（被 If-None-Match 屏蔽；非 GET/HEAD 忽略）
  * §13.1.4  If-Unmodified-Since（被 If-Match 屏蔽）
  * §13.1.5  If-Range（精确匹配，非 "<="）
  * §13.2.1  何时求值（非 2xx/412 的基线响应优先；CONNECT/OPTIONS/TRACE 忽略）
  * §13.2.2  六步优先级表
"""

from __future__ import annotations

import calendar
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

# §13.2.1：这些方法与"选择/修改某个表示"无关，条件头一律忽略
NO_SELECTION_METHODS = ("CONNECT", "OPTIONS", "TRACE")


# --------------------------------------------------------------------------
# §8.8.3.2 校验器比较
# --------------------------------------------------------------------------
def parse_entity_tag(raw: str) -> Optional[Tuple[bool, str]]:
    """把 '"1"' / 'W/"1"' 解析成 (是否弱, opaque-tag)。非法返回 None。"""
    if not isinstance(raw, str) or len(raw) < 2:
        return None
    weak = False
    body = raw
    # weak 前缀是大小写敏感的 "W/"（§8.8.3）
    if body.startswith("W/"):
        weak = True
        body = body[2:]
    elif body.startswith("w/"):
        return None
    if len(body) < 2 or body[0] != '"' or body[-1] != '"':
        return None
    return (weak, body[1:-1])


def strong_compare(a: str, b: str) -> bool:
    """强比较：两者都**不**弱，且 opaque-tag 逐字符相同。"""
    pa, pb = parse_entity_tag(a), parse_entity_tag(b)
    if pa is None or pb is None:
        return False
    return (not pa[0]) and (not pb[0]) and pa[1] == pb[1]


def weak_compare(a: str, b: str) -> bool:
    """弱比较：只看 opaque-tag，弱标记不参与。"""
    pa, pb = parse_entity_tag(a), parse_entity_tag(b)
    if pa is None or pb is None:
        return False
    return pa[1] == pb[1]


def parse_http_date(value: Any) -> Optional[int]:
    """HTTP-date → epoch 秒；非法值返回 None（§13.1.3/§13.1.4 要求忽略）。"""
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return calendar.timegm(dt.utctimetuple())


def split_list(value: str) -> List[str]:
    """条件头的 #entity-tag 列表：逗号分隔并去掉空白。"""
    if value is None:
        return []
    if value.strip() == "*":
        return ["*"]
    return [x.strip() for x in value.split(",") if x.strip()]


# --------------------------------------------------------------------------
# 资源与服务器
# --------------------------------------------------------------------------
class Resource:
    """目标资源的当前状态：是否存在、当前 ETag、最后修改时间。"""

    def __init__(self, exists: bool = True, etag: str = '"1"',
                 last_modified: int = 1000) -> None:
        self.exists = exists
        self.etag = etag
        self.last_modified = last_modified


class Response:
    def __init__(self, status: int, reason: str, ignored: Optional[List[str]] = None,
                 applied: bool = False) -> None:
        self.status = status
        self.reason = reason
        self.ignored = ignored or []
        self.applied = applied

    def __repr__(self) -> str:
        return "Response(%d, %r)" % (self.status, self.reason)


class OriginServer:
    """源服务器侧的条件求值（§13.2.2 六步）。"""

    def __init__(self, resource: Resource, base_status: int = 200) -> None:
        self.resource = resource
        # base_status = 若不带任何条件头、在处理请求内容之前本会返回的状态码
        self.base_status = base_status

    def handle(self, method: str, headers: Dict[str, str],
               has_range: bool = False, already_applied: bool = False) -> Response:
        m = method.upper()
        ignored: List[str] = []

        # §13.2.1 step 1：与方法无关的选择 → 忽略全部条件
        if m in NO_SELECTION_METHODS:
            return Response(200, "method does not select a representation",
                            sorted(headers.keys()))

        # §13.2.1：基线响应不是 2xx 也不是 412 → 全部条件被忽略
        if not (200 <= self.base_status < 300 or self.base_status == 412):
            return Response(self.base_status,
                            "baseline failure/redirect takes precedence",
                            sorted(headers.keys()))

        r = self.resource
        im = headers.get("If-Match")
        inm = headers.get("If-None-Match")
        ius = headers.get("If-Unmodified-Since")
        ims = headers.get("If-Modified-Since")
        irng = headers.get("If-Range")

        # step 1：If-Match（强比较）
        if im is not None:
            ok = self._eval_if_match(im)
            if not ok:
                # MAY：若变更看起来已被应用过，可以回 2xx
                if already_applied:
                    return Response(200, "already applied (§13.1.1 MAY)", [], False)
                return Response(412, "If-Match failed", [])
        else:
            if ius is not None:
                ignored.append("If-Unmodified-Since")  # 被 If-Match 屏蔽的前提不存在
                # step 2：If-Unmodified-Since
                ts = parse_http_date(ius)
                if ts is None:
                    ignored.append("If-Unmodified-Since(invalid date)")
                else:
                    ok = not r.exists or r.last_modified <= ts
                    if not ok:
                        if already_applied:
                            return Response(200, "already applied (§13.1.4 MAY)", [], False)
                        return Response(412, "If-Unmodified-Since failed", [])

        # step 3：If-None-Match（弱比较）
        if inm is not None:
            if ims is not None:
                ignored.append("If-Modified-Since")  # §13.1.3 MUST ignore
            ok = self._eval_if_none_match(inm)
            if not ok:
                if m in ("GET", "HEAD"):
                    return Response(304, "If-None-Match failed on GET/HEAD", [])
                return Response(412, "If-None-Match failed on unsafe method", [])
        # step 4：If-Modified-Since（仅 GET/HEAD 且无 If-None-Match）
        elif ims is not None:
            if m not in ("GET", "HEAD"):
                ignored.append("If-Modified-Since(non-GET/HEAD)")
            else:
                ts = parse_http_date(ims)
                if ts is None:
                    ignored.append("If-Modified-Since(invalid date)")
                elif r.last_modified <= ts:
                    return Response(304, "If-Modified-Since false", [])

        # step 5：If-Range 只在 GET + Range 同时存在时求值
        if irng is not None:
            if not (m == "GET" and has_range):
                ignored.append("If-Range(no Range or non-GET)")
            elif self._eval_if_range(irng):
                return Response(206, "If-Range true → partial content", ignored, True)
            else:
                ignored.append("Range(If-Range false → ignore Range)")
                return Response(200, "If-Range false → whole representation", ignored, True)

        # step 6：执行方法
        return Response(200, "perform requested method", ignored, True)

    # -- 各条件头求值 --------------------------------------------------------
    def _eval_if_match(self, value: str) -> bool:
        r = self.resource
        items = split_list(value)
        if items == ["*"]:
            return r.exists
        return any(strong_compare(t, r.etag) for t in items)

    def _eval_if_none_match(self, value: str) -> bool:
        r = self.resource
        items = split_list(value)
        if items == ["*"]:
            return not r.exists
        if not r.exists:
            return True
        return not any(weak_compare(t, r.etag) for t in items)

    def _eval_if_range(self, value: str) -> bool:
        """§13.1.5：entity-tag 走强比较的精确匹配；HTTP-date 走精确相等。"""
        r = self.resource
        if not r.exists:
            return False
        if isinstance(value, str) and (value[:1] == '"' or value[:2] == "W/"):
            return strong_compare(value, r.etag)
        ts = parse_http_date(value)
        if ts is None:
            return False
        return ts == r.last_modified


def lost_update_demo() -> Tuple[int, int]:
    """复刻 'lost update'：两个客户端并发 PUT 同一资源。

    返回 (无 If-Match 时后写者的结果, 有 If-Match 时后写者的结果)。
    """
    r = Resource(exists=True, etag='"v1"', last_modified=1000)
    srv = OriginServer(r)
    seen_etag = r.etag

    # A 先写成功，ETag 前进到 v2
    r.etag = '"v2"'
    r.last_modified = 1001

    # B 拿着旧 ETag 写：没有 If-Match 就直接覆盖（丢更新）
    naive = OriginServer(r).handle("PUT", {}).status
    # B 带上 If-Match（旧 ETag）→ 强比较失败 → 412
    guarded = OriginServer(r).handle("PUT", {"If-Match": seen_etag}).status
    return naive, guarded
