"""Envoy ratelimit：描述符树匹配 + 分布式配额判定。

权威依据：envoyproxy/ratelimit@main
- src/config/config_impl.go（GetLimit 的匹配顺序、wildcardMatch、unlimited 校验）
- src/limiter/base_limiter.go（GetResponseDescriptorStatus 的阈值判据与 shadow_mode）

配置形如：
    domain: mongo_cps
    descriptors:
      - key: database
        value: users
        rate_limit: {unit: second, requests_per_unit: 10}
      - key: database                      # 无 value ⇒ 默认条目
        rate_limit: {unit: second, requests_per_unit: 100}
      - key: path_/api/*                   # 通配条目
        descriptors:
          - key: remote_address
            rate_limit: {unit: minute, requests_per_unit: 5}
"""

from __future__ import annotations

import math

UNITS = ("unknown", "second", "minute", "hour", "day", "month", "year")


class ConfigError(Exception):
    """对应 Go 侧的 panic(newRateLimitConfigError(...))。"""


class RateLimit:
    """一条限额：requests_per_unit + unit + unlimited / shadow_mode 等开关。"""

    def __init__(self, requests_per_unit: int, unit: str = "second",
                 unlimited: bool = False, shadow_mode: bool = False,
                 name: str = "") -> None:
        self.requests_per_unit = requests_per_unit
        self.unit = unit
        self.unlimited = unlimited
        self.shadow_mode = shadow_mode
        self.name = name
        self.full_key = ""

    @staticmethod
    def from_yaml(spec: dict) -> "RateLimit":
        unlimited = bool(spec.get("unlimited", False))
        unit = str(spec.get("unit", "unknown")).lower()
        valid_unit = unit in UNITS and unit != "unknown"
        if unlimited and valid_unit:
            raise ConfigError("should not specify rate limit unit when unlimited")
        if not unlimited and not valid_unit:
            raise ConfigError("invalid rate limit unit '%s'" % unit)
        return RateLimit(int(spec.get("requests_per_unit", 0)), unit, unlimited,
                         bool(spec.get("shadow_mode", False)), spec.get("name", ""))


class DescriptorNode:
    """配置树的一个节点：finalKey → (limit, 子描述符, 通配条目)。"""

    def __init__(self) -> None:
        self.limit = None
        self.descriptors: dict[str, DescriptorNode] = {}
        self.wildcards: list[tuple[str, list[str]]] = []   # (config key, splitted pattern)


def wildcard_match(parts: list[str], value: str) -> bool:
    """config_impl.go:wildcardMatch 的逐行转写。"""
    if len(parts) == 1:
        return parts[0] == value
    if not value.startswith(parts[0]):
        return False
    if not value.endswith(parts[-1]):
        return False
    total_fixed = sum(len(p) for p in parts)
    if len(value) < total_fixed:
        return False
    remaining = value[len(parts[0]):]
    if parts[-1]:
        remaining = remaining[: len(remaining) - len(parts[-1])]
    for part in parts[1:-1]:
        idx = remaining.find(part)
        if idx < 0:
            return False
        remaining = remaining[idx + len(part):]
    return True


class RateLimitConfig:
    """一个 domain 的配置树 + GetLimit 匹配逻辑。"""

    def __init__(self) -> None:
        self.domains: dict[str, DescriptorNode] = {}

    # --- 配置装载 ---------------------------------------------------------
    def load(self, domain: str, descriptors: list[dict]) -> None:
        root = self.domains.setdefault(domain, DescriptorNode())
        self._load_into(root, descriptors, domain)

    def _load_into(self, node: DescriptorNode, descriptors: list[dict], key_prefix: str) -> None:
        for d in descriptors:
            if not d.get("key"):
                raise ConfigError("descriptor has empty key")
            final_key = d["key"] + ("_" + d["value"] if d.get("value") else "")
            if final_key in node.descriptors:
                raise ConfigError("duplicate descriptor composite key '%s'" % (key_prefix + final_key))
            child = DescriptorNode()
            if "*" in final_key:
                node.wildcards.append((final_key, final_key.split("*")))
            node.descriptors[final_key] = child
            if d.get("rate_limit") is not None:
                child.limit = RateLimit.from_yaml(d["rate_limit"])
            self._load_into(child, d.get("descriptors", []), key_prefix + final_key)

    # --- 匹配 -------------------------------------------------------------
    def get_limit(self, domain: str, entries: list[tuple[str, str]],
                  limit_override: dict | None = None):
        """返回 (RateLimit|None, matched_key)。"""
        root = self.domains.get(domain)
        if root is None:
            return None, None                                   # NotFound 统计 +1

        # Envoy 侧自带 limit override：完全绕过配置树，且不启用 shadow_mode
        if limit_override is not None:
            rl = RateLimit(int(limit_override.get("requests_per_unit", 0)),
                           str(limit_override.get("unit", "second")).lower())
            return rl, "override"

        cur_map, prev = root.descriptors, root
        found = None
        for i, (k, v) in enumerate(entries):
            final_key = k + "_" + v
            nxt = cur_map.get(final_key)
            if nxt is None and prev.wildcards:
                for cfg_key, parts in prev.wildcards:
                    if wildcard_match(parts, final_key):
                        nxt = cur_map.get(cfg_key)
                        break
            if nxt is None:
                final_key = k                                   # 回落到「只按 key」的默认条目
                nxt = cur_map.get(final_key)
            if nxt is None:
                break
            if nxt.limit is not None and i == len(entries) - 1:
                found = nxt.limit                               # 只有深度完全匹配才采用
            if nxt.descriptors:
                cur_map, prev = nxt.descriptors, nxt
            else:
                break
        return found, final_key


