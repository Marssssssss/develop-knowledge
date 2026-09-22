"""Cassandra SAI（Storage-Attached Index）的两层索引结构与查询路径。

官方源码口径（apache/cassandra@trunk，本轮实读）：
- `index/sai/README.md`：SAI 是「column based **local** secondary index」；
  字符串在磁盘上用 byte-ordered trie，数值用 block-oriented balanced tree；
  相关 offset 与 token 信息**只在 SSTable 级存一份**（多列索引省空间）；
  索引更新与 mutation 同步，**不需要 read-before-write**；
  全局查询建立在 C* range read 之上；**只支持 token 序**。
- `index/sai/disk/RowMapping.java`：PrimaryKey -> rowID 的映射用 `InMemoryTrie`，
  且只在 `OperationType.FLUSH` 时创建（其他操作一律返回 `DUMMY`）。
- `index/sai/disk/v1/V1OnDiskFormat.java`：两层 writer ——
  `newPerSSTableIndexWriter` 产出 SSTable 级组件，`newPerColumnIndexWriter` 产出列级组件；
  完成标记也分两级：`GROUP_COMPLETION_MARKER`（per-SSTable）与 `COLUMN_COMPLETION_MARKER`（per-column）；
  `newPrimaryKeyMapFactory` 按 `hasClustering()` 选 `WidePrimaryKeyMap` 或 `SkinnyPrimaryKeyMap`。

由此得到本模型的两个核心事实：
1. **rowID 是 SSTable 局部的**，跨 SSTable 合并 postings 必须带 SSTable 标识；
2. 查询结果是 **token 序**，不保证其他顺序。
"""


class PrimaryKey:
    """主键：分区键 + token（SAI 的 PrimaryKeyMap 存的就是这些）。"""

    def __init__(self, pk, token, clustering=None):
        self.pk = pk
        self.token = token
        self.clustering = clustering

    def has_clustering(self):
        return self.clustering is not None

    def __eq__(self, other):
        return isinstance(other, PrimaryKey) and (self.pk, self.clustering) == (other.pk, other.clustering)

    def __hash__(self):
        return hash((self.pk, self.clustering))

    def __repr__(self):
        return "PK(%s, token=%d%s)" % (self.pk, self.token,
                                       ", ck=%s" % self.clustering if self.clustering else "")


class RowMapping:
    """PrimaryKey -> rowID，按 flush 顺序编号；只在 FLUSH 时建立。"""

    def __init__(self):
        self.keys = []          # rowID -> PrimaryKey
        self.index = {}         # PrimaryKey -> rowID

    @classmethod
    def create(cls, op_type):
        """源码：只有 FLUSH 才建真映射，其余返回 DUMMY（add/get 都是空操作）。"""
        return cls() if op_type == "FLUSH" else None

    def add(self, key, row_id=None):
        if row_id is None:
            row_id = len(self.keys)
        assert row_id == len(self.keys), "rowID 必须按 flush 顺序连续分配"
        self.keys.append(key)
        self.index[key] = row_id
        return row_id

    def get(self, key):
        return self.index.get(key, -1)

    def row_id_for(self, key):
        return self.index[key]

    def key_for(self, row_id):
        return self.keys[row_id]


class MemtableIndex:
    """memtable 侧的列索引：term -> PrimaryKey 集合（写路径同步维护，无 read-before-write）。"""

    def __init__(self, column):
        self.column = column
        self.terms = {}

    def add(self, term, key):
        self.terms.setdefault(term, set()).add(key)

    def iterator(self):
        return list(self.terms.items())


class SSTableIndex:
    """一个 SSTable 上的一个列索引：term -> 该 SSTable 局部的 rowID 列表。"""

    def __init__(self, name, row_mapping):
        self.name = name
        self.row_mapping = row_mapping
        self.postings = {}

    @classmethod
    def build(cls, name, memtable_index, keys_in_flush_order):
        """flush：先建 RowMapping（分配 rowID），再把 term -> PK 转成 term -> rowID。"""
        rm = RowMapping.create("FLUSH")
        for k in keys_in_flush_order:
            rm.add(k)
        idx = cls(name, rm)
        for term, keys in memtable_index.iterator():
            ids = sorted(rm.row_id_for(k) for k in keys if rm.get(k) >= 0)
            if ids:
                idx.postings[term] = ids
        return idx

    def search(self, term):
        return self.postings.get(term, [])


def search(sstable_indexes, term):
    """全局查询：合并各 SSTable 的 postings，再映射回 PrimaryKey，按 token 排序。

    注意 postings 里的 rowID 是**局部**的，必须带 SSTable 一起带出去。
    """
    hits = []
    for idx in sstable_indexes:
        for row_id in idx.search(term):
            hits.append((idx.name, row_id, idx.row_mapping.key_for(row_id)))
    hits.sort(key=lambda h: (h[2].token, h[0], h[1]))
    return [h[2] for h in hits]


def naive_search(sstable_indexes, term):
    """最简单的错误写法：把所有 SSTable 的 rowID 混成一个集合再解析。"""
    ids = set()
    for idx in sstable_indexes:
        ids.update(idx.search(term))
    first = sstable_indexes[0]
    return [first.row_mapping.key_for(i) for i in sorted(ids) if i < len(first.row_mapping.keys)]


def on_disk_structure(value):
    """按类型选磁盘结构：字符串走 trie，数值走 block-oriented balanced tree。"""
    if isinstance(value, str):
        return "byte-ordered trie"
    if isinstance(value, bool):
        return "postings only"
    return "block-oriented balanced tree"


def index_components():
    """两层组件与两级完成标记（来自 V1OnDiskFormat）。"""
    return {
        "per_sstable": ["GROUP_COMPLETION_MARKER", "PrimaryKeyMap", "token/offset (只存一份)"],
        "per_column": ["COLUMN_COMPLETION_MARKER", "trie / bbtree", "postings"],
    }
