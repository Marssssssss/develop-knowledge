"""多租户与 limits_config：默认值、按租户覆盖、分发器限流均摊。

所有默认值都取自官方配置参考手册（页面逐项标注 `default = ...`），而不是
凭印象填。几处文档版本分歧在 README 里单独列出，代码里不把分歧值写进断言。

最反直觉的一条在 `distributor_rate_mb()`：**全局策略下，配置的
`ingestion_rate_mb` 会被均摊到每个 distributor 实例上**，而
`ingestion_burst_size_mb` 不会。官方原文：

  - global: 「configuring a per-distributor local rate limiter as
    `ingestion_rate / N`, where N is the number of distributor replicas」
  - burst: 「The burst size refers to the per-distributor local rate limiter
    even in the case of the "global" strategy」

所以扩容 distributor 会**降低**每实例阈值，而滚动重启导致实例数下降会
**抬高**每实例阈值 —— 这是"429 风暴常与滚动重启同时发生"的成因。
"""

from __future__ import annotations

from dataclasses import dataclass

from logql_ast import parse_bytes

__all__ = [
    "MIB",
    "DEFAULTS",
    "resolve_limits",
    "distributor_rate_mb",
    "distributor_burst_mb",
    "cluster_effective_rate_mb",
    "check_line",
    "check_labels",
    "StreamRegistry",
    "Decision",
]

# Loki 的字节/速率单位是 1024 进制（见 logql_ast.BYTE_UNITS 的注释依据）。
MIB = 1024 * 1024

# limits_config 默认值。来源：官方配置参考手册 limits_config 块 +
# v3.5 排障手册（max_global_streams_per_user / max_line_size）。
DEFAULTS: dict[str, object] = {
    "ingestion_rate_strategy": "global",
    "ingestion_rate_mb": 4.0,
    "ingestion_burst_size_mb": 6.0,
    "per_stream_rate_limit": "3MB",
    "per_stream_rate_limit_burst": "15MB",
    "max_line_size": "256KB",
    "max_line_size_truncate": False,
    "max_label_name_length": 1024,
    "max_label_value_length": 2048,
    "max_label_names_per_series": 30,
    "max_streams_per_user": 0,  # 0 = 该 ingester 上不限
    "max_global_streams_per_user": 5000,
    "reject_old_samples": True,
    "reject_old_samples_max_age": "168h",
    "creation_grace_period": "10m",
    "max_entries_limit_per_query": 5000,
    "max_query_length": "721h",
    "max_query_series": 500,
    "unordered_writes": True,
    "chunk_idle_period": "30m",  # 活跃流的判定窗口，见 StreamRegistry
}


def resolve_limits(
    global_limits: dict | None = None,
    overrides: dict[str, dict] | None = None,
    tenant: str = "fake",
) -> dict:
    """global 默认 ← overrides[tenant] 逐键覆盖。

    纯函数：返回新字典，不修改 global_limits。生产上这份 overrides 由
    `per_tenant_override_config` 指向的文件提供，每 `per_tenant_override_period`
    （默认 10s）热加载一次，无需重启组件。
    """
    merged = dict(DEFAULTS)
    if global_limits:
        merged.update(global_limits)
    if overrides and tenant in overrides:
        merged.update(overrides[tenant])
    return merged


def distributor_rate_mb(limit_mb: float, n_distributors: int, strategy: str = "global") -> float:
    """单个 distributor 实例实际执行的本地限流阈值（MB/s）。

    global：把租户级限额均分成 N 份（N = ring 里健康实例数，随扩缩容自动重算）。
    local ：每实例各自执行整份额度，集群总有效额度被放大成 N 倍。
    """
    if n_distributors < 1:
        raise ValueError("distributor 数量必须 >= 1")
    if strategy == "local":
        return float(limit_mb)
    if strategy == "global":
        return float(limit_mb) / float(n_distributors)
    raise ValueError(f"未知的 ingestion_rate_strategy: {strategy!r}")


def distributor_burst_mb(burst_mb: float, n_distributors: int, strategy: str = "global") -> float:
    """burst **不随 distributor 数量变化**（官方原文明确说明）。

    参数 n_distributors / strategy 保留是为了让调用点显式写出"这里做过均摊
    判断"，避免读者误以为忘了除。
    """
    del n_distributors, strategy
    return float(burst_mb)


def cluster_effective_rate_mb(limit_mb: float, n_distributors: int, strategy: str = "global") -> float:
    """集群口径的**实际**总限额。local 策略下是配置值的 N 倍。"""
    return distributor_rate_mb(limit_mb, n_distributors, strategy) * n_distributors


def check_line(limits: dict, line_bytes: int) -> str:
    """行长校验。返回 ok | truncated | rejected。

    max_line_size 为 0（或未设）表示不限。官方强烈反对调大这个值：
    它直接影响查询稳定性，调大意味着要么放宽 gRPC 消息上限、要么吃更多内存。
    """
    cap = parse_bytes(str(limits.get("max_line_size") or "0b"))
    if cap <= 0 or line_bytes <= cap:
        return "ok"
    return "truncated" if limits.get("max_line_size_truncate") else "rejected"


def check_labels(limits: dict, labels: dict[str, str]) -> str:
    """标签数量与名/值长度校验。标签数量是防标签爆炸（cardinality）的第一道闸。"""
    if len(labels) > int(limits["max_label_names_per_series"]):
        return "too_many_labels"
    for name, value in labels.items():
        if len(name) > int(limits["max_label_name_length"]):
            return "label_name_too_long"
        if len(value) > int(limits["max_label_value_length"]):
            return "label_value_too_long"
    return "ok"


@dataclass
class Decision:
    status: int
    reason: str

    @property
    def accepted(self) -> bool:
        return self.status < 400


class StreamRegistry:
    """按租户跟踪活跃流，执行 max_global_streams_per_user。

    「活跃」的判定窗口就是 `chunk_idle_period`（默认 30 分钟）—— 流在这个
    窗口内收到过日志才算活跃。这个耦合很容易被忽略：调大 chunk_idle_period
    会同时拉长流的活跃期，从而**提前撞上**流数量上限。
    """

    def __init__(self, idle_period_s: float = 1800.0) -> None:
        self.idle_period_s = idle_period_s
        self._last_seen: dict[tuple[str, tuple], int] = {}

    def touch(self, tenant: str, key: tuple, ts_ns: int) -> None:
        self._last_seen[(tenant, key)] = ts_ns

    def active_count(self, tenant: str, now_ns: int) -> int:
        window_ns = int(self.idle_period_s * 1e9)
        return sum(
            1
            for (t, _k), ts in self._last_seen.items()
            if t == tenant and now_ns - ts <= window_ns
        )

    def admit(self, tenant: str, key: tuple, ts_ns: int, limits: dict, now_ns: int) -> Decision:
        """新流准入判定。已存在的流直接放行，不重复计入。"""
        known = (tenant, key) in self._last_seen
        if known:
            return Decision(200, "existing_stream")
        cap = int(limits.get("max_global_streams_per_user") or 0)
        if cap and self.active_count(tenant, now_ns) >= cap:
            return Decision(429, "stream_limit")
        return Decision(200, "new_stream")
