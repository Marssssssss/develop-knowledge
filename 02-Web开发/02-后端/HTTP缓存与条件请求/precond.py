"""RFC 9110 §13.2.2 前置条件优先级（自 main.py 拆出，保持单文件 ≤300 行）。

顺序（前两条**只对 origin server 有意义**，缓存不得评估）：
1. If-Match 不匹配 → 412
2. If-Unmodified-Since 不满足 → 412（仅当 If-Match 缺席）
3. If-None-Match 命中 → GET/HEAD 回 304，其它方法回 412
4. If-Modified-Since 未修改 → 304（仅 GET/HEAD 且无 If-None-Match）
5. If-Range + Range 满足 → 206（仅 GET）
6. 否则执行方法
"""

from __future__ import annotations

from typing import Dict, Optional

from main import StoredResponse, http_date

# ---------------------------------------------------------------------------
# RFC 9110 §13.2.2 前置条件优先级
# ---------------------------------------------------------------------------
def evaluate_preconditions(req: Dict[str, str], resp: StoredResponse,
                           is_origin: bool = True) -> Optional[int]:
    """返回应直接给出的状态码；None 表示条件全部通过，正常执行方法。"""
    method = req.get("__method__", "GET")
    # 1. If-Match 只对 origin 有意义
    if is_origin and "if-match" in req:
        if not etag_list_match(req["if-match"], resp.headers.get("etag")):
            return 412
    # 2. If-Unmodified-Since 同上
    elif is_origin and "if-unmodified-since" in req:
        if not unmodified_since(req["if-unmodified-since"], resp):
            return 412
    # 3. If-None-Match
    if "if-none-match" in req:
        if etag_list_match(req["if-none-match"], resp.headers.get("etag")):
            return 304 if method in ("GET", "HEAD") else 412
    # 4. If-Modified-Since（仅 GET/HEAD 且无 If-None-Match）
    elif method in ("GET", "HEAD") and "if-modified-since" in req:
        if unmodified_since(req["if-modified-since"], resp):
            return 304
    # 5. If-Range（仅 GET + Range）
    if method == "GET" and "range" in req and "if-range" in req:
        return 206
    return None


def etag_list_match(header_value: str, etag: Optional[str]) -> bool:
    if header_value.strip() == "*":
        return etag is not None
    if etag is None:
        return False
    return any(t.strip() == etag for t in header_value.split(","))


def unmodified_since(header_value: str, resp: StoredResponse) -> bool:
    """True 表示「自该时刻以来资源未修改」（If-Unmodified-Since 为真 / If-Modified-Since 为假）。"""
    since = http_date(header_value) if isinstance(header_value, str) else header_value
    lm = resp.headers.get("last-modified")
    if lm is None:
        d = resp.headers.get("date")
        lm = http_date(d) if isinstance(d, str) else (d if d is not None else 0.0)
    else:
        lm = http_date(lm) if isinstance(lm, str) else lm
    return lm <= since