class LimitInfo:
    """一次判定的输入：限额、自增前后的计数、hits_addend。"""

    def __init__(self, limit: RateLimit, before: int, addend: int,
                 near_limit_ratio: float = 0.8) -> None:
        self.limit = limit
        self.before = before
        self.after = before + addend
        self.addend = addend
        self.near_limit_ratio = near_limit_ratio

    @property
    def over_limit_threshold(self) -> int:
        return self.limit.requests_per_unit

    @property
    def near_limit_threshold(self) -> int:
        return int(math.floor(self.over_limit_threshold * self.near_limit_ratio))


def get_response_descriptor_status(cache_key: str, info: LimitInfo,
                                   is_over_limit_with_local_cache: bool = False):
    """返回 (code, limit_remaining, stats)。

    code ∈ {"OK", "OVER_LIMIT"}；stats 列出被累加的统计项，条目形如
    "OverLimit(3)" / "NearLimit(1)" / "WithinLimit(1)" / "ShadowMode(5)"。
    """
    stats: list[str] = []
    if cache_key == "":
        return "OK", 0, stats                    # 没配 limit 的 descriptor 恒 OK
    if is_over_limit_with_local_cache:
        stats.append("OverLimit(%d)" % info.addend)
        stats.append("OverLimitWithLocalCache(%d)" % info.addend)
        return "OVER_LIMIT", 0, stats
    threshold = info.over_limit_threshold
    near = info.near_limit_threshold
    if info.after > threshold:                   # 严格大于
        if info.before >= threshold:             # 自增前就已过限 ⇒ 整份 addend 都算过限
            stats.append("OverLimit(%d)" % info.addend)
        else:
            stats.append("OverLimit(%d)" % (info.after - threshold))
            # 自增前没过限 ⇒ 有一段落进了 near 区间
            stats.append("NearLimit(%d)" % (threshold - max(near, info.before)))
        if info.limit.shadow_mode:
            if info.before >= threshold:
                stats.append("ShadowMode(%d)" % info.addend)
            else:
                stats.append("ShadowMode(%d)" % (info.after - threshold))
            return "OK", 0, stats                # shadow 下恒返回 OK
        return "OVER_LIMIT", 0, stats
    if info.after > near:
        if info.before >= near:
            stats.append("NearLimit(%d)" % info.addend)
        else:
            stats.append("NearLimit(%d)" % (info.after - near))
    stats.append("WithinLimit(%d)" % info.addend)
    return "OK", 0, stats


def status_for_negative_hits(limit: RateLimit, addend: int):
    """GetResponseDescriptorStatusForNegativeHits：负数 hits 直接 OK 并计 TotalNegativeHits。"""
    return "OK", 0, ["TotalNegativeHits(%d)" % addend]


def is_unlimited(info: LimitInfo) -> bool:
    """unlimited 的限额在缓存层被跳过（本 demo 只覆盖配置期的校验口径）。"""
    return info.limit.unlimited


# ---- 缓存键 ---------------------------------------------------------------

UNIT_DIVIDER = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def month_start_unix(now_unix: int) -> int:
    """utils.MonthStartUnix：UTC 下当月 1 日 00:00:00 的 Unix 时间戳。"""
    import datetime as _dt
    t = _dt.datetime.fromtimestamp(now_unix, tz=_dt.timezone.utc)
    return int(t.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())


def month_expiration_seconds(now_unix: int) -> int:
    """utils.MonthExpirationSeconds：距当月（UTC）结束还有多少秒。"""
    import datetime as _dt
    t = _dt.datetime.fromtimestamp(now_unix, tz=_dt.timezone.utc)
    nxt = (t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
           + _dt.timedelta(days=32)).replace(day=1)
    return int((nxt - t).total_seconds())


def generate_cache_key(domain: str, entries: list[tuple[str, str]], limit: RateLimit | None,
                       now_unix: int, prefix: str = "",
                       use_calendar_month: bool = True,
                       share_threshold_pattern: list[str] | None = None) -> tuple[str, bool]:
    """CacheKeyGenerator.GenerateCacheKey 的转写。

    返回 (key, per_second)。limit 为 None 时返回空键 —— 空键在
    GetResponseDescriptorStatus 里被短路成「永远 OK」。
    """
    if limit is None:
        return "", False
    parts = [prefix + domain]
    for i, (k, v) in enumerate(entries):
        use = v
        if share_threshold_pattern and i < len(share_threshold_pattern):
            if share_threshold_pattern[i]:
                use = share_threshold_pattern[i]
        parts.append("%s_%s" % (k, use))
    if use_calendar_month and limit.unit == "month":
        bucket_start = month_start_unix(now_unix)
    else:
        divider = UNIT_DIVIDER.get(limit.unit, 1)
        bucket_start = (now_unix // divider) * divider
    return "_".join(parts) + "_" + str(bucket_start), limit.unit == "second"
