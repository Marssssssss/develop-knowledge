"""存储 schema 与索引布局。

官方口径（Storage schema 文档）：
  * 新装实例推荐且**事实上强制**的组合是 `store: tsdb` + `schema: v13`。
  * 理由是 structured metadata 与原生 OTLP 摄入默认开启
    （`allow_structured_metadata: true`），而这两项特性要求当前 schema 段
    使用 tsdb 索引与 v13 及以上版本；不满足时 Loki **拒绝启动**并抛出
    `CONFIG ERROR: schema v13 is required ...` / `CONFIG ERROR: tsdb index
    type is required ...`。
  * `boltdb-shipper` 已弃用，将在 4.0 移除；它也不支持 structured metadata。
  * tsdb 的 `index.period` 必须是 24h；`row_shards` 自 v10 起默认 16。
  * 新增 schema 段时 `from` 必须是**未来**日期（老装升级），而全新安装必须
    是**过去**日期 —— 两者方向相反，写错会导致数据不可读或无法启动。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SCHEMA_DEFAULTS",
    "DEPRECATED_STORES",
    "REMOVED_IN_V4",
    "TSDB_INDEX_PERIOD",
    "schema_version_number",
    "schema_check",
    "validate_index_period",
    "validate_from_date",
]

SCHEMA_DEFAULTS: dict[str, object] = {
    "store": "tsdb",
    "schema": "v13",
    "object_store": "s3",
    "index_prefix": "index_",
    "index_period": "24h",
    "row_shards": 16,  # v10 起的默认值，通常不需要改
}

DEPRECATED_STORES = frozenset({"boltdb", "boltdb-shipper", "cassandra", "bigtable", "dynamodb"})
REMOVED_IN_V4 = frozenset({"boltdb-shipper"})
TSDB_INDEX_PERIOD = "24h"


def schema_version_number(schema: str) -> int:
    """`v13` → 13。无法解析时抛错，避免字符串比较把 v9 判成大于 v13。"""
    text = schema.strip().lower().lstrip("v")
    if not text.isdigit():
        raise ValueError(f"非法的 schema 版本: {schema!r}")
    return int(text)


def schema_check(
    store: str,
    schema: str,
    *,
    allow_structured_metadata: bool = True,
    native_otlp: bool = False,
) -> str | None:
    """模拟启动期校验，返回错误串；None 表示可启动。

    这就是「为什么老教程的 boltdb-shipper 配置升级后会起不来」的机器可读版本。
    """
    needs_modern = allow_structured_metadata or native_otlp
    if store in REMOVED_IN_V4:
        return f"CONFIG ERROR: {store} 已在 4.0 中移除，请迁移到 tsdb"
    if needs_modern and store != "tsdb":
        return (
            "CONFIG ERROR: `tsdb` index type is required to store Structured "
            "Metadata and use native OTLP ingestion..."
        )
    if needs_modern and schema_version_number(schema) < 13:
        return (
            "CONFIG ERROR: schema v13 is required to store Structured Metadata "
            "and use native OTLP ingestion..."
        )
    return None


def validate_index_period(store: str, period: str) -> bool:
    """tsdb 的索引 period 必须恰好是 24h（官方配置说明里的 must）。"""
    if store != "tsdb":
        return True
    return period == TSDB_INDEX_PERIOD


@dataclass(frozen=True)
class FromDateCheck:
    ok: bool
    reason: str


def validate_from_date(is_new_install: bool, from_date: str, today: str) -> FromDateCheck:
    """`from` 日期方向校验。

    全新安装：`from` 必须在过去（否则当前时刻没有生效的 schema，无法写入）。
    老装升级：`from` 必须在未来（否则切换瞬间前后的数据会用错 schema，老数据
    变得不可读，而这**不可回滚**）。
    """
    if is_new_install:
        if from_date < today:
            return FromDateCheck(True, "ok")
        return FromDateCheck(False, "全新安装的 from 必须是过去的日期，否则当前无可用 schema")
    if from_date > today:
        return FromDateCheck(True, "ok")
    return FromDateCheck(False, "追加 schema 段时 from 必须是未来日期，否则切换前的数据不可读")
