"""Cassandra SAI 演示：两层索引结构、局部 rowID 与 token 序查询。"""

from sai import (
    MemtableIndex,
    PrimaryKey,
    SSTableIndex,
    index_components,
    naive_search,
    on_disk_structure,
    search,
)


def build_cluster():
    """两个 SSTable，各装两行；rowID 各自从 0 开始编号。"""
    ka0 = PrimaryKey("pk_a0", 40)
    ka1 = PrimaryKey("pk_a1", 20)
    kb0 = PrimaryKey("pk_b0", 5)
    kb1 = PrimaryKey("pk_b1", 30)

    ma = MemtableIndex("name")
    ma.add("John", ka1)
    ma.add("Boris", ka0)
    a = SSTableIndex.build("A", ma, [ka0, ka1])

    mb = MemtableIndex("name")
    mb.add("John", kb0)
    mb.add("Caleb", kb1)
    b = SSTableIndex.build("B", mb, [kb0, kb1])
    return [a, b]


def demo():
    idxs = build_cluster()

    print("=== 1. 两层索引结构 ===")
    comp = index_components()
    for layer, items in comp.items():
        print("  %-12s %s" % (layer, items))

    print()
    print("=== 2. 磁盘结构按类型选 ===")
    for v in ("John", 21, 3.5, True):
        print("  %-8r -> %s" % (v, on_disk_structure(v)))

    print()
    print("=== 3. rowID 是 SSTable 局部的 ===")
    for idx in idxs:
        print("  SSTable %s: rowID 0 -> %s, rowID 1 -> %s"
              % (idx.name, idx.row_mapping.key_for(0), idx.row_mapping.key_for(1)))
    print("  同一个 rowID 在两个 SSTable 里指向不同的行 -> 合并时必须带 SSTable 标识")

    print()
    print("=== 4. 查询 'John'：正确实现 vs 错误实现 ===")
    good = search(idxs, "John")
    bad = naive_search(idxs, "John")
    print("  正确（带 SSTable 标识 + token 序）: %s" % good)
    print("  错误（把 rowID 当全局）          : %s" % bad)
    print("  差异: %s" % ("结果不同，错误实现张冠李戴" if good != bad else "相同"))

    print()
    print("=== 5. 结果只保证 token 序 ===")
    for term in ("John", "Boris", "Caleb"):
        hits = search(idxs, term)
        print("  %-6s -> %s" % (term, [h.pk for h in hits] if hits else "无命中"))
    print("  交换 SSTable 顺序，结果不变: %s"
          % (search(list(reversed(idxs)), "John") == search(idxs, "John")))

    print()
    print("=== 6. 多列索引共享同一套 rowID ===")
    ka0 = PrimaryKey("pk_a0", 40)
    ka1 = PrimaryKey("pk_a1", 20)
    mage = MemtableIndex("age")
    mage.add(21, ka1)
    mage.add(50, ka0)
    age_idx = SSTableIndex.build("A", mage, [ka0, ka1])
    print("  name 索引里 pk_a1 的 rowID = %d" % idxs[0].row_mapping.row_id_for(ka1))
    print("  age  索引里 pk_a1 的 rowID = %d（相同 -> offset/token 只存一份）"
          % age_idx.row_mapping.row_id_for(ka1))


if __name__ == "__main__":
    demo()
