#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Redis 底层数据结构 demo 的断言集：字典渐进式 rehash、跳表、紧凑编码阈值。

来源口径见 dict_rehash.py / skiplist.py 头部；编码阈值来自 Redis 官方
"Memory optimization"（redis.io/docs/.../optimization/memory-optimization，实读）：
  Redis ≥ 7.0：hash-max-listpack-entries 512 / hash-max-listpack-value 64 /
  zset-max-listpack-entries 128 / zset-max-listpack-value 64 / set-max-intset-entries 512；
  Redis ≥ 7.2 另有 set-max-listpack-entries 128 / set-max-listpack-value 64；
  "If a specially encoded value overflows the configured max size, Redis will automatically
  convert it into normal encoding."

运行：python3 checks.py
"""
from __future__ import annotations

import random

from dict_rehash import AVOID, ENABLE, FORBID, INITIAL_SIZE, Dict, next_exp
from skiplist import ZSKIPLIST_MAXLEVEL, ZSKIPLIST_P, SkipList, zsl_random_level

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} :: {detail}")


def zset_encoding(size_hint: int, val_len_hint: int,
                  max_entries: int = 128, max_value: int = 64) -> str:
    """对应 zsetTypeCreate()：两个 hint 都在阈值内 → listpack，否则 skiplist。"""
    if size_hint <= max_entries and val_len_hint <= max_value:
        return "listpack"
    return "skiplist"


def hash_encoding(field_count: int, max_field_len: int,
                  max_entries: int = 512, max_value: int = 64) -> str:
    if field_count <= max_entries and max_field_len <= max_value:
        return "listpack"
    return "hashtable"


def set_encoding(size: int, all_int: bool, max_intset: int = 512,
                 max_listpack: int = 128) -> str:
    if all_int and size <= max_intset:
        return "intset"
    if size <= max_listpack:
        return "listpack"
    return "hashtable"


def section_dict() -> None:
    print("1) 字典：首次插入触发初始化扩容，1:1 时扩容并开始渐进式 rehash")
    d = Dict()
    d.insert("a", "1")
    check("首次插入后容量 = DICT_HT_INITIAL_SIZE(4)", d.size(0) == INITIAL_SIZE, d.stats())
    check("首次初始化不是真 rehash（rehashidx 仍为 -1）", d.rehashidx == -1, d.stats())
    for i in range(4):
        d.insert(f"k{i}", str(i))
    check("used 达到 1:1 后创建第二张表并置 rehashidx=0", d.size(1) > 0 and d.rehashidx == 0,
          d.stats())
    check("新表大小是 ≥ used+1 的最小 2 的幂", d.size(1) == 8, d.stats())
    check("扩容瞬间键分布在两张表上（used0 + used1 = 总数）",
          d.used[0] + d.used[1] == d.total() > 0, d.stats())

    print("\n2) rehash 期间查找必须同时看两张表，并跳过已迁移区间")
    snap = d.stats()
    tracked = ["a"] + [f"k{i}" for i in range(4)]      # 本节真正插入过的键
    missing = [k for k in tracked if d.find(k) is None]
    check("两张表并存期间全部键可查（table0 跳过 idx < rehashidx 的桶）", not missing, str(missing))
    check("查找本身会顺带搬桶（_dictRehashStep），步骤数增加",
          d.rehash_steps > snap["steps"], f"{snap['steps']} -> {d.rehash_steps}")

    print("\n3) rehash 期间插入进「新表」ht[1]")
    d2 = Dict()
    for i in range(4):
        d2.insert(f"x{i}", str(i))
    check("4 个键时尚未扩容（检查发生在插入之前，used 还没到 1:1）",
          not d2.is_rehashing() and d2.size(0) == INITIAL_SIZE, d2.stats())
    d2.insert("x4", "4")                                # 第 5 个键：插入前 used=4 = 容量，触发扩容
    check("第 5 个键触发扩容并进入 rehash 状态", d2.is_rehashing(), d2.stats())
    before = d2.used[1]
    d2.pauserehash += 1          # 模拟「有安全迭代器在场」：期间不搬桶，rehash 状态被冻结
    d2.insert("brand_new", "v")
    d2.pauserehash -= 1
    check("rehash 期间的新键计入 ht[1] 的 used（搬运暂停时最易观察）",
          d2.used[1] == before + 1 and d2.is_rehashing(), d2.stats())
    check("新键在两张表并存期间可查到", d2.find("brand_new") == "v")

    print("\n4) rehash 完成：ht[1] 拷回 ht[0]，rehashidx 回到 -1")
    d3 = Dict()
    for i in range(60):
        d3.insert(f"n{i}", str(i))
    d3.rehash(1000)
    check("完成后只有一张表在用", not d3.is_rehashing() and d3.used[1] == 0, d3.stats())
    check("表大小是 2 的幂且 ≥ 元素数", d3.size(0) >= d3.total() and (d3.size(0) & (d3.size(0) - 1)) == 0,
          d3.stats())
    check("全部 60 个键可查", all(d3.find(f"n{i}") == str(i) for i in range(60)))

    print("\n5) 不变量：插入与查找交错进行时，任意时刻所有键都可查")
    d4 = Dict()
    bad_keys = []
    for i in range(300):
        d4.insert(f"q{i}", str(i))
        d4.find(f"q{max(0, i - 7)}")
        if i % 25 == 0:
            bad_keys += [k for k in range(i + 1) if d4.find(f"q{k}") != str(k)]
    check("300 次插入（含跨多次 rehash）后无丢失键", not bad_keys, str(bad_keys[:5]))
    check("期间至少发生过 2 次表切换（steps 足够大）", d4.rehash_steps > 10, str(d4.rehash_steps))

    print("\n6) resize 开关：AVOID 时只在 4 倍处强制扩容；FORBID 完全不扩")
    d5 = Dict(AVOID)
    for i in range(16):
        d5.insert(f"a{i}", "v")
    check("AVOID 模式下 used(16) 尚未超过 4×capacity 的判定点时不扩容",
          len(d5.resize_log) == 1, str(d5.resize_log))
    d5.insert("a16", "v")                       # 插入前 used=16 = 4×4，强制扩容
    check("第 17 个键（插入前 used=16=4×4）触发强制扩容，新表 32 桶",
          len(d5.resize_log) == 2 and d5.resize_log[-1] == (4, 32), str(d5.resize_log))
    d6 = Dict(FORBID)
    for i in range(40):
        d6.insert(f"f{i}", "v")
    check("FORBID 模式下永不扩容（容量停在 4）", d6.size(0) == INITIAL_SIZE and not d6.is_rehashing(),
          d6.stats())

    print("\n7) 收缩：1:8 触发（AVOID 时退到 1:32），且不小于初始容量")
    d7 = Dict()
    for i in range(200):
        d7.insert(f"s{i}", "v")
    d7.rehash(1000)
    big = d7.size(0)
    for i in range(180):
        d7.delete(f"s{i}")
    d7.rehash(1000)
    check("删除后容量收缩", d7.size(0) < big, f"{big} -> {d7.size(0)}")
    check("收缩后仍 ≥ 元素数且为 2 的幂",
          d7.size(0) >= d7.total() and d7.size(0) & (d7.size(0) - 1) == 0, d7.stats())
    check("剩余 20 个键仍可查", all(d7.find(f"s{i}") == "v" for i in range(180, 200)))
    d8 = Dict()
    for i in range(8):
        d8.insert(f"t{i}", "v")
    for i in range(8):
        d8.delete(f"t{i}")
    check("容量已到初始值时不再收缩", d8.size(0) == INITIAL_SIZE and d8.total() == 0, d8.stats())

    print("\n8) 单步 rehash 的耗时上界：最多探望 n*10 个空桶")
    d9 = Dict()
    for i in range(3):
        d9.insert(f"z{i}", "v")
    d9.expand(1024)                          # 手工造一个稀疏新表
    start = d9.rehashidx
    d9.rehash(1)
    check("rehash(1) 的 rehashidx 前进步数 ≤ 10", d9.rehashidx - start <= 10,
          str(d9.rehashidx - start))

    print("\n9) 安全迭代器：暂停 rehash，且不重不漏")
    d10 = Dict()
    for i in range(30):
        d10.insert(f"i{i}", str(i))
    keys = d10.keys_safe()
    check("迭代结果无重复", len(keys) == len(set(keys)), f"{len(keys)} vs {len(set(keys))}")
    check("迭代结果覆盖所有键", set(keys) == {f"i{i}" for i in range(30)},
          str(sorted(set(f"i{i}" for i in range(30)) - set(keys))[:5]))
    check("迭代期间 pauserehash 计数已归零", d10.pauserehash == 0)
    check("next_exp(17) = 5（下一张表 32 桶，2 的幂）", next_exp(17) == 5, str(next_exp(17)))


def section_skiplist() -> None:
    print("\n10) 跳表：顺序、rank、反向指针")
    rnd = random.Random(1)
    sl = SkipList(rnd)
    data = [(rnd.randint(0, 50), f"e{i:04d}") for i in range(500)]
    for score, ele in data:
        sl.insert(score, ele)
    naive = sorted(data, key=lambda x: (x[0], x[1]))
    check("遍历顺序 = 按 (score, ele) 排序", sl.items() == naive)
    check("zslGetRank 为 1-based 且与朴素排名一致",
          all(sl.get_rank(s, e) == i for i, (s, e) in enumerate(naive, start=1)))
    check("get_element_by_rank 与 get_rank 互逆",
          all(sl.get_element_by_rank(i).ele == e for i, (s, e) in enumerate(naive, start=1)))
    check("level 0 的 backward 指针可反向遍历（ZREVRANGE）",
          sl.reverse_items() == list(reversed(naive)))
    check("允许重复 score", sum(1 for x in naive if x[0] == 3) > 1)

    print("\n11) 层数分布与空间开销")
    rnd2 = random.Random(7)
    levels = [zsl_random_level(rnd2) for _ in range(60000)]
    avg = sum(levels) / len(levels)
    check("平均层数 ≈ 1/(1-P) = 1.333（实测 ±0.03）", abs(avg - 1 / (1 - ZSKIPLIST_P)) < 0.03,
          f"{avg:.4f}")
    check("P(level ≥ 2) ≈ P = 0.25（实测 ±0.01）",
          abs(sum(1 for x in levels if x >= 2) / len(levels) - ZSKIPLIST_P) < 0.01,
          f"{sum(1 for x in levels if x >= 2) / len(levels):.4f}")
    check("层数上限 = ZSKIPLIST_MAXLEVEL(32)",
          max(levels) <= ZSKIPLIST_MAXLEVEL and zsl_random_level(ForcedLevels(100)) == ZSKIPLIST_MAXLEVEL,
          str(max(levels)))
    avg_ptr = sl.pointer_count() / sl.length
    check("每节点平均指针数 < 1.6（理论 1.33，远优于平衡树）", avg_ptr < 1.6, f"{avg_ptr:.3f}")

    print("\n12) 删除后 rank 与 level 的一致性")
    for score, ele in naive[:100]:
        sl.delete(score, ele)
    rest = naive[100:]
    check("删除 100 个后顺序仍正确", sl.items() == rest)
    check("删除后 rank 连续（无 span 泄漏）",
          all(sl.get_rank(s, e) == i for i, (s, e) in enumerate(rest, start=1)))
    while sl.length:
        score, ele = sl.items()[0]
        sl.delete(score, ele)
    check("清空后 level 回落到 1、tail 为空", sl.level == 1 and sl.tail is None, str(sl.level))


class ForcedLevels(random.Random):
    """让 zslRandomLevel 连续命中 100 次（源码头部的 while 本身无上界），
    验证返回值被 ZSKIPLIST_MAXLEVEL 截断 —— 现实中连续命中 32 次的概率是 0.25^32。"""

    def __init__(self, hits: int):
        super().__init__(0)
        self.hits = hits

    def random(self) -> float:
        if self.hits > 0:
            self.hits -= 1
            return 0.0          # < ZSKIPLIST_P，命中一次
        return 1.0              # 退出循环


def section_encoding() -> None:
    print("\n13) 紧凑编码阈值（Redis ≥ 7.0 / ≥ 7.2 官方默认值）")
    check("zset 128 元素 / 64 字节 → listpack", zset_encoding(128, 64) == "listpack")
    check("zset 129 元素 → skiplist（越界即转换）", zset_encoding(129, 64) == "skiplist")
    check("zset 单元素 65 字节 → skiplist", zset_encoding(128, 65) == "skiplist")
    check("hash 512 字段 / 64 字节 → listpack", hash_encoding(512, 64) == "listpack")
    check("hash 513 字段 → hashtable", hash_encoding(513, 64) == "hashtable")
    check("纯整数小集合 → intset（512 上限）",
          set_encoding(512, True) == "intset" and set_encoding(513, True) == "hashtable")
    check("含字符串的集合走 listpack(128) 而非 intset",
          set_encoding(100, False) == "listpack" and set_encoding(129, False) == "hashtable")


def main() -> int:
    print("=" * 74)
    section_dict()
    section_skiplist()
    section_encoding()
    print("\n" + "=" * 74)
    print(f"断言结果：pass={PASS} fail={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
