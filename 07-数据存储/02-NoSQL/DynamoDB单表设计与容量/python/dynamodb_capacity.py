"""
dynamodb_capacity.py — DynamoDB 容量单位换算、单分区上限与写分片算术

权威来源(全部实际读过):
  - AWS: Best practices for designing and using partition keys effectively
    https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-design.html
    原文要点:
      * "Every partition in a DynamoDB table is designed to deliver a maximum
         capacity of 3,000 read units per second and 1,000 write units per second."
      * "One read unit represents one strongly consistent read operation per second,
         or two eventually consistent read operations per second, for an item up to
         4 KB in size. One write unit represents one write operation per second for
         an item up to 1 KB in size."
      * "if the table has an item size of 20 KB, a single consistent read operation
         will consume 5 read units ... you can concurrently drive 600 consistent read
         operations per second on that single item before reaching the partition limits."
  - AWS: Using write sharding to distribute workloads evenly
    https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-sharding.html
      * 官方示例:日期分区键拼接 1..200 的随机后缀 → 写入摊到多个分区;
        "to read all the items for a given day, you would have to query the items for
         all the suffixes and then merge the results."
  - AWS: DynamoDB read/write operations(单位耗用示例)
  - AWS: DynamoDB pricing(强一致/最终一致/事务读的 1/0.5/2 倍关系)
  - AWS: DynamoDB Cheat Sheet(400 KB 项目上限、键长度、1 MB 页大小)

运行自检: python3 dynamodb_check.py
"""
from __future__ import annotations

KB = 1024
MB = 1024 * KB

READ_UNIT_BYTES = 4 * KB        # 读单位按 4 KB 计
WRITE_UNIT_BYTES = 1 * KB       # 写单位按 1 KB 计

PER_PARTITION_RCU = 3000        # 官方:每分区 3000 读单位/秒
PER_PARTITION_WCU = 1000        # 官方:每分区 1000 写单位/秒

MAX_ITEM_BYTES = 400 * KB       # 官方条目大小上限,含属性名
MAX_PARTITION_KEY_BYTES = 2048
MAX_SORT_KEY_BYTES = 1024
MAX_PAGE_BYTES = 1 * MB         # Query/Scan 单页 1 MB

# 读一致性倍数:强一致 1,最终一致 0.5,事务读 2(官方定价页三种口径)
CONSISTENCY_FACTOR = {"strong": 1.0, "eventual": 0.5, "transactional": 2.0}
PARTITION_SIZE_BYTES = 10 * 1024 * MB   # 单个分区承载能力 10 GB(见 README 口径说明)


def ceil_div(n: int, d: int) -> int:
    return -(-int(n) // int(d))


# ---------------------------------------------------------------------
# 单位换算
# ---------------------------------------------------------------------
def read_units(item_bytes: int, consistency: str = "strong") -> float:
    """一次读请求消耗的读单位。按 4 KB 向上取整后再乘一致性倍数。"""
    if consistency not in CONSISTENCY_FACTOR:
        raise ValueError("unknown consistency: %r" % consistency)
    return ceil_div(item_bytes, READ_UNIT_BYTES) * CONSISTENCY_FACTOR[consistency]


def write_units(item_bytes: int, transactional: bool = False) -> int:
    """一次写请求消耗的写单位。按 1 KB 向上取整;事务写 ×2。"""
    return ceil_div(item_bytes, WRITE_UNIT_BYTES) * (2 if transactional else 1)


def batch_get_units(sizes, consistency: str = "strong") -> float:
    """BatchGetItem:**每个条目各自**向上取整到 4 KB 后再相加。

    官方示例:1.5 KB + 6.5 KB 两项算成 12 KB(4 KB + 8 KB),而不是 8 KB。
    """
    total = sum(ceil_div(s, READ_UNIT_BYTES) * READ_UNIT_BYTES for s in sizes)
    return read_units(total, consistency)


def query_units(total_bytes: int, consistency: str = "strong") -> float:
    """Query/Scan:把**返回集合的总大小**一次性向上取整到 4 KB(不是逐条取整)。"""
    return read_units(total_bytes, consistency)


def query_page(total_bytes: int) -> dict:
    """1 MB 分页:超过则返回 LastEvaluatedKey 供下一页续读。"""
    if total_bytes <= MAX_PAGE_BYTES:
        return {"pages": 1, "last_evaluated_key": None}
    pages = ceil_div(total_bytes, MAX_PAGE_BYTES)
    return {"pages": pages, "last_evaluated_key": "present"}


# ---------------------------------------------------------------------
# 单分区可持续速率
# ---------------------------------------------------------------------
def sustained_reads_per_partition(item_bytes: int, consistency: str = "strong") -> float:
    """单个分区（单个分区键值）每秒可承受的读请求数。"""
    return PER_PARTITION_RCU / read_units(item_bytes, consistency)


def sustained_writes_per_partition(item_bytes: int, transactional: bool = False) -> float:
    return PER_PARTITION_WCU / write_units(item_bytes, transactional)


def partitions_for_size(total_bytes: int) -> int:
    """按分区承载 10 GB 估算分区数(官方 LSI 文档:"each item collection is stored
    in one partition. The total size of such an item collection is limited to the
    capability of that partition: 10 GB")。"""
    return max(1, ceil_div(total_bytes, PARTITION_SIZE_BYTES))


# ---------------------------------------------------------------------
# 写分片
# ---------------------------------------------------------------------
def shard_count(target_wcu: int, per_partition: int = PER_PARTITION_WCU) -> int:
    """把一个逻辑分区键的写负载摊到 N 个分区所需的分片数。

    推导(非官方原文):每个分区写上限 per_partition,故 N = ceil(target / 上限)。
    官方 sharding 页给出的示例后缀范围是 1..200,本函数算出的 N 与之一致时即可解释
    该示例(见自检)。
    """
    return max(1, ceil_div(target_wcu, per_partition))


def sharded_read_queries(shards: int) -> int:
    """分片后的读代价:必须对每个后缀各发一次 Query 再合并(官方 sharding 页原文)。"""
    return max(1, shards)


# ---------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------
def validate_item_size(item_bytes: int) -> str:
    if item_bytes > MAX_ITEM_BYTES:
        return "ItemSizeTooLarge: %d > %d" % (item_bytes, MAX_ITEM_BYTES)
    return ""


def validate_keys(pk_bytes: int, sk_bytes: int = 0) -> list:
    """官方:分区键 1~2048 字节;排序键 1~1024 字节。"""
    problems = []
    if not 1 <= pk_bytes <= MAX_PARTITION_KEY_BYTES:
        problems.append("partition key length %d not in 1..%d" % (pk_bytes, MAX_PARTITION_KEY_BYTES))
    if sk_bytes and not 1 <= sk_bytes <= MAX_SORT_KEY_BYTES:
        problems.append("sort key length %d not in 1..%d" % (sk_bytes, MAX_SORT_KEY_BYTES))
    return problems


def hot_partition(ops_per_second: int, limit: int) -> bool:
    """单个分区键值上的吞吐超过分区上限 → 该键被限流(自适应容量抬不动这个硬顶)。"""
    return ops_per_second > limit
