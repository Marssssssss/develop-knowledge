"""HTTP 缓存核心语义（RFC 9111 §3/§4 + RFC 5861）。

纯标准库实现，仅建模「判定」部分，不涉及网络与存储。
所有函数的时间戳单位统一为「秒」（unix epoch，整数），便于断言。
"""

# ---------------------------------------------------------------- 指令解析

def split_commas(value):
    """按逗号切分但**不切开引号内的逗号**。

    `no-cache="Set-Cookie,ETag"` 是一个指令而不是两个 —— 朴素 `split(",")`
    会把它拆成 `no-cache="Set-Cookie` 与 `ETag"`，后者被当成未知指令丢掉、
    前者连引号都不闭合。这是手写解析器的经典坑。
    """
    parts, cur, in_q = [], [], False
    for ch in value:
        if ch == '"':
            in_q = not in_q
            cur.append(ch)
        elif ch == "," and not in_q:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def parse_cache_control(value):
    """把 Cache-Control 值解析为 {directive: arg}。

    RFC 9111 §5.2 要求忽略未知指令；重复指令取第一次出现（§4.2.1 建议）。
    """
    out = {}
    if not value:
        return out
    for part in split_commas(value):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            name, arg = part.split("=", 1)
            name = name.strip().lower()
            arg = arg.strip()
            # RFC 9111 §5.2：允许 max-age=5 与 max-age="5" 两种形式
            if len(arg) >= 2 and arg[0] == '"' and arg[-1] == '"':
                arg = arg[1:-1]
            out[name] = arg
        else:
            out[part.lower()] = None
    return out


def delta_seconds(raw):
    """delta-seconds 解析。

    RFC 9111 §1.2.2：非法值（非整数）应使响应被视为陈旧（stale）。
    这里返回 None 表示该语义，交由上层处理。
    """
    if raw is None:
        return None
    raw = raw.strip()
    if not raw.isdigit():
        return None
    v = int(raw)
    # §1.2.2：大于 2147483648 (2^31) 的 delta-seconds 应视为 2147483648
    return min(v, 2147483648)


# ---------------------------------------------------------------- 新鲜度

def freshness_lifetime(headers, shared, last_modified=None, now=None,
                       heuristic_fraction=0.10):
    """RFC 9111 §4.2.1 —— 按顺序取第一个匹配。

    返回 (lifetime, source)。source 用于断言「谁赢了」。
    """
    cc = parse_cache_control(headers.get("cache-control", ""))
    # 共享缓存才看 s-maxage，私有缓存必须忽略它
    if shared:
        v = delta_seconds(cc.get("s-maxage"))
        if v is not None:
            return v, "s-maxage"
    v = delta_seconds(cc.get("max-age"))
    if v is not None:
        return v, "max-age"
    if "expires" in headers:
        expires = headers["expires"]
        # §4.2.1：Expires 减去 Date；没有 Date 就用「收到响应的时刻」
        date_value = headers.get("date", now if now is not None else expires)
        return expires - date_value, "expires-date"
    # §4.2.2：没有显式过期时间才允许启发式
    if last_modified is not None and now is not None:
        age_since_lm = now - last_modified
        if age_since_lm > 0:
            return int(age_since_lm * heuristic_fraction), "heuristic"
    return 0, "none"


def current_age(age_value, date_value, request_time, response_time, now,
                conservative=True):
    """RFC 9111 §4.2.3 —— 两种独立算法 + 可选保守合成。

    保守合成（推荐）在有老旧缓存实现时使用：corrected_initial_age = max(apparent, corrected)。
    """
    apparent_age = max(0, response_time - date_value)
    response_delay = response_time - request_time
    corrected_age_value = (age_value or 0) + response_delay
    if conservative:
        corrected_initial_age = max(apparent_age, corrected_age_value)
    else:
        corrected_initial_age = corrected_age_value
    resident_time = now - response_time
    return corrected_initial_age + resident_time


def response_is_fresh(lifetime, age):
    """RFC 9111 §4.2 —— `response_is_fresh = (freshness_lifetime > current_age)`。

    注意是**严格大于**：age 恰好等于 lifetime 时已经陈旧。
    """
    return lifetime > age


# ---------------------------------------------------------------- 可存储性

def is_storable(status, headers, shared):
    """RFC 9111 §3 —— 是否允许存入缓存。返回 (bool, reason)。"""
    cc = parse_cache_control(headers.get("cache-control", ""))
    if "no-store" in cc:
        return False, "no-store"
    if shared and "private" in cc:
        return False, "private"
    if status in (200, 203, 204, 206, 300, 301, 308, 404, 405, 410, 414, 501):
        return True, "heuristically-cacheable"
    if "public" in cc:
        return True, "public"
    if delta_seconds(cc.get("max-age")) is not None or "expires" in headers:
        return True, "explicit-freshness"
    return False, "no-explicit-and-not-heuristic"


def may_reuse_without_validation(headers, shared, disconnected=False):
    """陈旧响应能否被复用。返回 (bool, reason)。"""
    cc = parse_cache_control(headers.get("cache-control", ""))
    if "no-cache" in cc and cc["no-cache"] is None:
        return False, "no-cache(unqualified)"
    if "must-revalidate" in cc:
        # §5.2.2.2：断网时必须产生错误响应（504），而不是复用陈旧响应
        return (False, "must-revalidate->504" if disconnected
                else "must-revalidate")
    if shared and "proxy-revalidate" in cc:
        return False, "proxy-revalidate"
    if shared and delta_seconds(cc.get("s-maxage")) is not None:
        return False, "s-maxage"
    return True, "allowed"


def error_status_for_stale_must_revalidate():
    """§5.2.2.2：断网 + must-revalidate → SHOULD 504。"""
    return 504


# ---------------------------------------------------------------- RFC 5861

STALE_IF_ERROR_STATUSES = (500, 502, 503, 504)


def stale_window(lifetime, age, swr):
    """RFC 5861 §3 —— 返回 'fresh' / 'swr' / 'stale'。

    - fresh : age <  lifetime
    - swr   : lifetime <= age < lifetime + swr   （可立即返回陈旧 + 后台校验）
    - stale : age >= lifetime + swr              （必须阻塞式校验）
    """
    if age < lifetime:
        return "fresh"
    if swr is not None and age < lifetime + swr:
        return "swr"
    return "stale"


def may_serve_stale_if_error(age, lifetime, sie):
    """RFC 5861 §4 —— 出错时可用陈旧响应的**上限**是 lifetime + sie。"""
    if sie is None:
        return False
    return age < lifetime + sie


# ---------------------------------------------------------------- Vary

def vary_match(vary, stored_req_headers, new_req_headers):
    """RFC 9111 §4.1 —— Vary 列出的头必须完全匹配（缺失只能匹配缺失）。"""
    if vary is None:
        return True
    for field in (f.strip().lower() for f in vary.split(",")):
        if field == "*":
            return False  # §4.1：含 "*" 永远不匹配
        stored = stored_req_headers.get(field)
        new = new_req_headers.get(field)
        # 「一个请求里缺失的头只能匹配另一个请求里也缺失」
        if (stored is None) != (new is None):
            return False
        if stored != new:
            return False
    return True


def secondary_cache_key(uri, vary, req_headers):
    """二级缓存键 = URI + Vary 指定头的值（按字段名排序以保证稳定）。"""
    if not vary:
        return (uri, ())
    fields = sorted(f.strip().lower() for f in vary.split(","))
    return (uri, tuple((f, req_headers.get(f)) for f in fields))
