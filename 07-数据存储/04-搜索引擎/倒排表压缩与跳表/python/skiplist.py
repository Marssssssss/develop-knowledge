"""Lucene 多级跳表 MultiLevelSkipListWriter / Reader。

忠实转写自 apache/lucene@main
``core/src/java/org/apache/lucene/codecs/MultiLevelSkipListWriter.java`` 与
``MultiLevelSkipListReader.java``。
"""

# Lucene104PostingsFormat 自己的两级跳表（不再走 MultiLevelSkipListWriter）
LEVEL1_FACTOR = 32          # 每 32 个 256-块 打一条 level1 skip datum
LEVEL1_NUM_DOCS = LEVEL1_FACTOR * 256   # = 8192 篇文档
LEVEL1_MASK = LEVEL1_NUM_DOCS - 1


def level1_group_of(doc):
    """文档 doc 落在第几条 level1 skip datum 上。"""
    return doc // LEVEL1_NUM_DOCS


def level1_offset_in_group(doc):
    return doc & LEVEL1_MASK


def log(base, x):
    """MathUtil.log(base, x)：满足 base^ret <= x 的最大 ret。"""
    ret = 0
    cur = 1
    while base * cur <= x:
        cur *= base
        ret += 1
    return ret


def number_of_skip_levels(df, skip_interval, skip_multiplier, max_skip_levels):
    if df > skip_interval:
        return min(1 + log(skip_multiplier, df // skip_interval), max_skip_levels)
    return 1


def buffer_skip_levels(df, skip_interval, skip_multiplier, num_levels):
    """bufferSkip：返回本次 skip datum 会写入的层级列表。"""
    window_length = skip_interval * skip_multiplier
    n = 1
    d = df
    if d % window_length == 0:
        n += 1
        d //= window_length
        while d % skip_multiplier == 0 and n < num_levels:
            n += 1
            d //= skip_multiplier
    return list(range(n))


def entries_per_level(df, skip_interval, skip_multiplier, level):
    """层级 i 的条目数：floor(df / skipInterval^(i+1))（level>0 用 skipMultiplier）。"""
    if level == 0:
        return df // skip_interval
    return df // (skip_interval * (skip_multiplier ** level))


class SkipListWriter(object):
    """模拟 MultiLevelSkipListWriter 的层级分配与落盘顺序。"""

    def __init__(self, df, skip_interval=128, skip_multiplier=8,
                 max_skip_levels=10):
        self.df = df
        self.skip_interval = skip_interval
        self.skip_multiplier = skip_multiplier
        self.max_skip_levels = max_skip_levels
        self.num_levels = number_of_skip_levels(
            df, skip_interval, skip_multiplier, max_skip_levels)
        self.window_length = skip_interval * skip_multiplier
        self.buffers = [[] for _ in range(self.num_levels)]
        self.child_pointers = [[] for _ in range(self.num_levels)]

    def feed(self, docs):
        """docs 为倒排表；在 df % skipInterval == 0 处写 skip datum。"""
        for i, _doc in enumerate(docs, start=1):
            if i % self.skip_interval != 0:
                continue
            levels = buffer_skip_levels(
                i, self.skip_interval, self.skip_multiplier, self.num_levels)
            child_pointer = 0
            for lvl in levels:
                self.buffers[lvl].append(i)
                new_child = len(self.buffers[lvl])
                if lvl != 0:
                    self.child_pointers[lvl].append(child_pointer)
                child_pointer = new_child
        return self

    def level_sizes(self):
        return [len(b) for b in self.buffers]

    def write_order(self):
        """writeSkip：层级自高向低落盘。"""
        return list(range(self.num_levels - 1, -1, -1))


def skip_to(target, level_docs, number_of_skip_levels_):
    """MultiLevelSkipListReader.skipTo：返回落到哪一层（简化为层号 + 该层文档号）。

    level_docs[level] 为该层当前 skip datum 的文档号。
    """
    level = 0
    while level < number_of_skip_levels_ - 1 and target > level_docs[level + 1]:
        level += 1
    return level, level_docs[level]
