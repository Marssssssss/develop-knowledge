"""RateLimit / RateLimit-Policy 响应头部的解析、校验与客户端决策。

权威依据：draft-ietf-httpapi-ratelimit-headers-11《RateLimit header fields for HTTP》
（2026-05，全文 67 KB 实读）与 RFC 6585 §4（429 Too Many Requests）。

字段值本身是 RFC 9651 Structured Fields 的 List of Items：
    RateLimit-Policy: "burst";q=100;w=60,"daily";q=1000;w=86400
    RateLimit:        "default";r=50;t=30
"""

from __future__ import annotations

import base64

QUOTA_UNITS = ("requests", "content-bytes", "concurrent-requests")

PROBLEM_TYPES = {
    "quota-exceeded": "https://iana.org/assignments/http-problem-types#quota-exceeded",
    "temporary-reduced-capacity":
        "https://iana.org/assignments/http-problem-types#temporary-reduced-capacity",
    "abnormal-usage-detected":
        "https://iana.org/assignments/http-problem-types#abnormal-usage-detected",
}

# draft-11 §5 各问题类型示例里出现的状态码与原因短语（原文照录，含其笔误）
PROBLEM_STATUS = {
    "quota-exceeded": (429, "Bad Request"),        # §5.1 原文如此（429 的规范短语是 Too Many Requests）
    "temporary-reduced-capacity": (503, "Server Unavailable"),
    "abnormal-usage-detected": (429, "Too Many Requests"),
}


class HeaderError(ValueError):
    """字段值不合规范。规范 §7：Malformed RateLimit header fields MUST be ignored."""


def _split_items(value: str):
    """按顶层逗号切分 SF list（引号内与字节序列内的逗号不切）。"""
    items, buf, in_str, in_b64 = [], [], False, False
    for ch in value:
        if ch == '"':
            in_str = not in_str
            buf.append(ch)
        elif ch == ":":
            in_b64 = not in_b64
            buf.append(ch)
        elif ch == "," and not in_str and not in_b64:
            items.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items


def _parse_item(raw: str):
    """解析一个 SF item：裸标识符、`"string"` 或 `:bytes:`，后接 ;k=v 参数。"""
    parts = raw.split(";")
    token = parts[0].strip()
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        name = token[1:-1]
    elif token.startswith(":") and token.endswith(":") and len(token) >= 2:
        name = token[1:-1]
    else:
        raise HeaderError("item 必须是 String 或 Byte Sequence：%r" % token)
    params = {}
    for p in parts[1:]:
        if "=" not in p:
            raise HeaderError("参数缺值：%r" % p)
        k, v = p.split("=", 1)
        params[k.strip()] = v.strip()
    return name, params


def _as_int(params, key, required=False, allow_zero=True, default=None):
    if key not in params:
        if required:
            raise HeaderError("缺少必填参数 %r" % key)
        return default
    raw = params[key]
    try:
        v = int(raw)
    except ValueError:
        raise HeaderError("参数 %r 必须是整数：%r" % (key, raw))
    if v < 0 or (v == 0 and not allow_zero):
        raise HeaderError("参数 %r 取值非法：%d" % (key, v))
    return v


def _as_bytes(params, key):
    if key not in params:
        return None
    raw = params[key]
    if not (raw.startswith(":") and raw.endswith(":")):
        raise HeaderError("参数 %r 必须是 Byte Sequence：%r" % (key, raw))
    body = raw[1:-1]
    try:
        return base64.b64decode(body + "=" * (-len(body) % 4))
    except Exception as exc:  # noqa: BLE001
        raise HeaderError("Byte Sequence 解码失败：%r" % raw) from exc


class QuotaPolicyItem:
    """RateLimit-Policy 的一个 Item：策略名 + q/qu/w/pk。"""

    def __init__(self, name, q, qu="requests", w=None, pk=None, extras=None):
        self.name = name
        self.q = q
        self.qu = qu
        self.w = w
        self.pk = pk
        self.extras = extras or {}

    @property
    def rate(self):
        """每秒配额；无 w 时不可推算（返回 None）。"""
        if self.w is None:
            return None
        return self.q / self.w


class ServiceLimitItem:
    """RateLimit 的一个 Item：策略名 + r/t/pk。"""

    def __init__(self, name, r, t=None, pk=None, extras=None):
        self.name = name
        self.r = r
        self.t = t
        self.pk = pk
        self.extras = extras or {}

    @property
    def rate(self):
        if self.t is None or self.t == 0:
            return None
        return self.r / self.t


def parse_rate_limit_policy(value: str):
    """§3：非空 List of Items；q 必填且为非负整数；w 非负**且非零**；pk 为字节序列。"""
    items = []
    for raw in _split_items(value):
        name, params = _parse_item(raw)
        q = _as_int(params, "q", required=True)
        qu = params.get("qu")
        if qu is not None:
            if not (qu.startswith('"') and qu.endswith('"')):
                raise HeaderError("qu 必须是 String：%r" % qu)
            qu = qu[1:-1]
            if qu not in QUOTA_UNITS:
                raise HeaderError("未在配额单位注册表中的 qu：%r" % qu)
        else:
            qu = "requests"
        w = _as_int(params, "w", allow_zero=False)       # §3.1.3 非负且非零
        pk = _as_bytes(params, "pk")
        items.append(QuotaPolicyItem(name, q, qu, w, pk))
    if not items:
        raise HeaderError("RateLimit-Policy 必须是非空 List")
    return items


def parse_rate_limit(value: str):
    """§4：List of Items；r 必填非负整数；t 非负整数；pk 为字节序列。"""
    items = []
    for raw in _split_items(value):
        name, params = _parse_item(raw)
        r = _as_int(params, "r", required=True)
        t = _as_int(params, "t")
        pk = _as_bytes(params, "pk")
        items.append(ServiceLimitItem(name, r, t, pk))
    if not items:
        raise HeaderError("RateLimit 必须是非空 List")
    return items


def safe_wait(headers: dict, retry_after=None):
    """客户端决策：返回应当等待的秒数。

    优先级链（§7）：Retry-After 存在时 **MUST** 优先，有效窗口 MAY 被忽略；
    否则取所有 RateLimit Item 中最保守（速率最小）的一个换算出的等待时间；
    无法推算（无 t）时返回 0——规范不要求客户端此时退避。
    """
    if retry_after is not None:
        return float(retry_after)
    raw = headers.get("RateLimit")
    if not raw:
        return 0.0
    try:
        items = parse_rate_limit(raw)
    except HeaderError:
        return 0.0                       # §7 畸形字段 MUST be ignored
    best = 0.0
    for it in items:
        if it.rate is None:
            continue
        best = max(best, 1.0 / it.rate)  # 最保守：按该速率发一个请求所需时间
    return best


def is_throttled_problem(payload: dict):
    """§5：识别三种问题类型。返回 (是否限流类问题, violated-policies)。"""
    t = payload.get("type", "")
    for key, uri in PROBLEM_TYPES.items():
        if t == uri or t.endswith("#" + key):
            return True, list(payload.get("violated-policies", []))
    return False, []
