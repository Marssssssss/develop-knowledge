# -*- coding: utf-8 -*-
"""Index-Only Scan 与可见性地图断言(两个前置条件/VM 生命周期/INCLUDE 语义)。"""

from ionly import (
    Heap, PAGE_SIZE, covering_index, execute_ios, expression_index_gap,
    index_only_eligible, partial_index_ios,
)

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def main():
    print("1. 两个前置条件(官方 11.9 的四个例子)")
    xy = index_only_eligible("btree", ["x", "y"], ["x"])
    assert xy == (True, "ok")
    assert index_only_eligible("btree", ["x", "y"], ["x", "y"]) == (True, "ok")
    why1 = index_only_eligible("btree", ["x", "y"], ["x", "z"])
    why2 = index_only_eligible("btree", ["x", "y"], ["x"])  # z 在条件里同理
    assert why1[0] is False and "索引外列" in why1[1]
    assert index_only_eligible("gin", ["x"], ["x"])[0] is False
    ok("查询列必须 ⊆ 索引列(SELECT x,z / WHERE z 都不行);B-tree 恒支持 IOS,"
       "GIN 因只存部分值不支持")

    print("2. VM 生命周期:回堆次数由位图决定")
    heap = Heap(4)
    entries = [(0, "a"), (1, "b"), (2, "c"), (3, "d")]
    assert execute_ios(entries, heap) == 4            # 无 VACUUM:全回堆
    heap.vacuum([0, 1, 2])
    assert execute_ios(entries, heap) == 1
    heap.modify(1)                                     # 页被修改→位失效
    assert execute_ios(entries, heap) == 2
    ok("VM 位=『该页全部行对所有当前与未来事务可见』;VACUUM 置位、修改清零;"
       "位未置的页必须回堆查可见性——退化为普通索引扫描的代价")

    print("3. VM 体量与收益结构")
    per_page_vm_bits = 2                                # all-visible + all-frozen
    heap_bits = PAGE_SIZE * 8
    ratio = heap_bits / per_page_vm_bits
    assert ratio > 10000
    ok(f"VM 比堆小四个数量级(每页 {per_page_vm_bits} 位 vs {PAGE_SIZE*8} 位),"
       "常态驻留内存——拿 VM 访问换堆随机 IO")

    print("4. INCLUDE 覆盖索引语义")
    idx = covering_index(["x"], ["y"])
    assert idx.key == ("x",) and idx.unique_on == ("x",)
    assert idx.eligible(["x", "y"]) and not idx.eligible(["x", "z"])
    ok("INCLUDE 的 payload 不参与搜索键;UNIQUE(x) INCLUDE(y) 的唯一性只约束 x;"
       "y 的类型甚至可以不是索引能处理的类型(只是被存储)")

    print("5. 部分索引的谓词蕴含")
    elig, recheck = partial_index_ios(
        ["success"], ["subject", "success"], ["target"], ["subject", "target"])
    assert elig is True and recheck is False
    ok("WHERE success 里的 success 列不在索引中,但部分索引保证全体条目 success=true"
       "→运行期无需 recheck,IOS 仍可行(9.6+ 才识别)")

    print("6. 表达式索引的规划器局限")
    missing = expression_index_gap(["x"], ["f(x)"])
    assert missing == ["x"]
    ok("索引建在 f(x) 上时,SELECT f(x) WHERE f(x)<1 理论可 IOS,但规划器认为"
       "『x 不在索引里』不认账——需 CREATE INDEX ... (f(x)) INCLUDE (x) 补救")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
