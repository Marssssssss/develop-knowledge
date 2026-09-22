"""MongoDB 分片集群的 chunk 分裂、均衡阈值与 range 迁移流程（口径见 README）。

官方事实（mongodb.com/docs 手动版，本轮实读）：
- 默认 range size = 128MB。
- 均衡阈值：一个集合被认为已均衡，当且仅当 shard 之间数据量差 < 3 × range size；
  默认 128MB 时差至少 384MB 才会触发迁移。
- 一个 shard 同时最多参与一次迁移；n 个 shard 最多 floor(n/2) 个并发迁移。
- range 迁移 7 步：moveRange -> 源分片继续承接写 -> 目标建索引 -> 拉数据 ->
  同步迁移期间的增量 -> 更新 config 元数据 -> 源端删除（等无游标后）。
- 删除阶段是异步的：不等它完成就可以开始下一次迁移。
- jumbo chunk = 超过配置 chunk size 且无法自动分裂；单个唯一 shard key 值的 chunk 不可分。
"""

DEFAULT_CHUNK_MB = 128


def migration_threshold(chunk_size=DEFAULT_CHUNK_MB):
    """触发均衡所需的最小数据量差：3 × range size。"""
    return 3 * chunk_size


def needs_balancing(sizes, chunk_size=DEFAULT_CHUNK_MB):
    """是否达到迁移阈值（严格「差值 ≥ 3 倍 range size」）。"""
    if len(sizes) < 2:
        return False
    return max(sizes) - min(sizes) >= migration_threshold(chunk_size)


def max_parallel_migrations(n_shards):
    """n 个 shard 最多 floor(n/2) 个并发迁移。"""
    return n_shards // 2


def split_plan(size_mb, chunk_size=DEFAULT_CHUNK_MB):
    """自动分裂：超过 chunk_size 就在中点二分，直到每块都不超。

    返回 (分裂次数, 最终块数, 每块大小)。注意是二分，所以块数是 2 的幂。
    """
    if size_mb <= chunk_size:
        return 0, 1, float(size_mb)
    splits = 0
    parts = 1
    while size_mb / float(parts) > chunk_size:
        parts *= 2
        splits += 1
    return splits, parts, size_mb / float(parts)


class Chunk:
    """一个 chunk：下限（含）到上限（不含），以及它含多少个唯一 shard key 值。"""

    def __init__(self, low, high, size_mb, distinct_keys):
        self.low = low
        self.high = high
        self.size_mb = size_mb
        self.distinct_keys = distinct_keys
        self.jumbo = False

    @property
    def divisible(self):
        """可分裂 = 含多个唯一 shard key 值。"""
        return self.distinct_keys > 1

    def evaluate(self, chunk_size=DEFAULT_CHUNK_MB):
        """判定是否被标记为 jumbo：超限**且**不可自动分裂。"""
        self.jumbo = self.size_mb > chunk_size and not self.divisible
        return self.jumbo

    def __repr__(self):
        return "Chunk[%s,%s) %dMB keys=%d jumbo=%s" % (
            self.low, self.high, self.size_mb, self.distinct_keys, self.jumbo)


class Migration:
    """range 迁移状态机：7 个步骤，删除阶段可以异步。"""

    STEPS = [
        "moveRange 下发到源分片",
        "源分片继续承接该 range 的写",
        "目标分片补齐索引",
        "目标分片拉取文档",
        "同步迁移期间的增量",
        "源分片更新 config 元数据",
        "源分片删除自己的副本（等无游标后）",
    ]

    def __init__(self, name, async_delete=True):
        self.name = name
        self.async_delete = async_delete
        self.step = 0
        self.done = False
        self.deleted = False

    def advance(self):
        """推进一步；异步模式下第 6 步完成后就算 done，删除可以后面再做。"""
        if self.done:
            if not self.deleted:
                self.deleted = True
            return self.deleted
        self.step += 1
        # 异步模式：第 6 步（更新 config 元数据）完成即算 done，删除排队去做
        if self.async_delete and self.step >= len(self.STEPS) - 1:
            self.done = True
            self.deleted = False
        elif self.step >= len(self.STEPS):
            self.done = True
            self.deleted = True
        return False


def balance_round(sizes, chunk_size=DEFAULT_CHUNK_MB):
    """一轮均衡：在阈值内反复从最多的 shard 往最少的 shard 搬一个 chunk。

    返回 (轮次列表, 迁移次数)。本模型按「每次搬 chunk_size 大小的数据」近似，
    官方实际是按 chunk 数/数据量挑 chunk，不是按固定块大小。
    """
    cur = list(sizes)
    rounds = []
    moves = 0
    while needs_balancing(cur, chunk_size) and moves < 1000:
        hi = cur.index(max(cur))
        lo = cur.index(min(cur))
        amount = min(cur[hi] - cur[lo], chunk_size * 2) // 2
        if amount <= 0:
            break
        cur[hi] -= amount
        cur[lo] += amount
        moves += 1
        rounds.append(list(cur))
    return rounds, moves
