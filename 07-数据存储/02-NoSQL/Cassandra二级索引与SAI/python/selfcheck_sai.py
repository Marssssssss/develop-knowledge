"""Cassandra SAI 模型自检（期望值全部手算，见注释）。"""

import sys

from sai import (
    MemtableIndex,
    PrimaryKey,
    RowMapping,
    SSTableIndex,
    index_components,
    naive_search,
    on_disk_structure,
    search,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAILED: " + label
    PASS += 1


def eq(a, b, label):
    ok(a == b, "%s (got %r want %r)" % (label, a, b))


# ---- 1. RowMapping 只在 FLUSH 时建立 ----
eq(RowMapping.create("FLUSH") is not None, True, "FLUSH 时建真映射")
for op in ("COMPACTION", "STREAM", "BUILD"):
    eq(RowMapping.create(op), None, "%s 时返回 DUMMY（不建映射）" % op)

rm = RowMapping()
k1 = PrimaryKey("pk1", 10)
k2 = PrimaryKey("pk2", 20)
eq(rm.add(k1), 0, "第一个 key 拿到 rowID 0")
eq(rm.add(k2), 1, "第二个 key 拿到 rowID 1")
eq(rm.get(k1), 0, "反查 pk1 -> 0")
eq(rm.get(PrimaryKey("nope", 99)), -1, "查不到的 key 返回 -1（与 DUMMY 一致）")

# ---- 2. 两个 SSTable 各有自己的 rowID 空间 ----
# SSTable A: pk_a0(token 40), pk_a1(token 20)  -> rowID 0, 1
# SSTable B: pk_b0(token 5),  pk_b1(token 30)  -> rowID 0, 1
ka0 = PrimaryKey("pk_a0", 40)
ka1 = PrimaryKey("pk_a1", 20)
kb0 = PrimaryKey("pk_b0", 5)
kb1 = PrimaryKey("pk_b1", 30)

def build_pair():
    m1 = MemtableIndex("name")
    m1.add("John", ka1)
    a = SSTableIndex.build("A", m1, [ka0, ka1])
    m2 = MemtableIndex("name")
    m2.add("John", kb0)        # B 里 John 落在 rowID 0
    b = SSTableIndex.build("B", m2, [kb0, kb1])
    return a, b


A, B = build_pair()
eq(A.search("John"), [1], "A 中 John 的 postings 是 rowID 1")
eq(B.search("John"), [0], "B 中 John 的 postings 是 rowID 0")
# 同一个 rowID 在两个 SSTable 里指向完全不同的 key
ok(A.row_mapping.key_for(1) is not B.row_mapping.key_for(1), "rowID 1 在 A/B 中指向不同 key")
eq(A.row_mapping.key_for(1), ka1, "A 的 rowID 1 = pk_a1")
eq(B.row_mapping.key_for(1), kb1, "B 的 rowID 1 = pk_b1")

# ---- 3. 全局查询：必须带 SSTable 标识合并，结果按 token 序 ----
res = search([A, B], "John")
eq(res, [kb0, ka1], "命中 pk_b0(token 5) 与 pk_a1(token 20)，按 token 升序")
eq([r.token for r in res], [5, 20], "结果确实是 token 序")
# 换个顺序构造，结果仍然一样（token 序与 SSTable 顺序无关）
eq(search([B, A], "John"), [kb0, ka1], "交换 SSTable 顺序不影响结果")

# ---- 4. rowID 是局部的：错误实现会张冠李戴 ----
bad = naive_search([A, B], "John")
# 错误实现把 {0,1} 都拿去 A 的映射里解析 -> pk_a0, pk_a1
eq(bad, [ka0, ka1], "错误实现解析出 A 的 0/1 两行")
ok(bad != res, "错误实现的结果与正确结果不同（本该失败）")
# 正确实现命中的两个 key 分别来自不同 SSTable
ok(any(r is kb0 for r in res) and any(r is ka1 for r in res), "正确结果跨两个 SSTable")
ok(not any(r is ka0 for r in res), "未命中的 pk_a0 不应出现在结果里")

# ---- 5. 多个 term 与多列索引共享 SSTable 级信息 ----
m = MemtableIndex("age")
m.add(21, ka1)
m.add(50, ka0)
age_idx = SSTableIndex.build("A", m, [ka0, ka1])
eq(age_idx.search(21), [1], "age=21 命中 rowID 1")
eq(age_idx.search(50), [0], "age=50 命中 rowID 0")
# 两列索引共用同一个 SSTable 的 rowID 空间：这就是「offset/token 只存一份」的含义
eq(age_idx.row_mapping.row_id_for(ka1), A.row_mapping.row_id_for(ka1),
   "同一 SSTable 内不同列索引的 rowID 一致")

# ---- 6. 磁盘结构按类型选 ----
eq(on_disk_structure("John"), "byte-ordered trie", "字符串走 trie")
eq(on_disk_structure(21), "block-oriented balanced tree", "数值走 bbtree")
eq(on_disk_structure(3.5), "block-oriented balanced tree", "浮点也是 bbtree")
eq(on_disk_structure(True), "postings only", "布尔只存 postings")

# ---- 7. 两层组件 ----
comp = index_components()
eq(len(comp["per_sstable"]), 3, "SSTable 级三个组件")
eq(len(comp["per_column"]), 3, "列级三个组件")
ok("GROUP_COMPLETION_MARKER" in comp["per_sstable"], "SSTable 级有 GROUP 完成标记")
ok("COLUMN_COMPLETION_MARKER" in comp["per_column"], "列级有 COLUMN 完成标记")

# ---- 8. skinny vs wide ----
eq(PrimaryKey("pk", 1).has_clustering(), False, "无 clustering -> SkinnyPrimaryKeyMap")
eq(PrimaryKey("pk", 1, clustering="ck").has_clustering(), True, "有 clustering -> WidePrimaryKeyMap")

# ---- 9. 写路径不需要 read-before-write ----
mi = MemtableIndex("name")
mi.add("John", ka1)
mi.add("John", ka1)   # 重复写同一个 key
eq(len(mi.terms["John"]), 1, "重复写同一个 key 只在 postings 里出现一次")
mi2 = MemtableIndex("name")
mi2.add("John", ka1)
mi2.add("John", ka0)
eq(len(mi2.terms["John"]), 2, "不同 key 各自入 postings")

print("PASS=%d" % PASS)
sys.exit(0)
