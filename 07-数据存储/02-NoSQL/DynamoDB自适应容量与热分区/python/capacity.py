"""DynamoDB 容量单位、分区上限与自适应容量（口径见 README）。

官方事实（docs.aws.amazon.com，本轮实读）：
- 每个分区上限 3000 RCU / 1000 WCU。
- 1 RCU = 每秒 1 次强一致读（item ≤ 4KB），或 2 次最终一致读；1 WCU = 每秒 1 次写（item ≤ 1KB）。
- 官方例子：20KB 的 item 一次强一致读消耗 5 RCU，单分区并发上限变成 600 次/秒。
- Adaptive capacity 官方 Note：「applies to on-demand mode and provisioned capacity」。
  官方**没有公布**它的调度算法，本模型选一种读法（见 README 标注）：
  冷分区未用的配额可以借给热分区，但热分区仍受单分区硬顶约束。
"""

import math

RCU_PER_PARTITION = 3000
WCU_PER_PARTITION = 1000
READ_UNIT_BYTES = 4096
WRITE_UNIT_BYTES = 1024


def wcu_for(item_bytes):
    """WCU：1KB 一档，向上取整（事务写是 2 倍，本模型不覆盖）。"""
    if item_bytes <= 0:
        return 1
    return int(math.ceil(item_bytes / float(WRITE_UNIT_BYTES)))


def rcu_for(item_bytes, consistent=True):
    """RCU：4KB 一档向上取整；最终一致读算半个单位。"""
    if item_bytes <= 0:
        return 0.5 if not consistent else 1
    units = int(math.ceil(item_bytes / float(READ_UNIT_BYTES)))
    return units if consistent else units / 2.0


def max_ops_per_partition(item_bytes, consistent=True):
    """单个分区上，给定 item 大小每秒最多多少次该操作。"""
    per_op = rcu_for(item_bytes, consistent)
    if per_op <= 0:
        return float("inf")
    return RCU_PER_PARTITION / per_op


def write_shard_suffix(order_id, shards=200):
    """官方给出的计算后缀：UTF-8 码点乘积 mod 200 再 +1。"""
    product = 1
    for ch in order_id:
        product *= ord(ch)
    return (product % shards) + 1


def random_suffix(day, n, shards=200):
    """写分片后的 partition key：`<day>.<n>`，n ∈ [1, shards]。"""
    return "%s.%d" % (day, n)


def read_fanout(shards=200):
    """读一天的数据要发多少次 Query（每个后缀一次，然后自己归并）。"""
    return shards


class Table:
    """表级容量与分区配额模型。"""

    def __init__(self, partitions, provisioned_rcu, adaptive=True):
        self.partitions = partitions
        self.provisioned = provisioned_rcu
        self.adaptive = adaptive

    def per_partition_quota(self):
        return self.provisioned / float(self.partitions)

    def serve(self, loads):
        """返回 (每分区实际服务量, 被限流的量)。

        无 adaptive：每分区只能用均分的配额，用不完就浪费。
        有 adaptive：冷分区的剩余可以借给热分区，但热分区仍受单分区硬顶约束。
        """
        quota = self.per_partition_quota()
        # 第一刀：每分区先按「均配额」与「单分区硬顶」的较小值分，硬顶无论如何都生效
        served = [min(q, quota, RCU_PER_PARTITION) for q in loads]
        if not self.adaptive:
            return served, sum(loads) - sum(served)

        # 第二刀：冷分区没用完的配额进池子，借给还没吃饱的分区（仍受硬顶约束）
        pool = sum(quota - s for s in served)
        for i, q in enumerate(loads):
            if served[i] < q and served[i] < RCU_PER_PARTITION:
                want = min(q - served[i], RCU_PER_PARTITION - served[i])
                give = min(want, pool)
                served[i] += give
                pool -= give
        return served, sum(loads) - sum(served)


def is_hot(loads, threshold=0.5):
    """热分区判据：某个分区吃到的负载超过按均匀分配的 1+threshold 倍。"""
    if not loads:
        return []
    fair = sum(loads) / float(len(loads))
    return [i for i, q in enumerate(loads) if q > fair * (1 + threshold)]
