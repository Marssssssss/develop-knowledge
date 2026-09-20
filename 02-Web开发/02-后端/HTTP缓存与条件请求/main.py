"""RFC 9111（HTTP Caching）与 RFC 9110 §13（Conditional Requests）的可执行模型。

时间一律用 epoch 秒（整数或 float）；http-date 只做 IMF-fixdate 的最小解析，
因为新鲜度判据全部是秒级算术。

官方口径（逐条对应）：
* §4.2    ``response_is_fresh = (freshness_lifetime > current_age)``
* §4.2.1 freshness_lifetime 取**第一个匹配**：共享缓存且 s-maxage 存在 → s-maxage；
  max-age → max-age；Expires → Expires 减 Date；否则无显式过期，走启发式
* §4.2.2 「A cache MUST NOT use heuristics ... when an explicit expiration time is
  present」；有 Last-Modified 时「no more than some fraction of the interval since
  that time. A typical setting of this fraction might be 10%.」
* §4.2.3 apparent_age = max(0, response_time - date_value)；
  response_delay = response_time - request_time；
  corrected_age_value = age_value + response_delay；
  保守版 corrected_initial_age = max(apparent_age, corrected_age_value)；
  resident_time = now - response_time；current_age = corrected_initial_age + resident_time
* §4.1   Vary 匹配：可加删空白、合并同名多值、按字段规范归一化大小写；
  「If (after any normalization ...) a header field is absent from a request, it can
  only match another request if it is also absent there.」；``Vary: *`` 恒不匹配
* §4.2.4 显式禁止（no-cache / must-revalidate / proxy-revalidate / s-maxage）时不许出 stale
* §4.3.1 生成条件请求：有 ETag **必须**发 If-None-Match；单条且非子范围且有
  Last-Modified 时 **应该**发 If-Modified-Since
* §4.3.3 304 → 更新并复用；完整响应 → 替换；5xx → 可沿用 stale（在允许 stale 的前提下）
* §4.3.4 freshening：强校验器优先；其次弱校验器取最近的；都没有且仅一条且该条也无校验器 → 更新
* RFC 9110 §13.2.2 前置条件优先级（见 evaluate_preconditions）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

HTTP_DATE_FMT = "%a, %d %b %Y %H:%M:%S GMT"


def http_date(s: str) -> float:
    return datetime.strptime(s, HTTP_DATE_FMT).replace(tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# Cache-Control 解析
# ---------------------------------------------------------------------------
def parse_cache_control(value: Optional[str]) -> Dict[str, Optional[str]]:
    """解析 Cache-Control：返回 {指令名: 参数或 None}。重复出现取第一次（§4.2.1 注）。"""
    out: Dict[str, Optional[str]] = {}
    if not value:
        return out
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            k, v = k.strip().lower(), v.strip().strip('"')
        else:
            k, v = part.lower(), None
        out.setdefault(k, v)
    return out


def directive_int(d: Dict[str, Optional[str]], key: str) -> Optional[int]:
    if key not in d:
        return None
    try:
        return int(d[key])
    except (TypeError, ValueError):
        return None          # 「invalid freshness information ... to be stale」


# ---------------------------------------------------------------------------
# §4.2.1 / §4.2.2 新鲜度生命周期
# ---------------------------------------------------------------------------
class StoredResponse:
    def __init__(self, status=200, headers=None, body=None, request_headers=None):
        self.status = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body = body
        self.request_headers = {k.lower(): v for k, v in (request_headers or {}).items()}

    def cc(self) -> Dict[str, Optional[str]]:
        return parse_cache_control(self.headers.get("cache-control"))

    def date_value(self, response_time: float) -> float:
        d = self.headers.get("date")
        return http_date(d) if isinstance(d, str) else (d if d is not None else response_time)


def freshness_lifetime(resp: StoredResponse, shared: bool) -> Optional[float]:
    """返回显式新鲜度生命周期（秒）；None 表示「无显式过期」，可走启发式。"""
    cc = resp.cc()
    if shared:
        s_max = directive_int(cc, "s-maxage")
        if s_max is not None:
            return float(s_max)
    m = directive_int(cc, "max-age")
    if m is not None:
        return float(m)
    exp = resp.headers.get("expires")
    if exp is not None:
        e = http_date(exp) if isinstance(exp, str) else exp
        # 用 Date 而不是本地时钟，正是为了抵消 origin 与 cache 之间的时钟偏移
        return e - resp.date_value(0.0)
    return None


def heuristic_lifetime(resp: StoredResponse, now: float, fraction: float = 0.1) -> Optional[float]:
    """§4.2.2：无显式过期时的启发式。返回 None 表示不允许用启发式。"""
    if freshness_lifetime(resp, shared=True) is not None or \
            freshness_lifetime(resp, shared=False) is not None:
        return None                       # 有显式过期 → 禁止启发式
    lm = resp.headers.get("last-modified")
    if lm is None:
        return None
    lm = http_date(lm) if isinstance(lm, str) else lm
    return max(0.0, fraction * (resp.date_value(now) - lm))


def effective_lifetime(resp: StoredResponse, shared: bool, now: float,
                       heuristic: bool = True, fraction: float = 0.1) -> Optional[float]:
    fl = freshness_lifetime(resp, shared)
    if fl is not None:
        return fl
    return heuristic_lifetime(resp, now, fraction) if heuristic else None


# ---------------------------------------------------------------------------
# §4.2.3 Age
# ---------------------------------------------------------------------------
def calculate_age(resp: StoredResponse, now: float, request_time: float,
                  response_time: float, conservative: bool = True) -> float:
    date_value = resp.date_value(response_time)
    apparent_age = max(0.0, response_time - date_value)
    age_value = float(resp.headers["age"]) if "age" in resp.headers else 0.0
    response_delay = response_time - request_time
    corrected_age_value = age_value + response_delay
    corrected_initial_age = max(apparent_age, corrected_age_value) if conservative \
        else corrected_age_value
    resident_time = now - response_time
    return corrected_initial_age + resident_time


def response_is_fresh(resp: StoredResponse, shared: bool, now: float, request_time: float,
                      response_time: float, conservative: bool = True,
                      heuristic: bool = True) -> bool:
    fl = effective_lifetime(resp, shared, now, heuristic)
    if fl is None:
        return False
    return fl > calculate_age(resp, now, request_time, response_time, conservative)


# ---------------------------------------------------------------------------
# §4.2.4 是否允许出 stale
# ---------------------------------------------------------------------------
def may_serve_stale(resp: StoredResponse, shared: bool, request_cc: Dict[str, Optional[str]],
                    disconnected: bool = False) -> bool:
    cc = resp.cc()
    for forbidden in ("no-cache", "must-revalidate"):
        if forbidden in cc:
            return False
    if shared and "proxy-revalidate" in cc:
        return False
    if shared and "s-maxage" in cc:
        return False
    if disconnected:
        return True
    return directive_int(request_cc, "max-stale") is not None


# ---------------------------------------------------------------------------
# §4.1 Vary 匹配
# ---------------------------------------------------------------------------
CASE_INSENSITIVE_VALUES = {"accept-encoding", "accept-charset", "connection", "vary"}


def _normalize(name: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    parts = [p.strip() for p in value.split(",")]
    parts = [p for p in parts if p]
    joined = ", ".join(parts)
    return joined.lower() if name in CASE_INSENSITIVE_VALUES else joined


def vary_matches(stored_req: Dict[str, str], new_req: Dict[str, str],
                 vary: Optional[str]) -> bool:
    if not vary:
        return True
    fields = [f.strip().lower() for f in vary.split(",") if f.strip()]
    if "*" in fields:
        return False                       # 「always fails to match」
    for f in fields:
        a = _normalize(f, stored_req.get(f))
        b = _normalize(f, new_req.get(f))
        if a is None or b is None:
            if a is not b:
                return False               # 一方缺失即不匹配
            continue
        if a != b:
            return False
    return True


def select_stored(entries: List[StoredResponse], new_req: Dict[str, str],
                  now: float) -> Optional[StoredResponse]:
    """§4.1：选一条可复用的；多条都匹配时取 Date 最新的一条。"""
    cands = [e for e in entries
             if vary_matches(e.request_headers, new_req, e.headers.get("vary"))]
    if not cands:
        return None
    # 「one or more omits the Vary header field → SHOULD choose the most recent ... with
    #  a valid Vary field value」
    with_vary = [e for e in cands if e.headers.get("vary")]
    pool = with_vary if with_vary else cands
    return max(pool, key=lambda e: e.date_value(now))


# ---------------------------------------------------------------------------
# §4.3.1 生成条件请求
# ---------------------------------------------------------------------------
def build_validation_request(stored: StoredResponse, subrange: bool = False) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if "etag" in stored.headers:
        out["if-none-match"] = stored.headers["etag"]          # MUST
    if (not subrange) and "last-modified" in stored.headers:
        out["if-modified-since"] = stored.headers["last-modified"]   # SHOULD
    return out


# ---------------------------------------------------------------------------
# §4.3.4 freshening
# ---------------------------------------------------------------------------
def is_strong(etag: str) -> bool:
    return not etag.startswith("W/")


def freshen(entries: List[StoredResponse], resp304: StoredResponse) -> List[StoredResponse]:
    """返回被 304 更新过的存储响应列表（可能为空 = MUST NOT update）。"""
    new_etag = resp304.headers.get("etag")
    new_weak = bool(new_etag) and not is_strong(new_etag)
    if new_etag and is_strong(new_etag):
        hit = [e for e in entries if e.headers.get("etag") == new_etag]
        if not hit:
            return []
        for e in hit:
            e.headers.update(resp304.headers)
        return hit
    if new_weak:
        hit = [e for e in entries if e.headers.get("etag") == new_etag]
        if not hit:
            return []
        newest = max(hit, key=lambda e: e.date_value(0.0))
        newest.headers.update(resp304.headers)
        return [newest]
    lm = resp304.headers.get("last-modified")
    if lm is None and len(entries) == 1 and "etag" not in entries[0].headers:
        entries[0].headers.update(resp304.headers)
        return entries
    hit = [e for e in entries if e.headers.get("last-modified") == lm] if lm else []
    for e in hit:
        e.headers.update(resp304.headers)
    return hit
