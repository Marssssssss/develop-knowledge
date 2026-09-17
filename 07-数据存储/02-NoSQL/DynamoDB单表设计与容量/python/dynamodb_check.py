"""
dynamodb_check.py — 自检:DynamoDB 容量换算、LSI/GSI 语义、项目集合与分区限流

每条断言对应官方文档中的明文数字或明确规则(出处见另两个文件的文件头)。
标注「官方示例」的断言直接用 AWS 文档给出的数字。

运行: python3 dynamodb_check.py
"""
from __future__ import annotations

import sys

from dynamodb_capacity import (
    MAX_ITEM_BYTES, MAX_PAGE_BYTES, PER_PARTITION_RCU, PER_PARTITION_WCU, batch_get_units,
    hot_partition, partitions_for_size, query_page, query_units, read_units, shard_count,
    sharded_read_queries, sustained_reads_per_partition, sustained_writes_per_partition,
    validate_item_size, validate_keys, write_units,
)
from dynamodb_design import (
    LSI_ITEM_COLLECTION_LIMIT, MAX_GSI_PER_TABLE, MAX_LSI_PER_TABLE, MAX_PROJECTED_ATTRS,
    Index, Item, ItemCollectionSizeLimitExceededException, PartitionLedger,
    ProvisionedThroughputExceededException, Table, ValidationException, build_key,
    projected_attr_count,
)

KB = 1024
PASS = 0
FAIL: list = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL.append("%s %s" % (label, detail))
        print("  FAIL  %s   %s" % (label, detail))


def raises(fn, exc) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


# =====================================================================
print("\n[1] 读单位:4 KB 粒度 × 一致性倍数(官方定价示例)")
check("10 KB 最终一致读 = 1.5 读单位(官方示例)", read_units(10 * KB, "eventual") == 1.5,
      str(read_units(10 * KB, "eventual")))
check("10 KB 强一致读 = 3 读单位(官方示例)", read_units(10 * KB, "strong") == 3)
check("10 KB 事务读 = 6 读单位(官方示例)", read_units(10 * KB, "transactional") == 6)
check("8 KB 最终一致读 = 1 读单位(官方定价示例)", read_units(8 * KB, "eventual") == 1)
check("8 KB 强一致读 = 2 读单位", read_units(8 * KB, "strong") == 2)
check("8 KB 事务读 = 4 读单位", read_units(8 * KB, "transactional") == 4)
check("4 KB+1 字节仍按 8 KB 计 → 2 读单位", read_units(4 * KB + 1, "strong") == 2)

print("\n[2] 写单位:1 KB 粒度(官方定价示例)")
check("1 KB 标准写 = 1 写单位", write_units(1 * KB) == 1)
check("3 KB 标准写 = 3 写单位(官方示例)", write_units(3 * KB) == 3)
check("3 KB 事务写 = 6 写单位(官方示例)", write_units(3 * KB, transactional=True) == 6)
check("10 KB 标准写 = 10 写单位(官方示例)", write_units(10 * KB) == 10)
check("10 KB 事务写 = 20 写单位(官方示例)", write_units(10 * KB, transactional=True) == 20)

print("\n[3] 单分区硬顶 3000 RCU / 1000 WCU(官方原文)")
check("20 KB 条目的强一致读消耗 5 读单位(官方示例)", read_units(20 * KB, "strong") == 5)
check("20 KB 条目单分区可支撑 600 次强一致读/秒(官方示例)",
      sustained_reads_per_partition(20 * KB, "strong") == 600)
check("1 KB 条目单分区 1000 次标准写/秒",
      sustained_writes_per_partition(1 * KB) == 1000)
check("单分区上限常量为 3000 读单位 / 1000 写单位(官方原文)",
      PER_PARTITION_RCU == 3000 and PER_PARTITION_WCU == 1000)
check("4 KB 条目单分区可支撑 3000 次强一致读/秒",
      sustained_reads_per_partition(4 * KB, "strong") == 3000)
check("每分区 1000 写/秒:1000 不越界、1001 越界",
      not hot_partition(1000, PER_PARTITION_WCU) and hot_partition(1001, PER_PARTITION_WCU))

print("\n[4] BatchGetItem 逐条取整 vs Query 整体取整(官方示例)")
check("1.5 KB + 6.5 KB 两项按 4KB+8KB=12 KB 计 → 3 读单位(官方示例)",
      batch_get_units([int(1.5 * KB), int(6.5 * KB)], "strong") == 3,
      str(batch_get_units([int(1.5 * KB), int(6.5 * KB)], "strong")))
check("同样的两条走 Query 整体取整 → 2 读单位(8 KB)",
      query_units(int(1.5 * KB) + int(6.5 * KB), "strong") == 2)
check("Query 返回合计 40.8 KB → 向上取整到 44 KB → 11 读单位(官方示例)",
      query_units(int(40.8 * KB), "strong") == 11, str(query_units(int(40.8 * KB), "strong")))
check("Query 返回 1500 条 × 64 B = 96000 B → 24 读单位",
      query_units(1500 * 64, "strong") == 24)

print("\n[5] 1 MB 分页(官方:Query/Scan 单页上限 1 MB + LastEvaluatedKey)")
check("恰好 1 MB → 单页且无 LastEvaluatedKey",
      query_page(MAX_PAGE_BYTES) == {"pages": 1, "last_evaluated_key": None})
check("2.5 MB → 3 页且带 LastEvaluatedKey",
      query_page(int(2.5 * 1024 * KB)) == {"pages": 3, "last_evaluated_key": "present"})
check("1 MB + 1 字节 → 2 页", query_page(MAX_PAGE_BYTES + 1)["pages"] == 2)

print("\n[6] 条目与键长度上限(官方 Cheat Sheet)")
check("条目 400 KB 合法", validate_item_size(MAX_ITEM_BYTES) == "")
check("条目 400 KB + 1 字节非法", "ItemSizeTooLarge" in validate_item_size(MAX_ITEM_BYTES + 1))
check("分区键 2048 / 排序键 1024 合法", validate_keys(2048, 1024) == [])
check("分区键 0 字节非法", validate_keys(0) != [])
check("分区键 2049 字节非法", validate_keys(2049) != [])
check("排序键 1025 字节非法", validate_keys(10, 1025) != [])

print("\n[7] 写分片(官方:日期键拼接 1..200 后缀)")
check("目标 1000 WCU 不需分片", shard_count(1000) == 1)
check("目标 1001 WCU → 2 个分片", shard_count(1001) == 2)
check("目标 200000 WCU → 200 个分片(与官方示例后缀范围 1..200 一致)",
      shard_count(200000) == 200)
check("分片后的读必须扇出到每个后缀(官方原文)",
      sharded_read_queries(200) == 200 and sharded_read_queries(1) == 1)
check("10 GB → 1 个分区;25 GB → 3 个分区",
      partitions_for_size(LSI_ITEM_COLLECTION_LIMIT) == 1
      and partitions_for_size(int(25 * 1024 ** 3)) == 3)

print("\n[8] LSI / GSI 结构约束")
t = Table("AppTable")
t.add_lsi(Index("by_ts", "LSI", "created_at"), at_creation=True)
check("LSI 必须建表时创建:事后追加抛错",
      raises(lambda: t.add_lsi(Index("late", "LSI", "x")), ValidationException))
check("LSI 与主表共用吞吐:带自有吞吐配置即抛错",
      raises(lambda: Table("T2").add_lsi(
          Index("bad", "LSI", "x", throughput=(10, 10)), at_creation=True), ValidationException))
t3 = Table("T3")
check("LSI 最多 5 个", raises(lambda: [t3.add_lsi(Index("l%d" % i, "LSI", "x"), at_creation=True)
                                      for i in range(MAX_LSI_PER_TABLE + 1)],
                              ValidationException))
check("GSI 必须声明自有吞吐",
      raises(lambda: Table("T4").add_gsi(Index("g", "GSI", "x")), ValidationException))
g = Table("T5")
for i in range(MAX_GSI_PER_TABLE):
    g.add_gsi(Index("g%d" % i, "GSI", "x", throughput=(10, 10)))
check("GSI 默认配额 20 个", len(g.gsis) == MAX_GSI_PER_TABLE)
check("第 21 个 GSI 抛错",
      raises(lambda: g.add_gsi(Index("g21", "GSI", "x", throughput=(10, 10))), ValidationException))

print("\n[9] GSI 写入语义:非唯一键、缺排序键不落索引、独立吞吐")
t6 = Table("Games")
t6.add_gsi(Index("ByTitle", "GSI", "TopScore", projection="ALL", throughput=(5, 5)))
r1 = t6.put(Item("USER#1", "GAME#A", {"TopScore": "0"}, 200))
r2 = t6.put(Item("USER#2", "GAME#A", {"TopScore": "0"}, 200))
check("GSI 键值不要求唯一(官方原文):两条同键写入均成功",
      r1.index_entries == ["ByTitle"] and r2.index_entries == ["ByTitle"])
r3 = t6.put(Item("USER#3", "GAME#B", {}, 200))
check("缺 GSI 排序键 → 不写索引条目、不消耗 GSI 写容量(官方原文)",
      r3.index_entries == [] and r3.index_units == {})
check("主表写单位照常计算(200 B → 1 写单位)", r3.table_units == 1)
check("写入同时消耗主表 + GSI 的写容量(官方:sum of both)",
      r1.table_units == 1 and r1.index_units == {"ByTitle": 1})
t7 = Table("Throttle")
t7.add_gsi(Index("Tiny", "GSI", "gsisk", projection="ALL", throughput=(5, 1)))
check("GSI 写容量不足 → 主表写被限流(官方原文)",
      raises(lambda: t7.put(Item("P#1", "S#1", {"gsisk": "v"}, 2 * KB)),
             ProvisionedThroughputExceededException))
t7b = Table("Throttle2")
t7b.add_gsi(Index("Tiny", "GSI", "gsisk", projection="ALL", throughput=(5, 2)))
check("把 GSI 写容量提到 2 → 2 KB 条目写成功",
      t7b.put(Item("P#1", "S#1", {"gsisk": "v"}, 2 * KB)).index_units == {"Tiny": 2})

print("\n[10] 读一致性:GSI 只能最终一致、LSI 支持强一致")
t8 = Table("Reads")
t8.add_lsi(Index("Lby", "LSI", "created_at", projection="KEYS_ONLY"), at_creation=True)
t8.add_gsi(Index("Gby", "GSI", "status", projection="KEYS_ONLY", throughput=(10, 10)))
t8.put(Item("P#1", "S#1", {"created_at": "2026", "status": "NEW"}, 1000))
check("GSI 强一致读 → 抛错(官方:GSI 仅支持最终一致读)",
      raises(lambda: t8.query("P#1", index="Gby", strongly_consistent=True), ValidationException))
check("GSI 最终一致读成功且标记为最终一致",
      t8.query("P#1", index="Gby")["strongly_consistent"] is False)
check("LSI 强一致读允许(官方原文)",
      t8.query("P#1", index="Lby", strongly_consistent=True)["strongly_consistent"] is True)
check("LSI 强一致读按 1× 计费,最终一致按 0.5× 计费",
      t8.query("P#1", index="Lby", strongly_consistent=True)["units"] == 1.0
      and t8.query("P#1", index="Lby")["units"] == 0.5)

print("\n[11] 非投影属性:LSI 可回表计费,GSI 不可回表")
t9 = Table("Proj")
t9.add_lsi(Index("Lsmall", "LSI", "created_at", projection="INCLUDE", included=["note"]),
           at_creation=True)
t9.add_gsi(Index("Gsmall", "GSI", "status", projection="KEYS_ONLY", throughput=(10, 10)))
# 主表条目 20 KB;"big" 只有 100 B —— 用来区分"按整条计费"与"按缺失属性计费"
t9.put(Item("P#1", "S#1", {"created_at": "2026", "status": "NEW", "note": "n", "big": "B" * 100},
            20 * KB))
check("GSI 查询取非投影属性 → 抛错(官方:GSI 查询不能回主表取属性)",
      raises(lambda: t9.query("P#1", index="Gsmall", need_attrs=["big"]), ValidationException))
lsi_res = t9.query("P#1", index="Lsmall", need_attrs=["big"])
check("LSI 查询取非投影属性 → 回主表,按整条条目计费(官方原文)",
      lsi_res["fetched_from_base"] == 1 and lsi_res["units"] > 0.5, str(lsi_res))
check("索引读按索引条目大小计(37 B → 0.5),回表按整条 20 KB 计 5 → 合计 5.5",
      lsi_res["units"] == 5.5, str(lsi_res["units"]))
check("对比:若按缺失属性大小(100 B)计费只会是 1.0,合计 1.5 —— 官方明确不是这样",
      lsi_res["units"] != 1.5)
check("投影属性命中时不回表",
      t9.query("P#1", index="Lsmall", need_attrs=["note"])["fetched_from_base"] == 0)

print("\n[12] LSI 项目集合 10 GB 上限")
big = Table("Collections")
big.lsi_collection_limit = 2500          # 见 README:用小阈值跑同一段逻辑
big.add_lsi(Index("Lall", "LSI", "created_at", projection="ALL"), at_creation=True)
big.put(Item("P#1", "S#1", {"created_at": "a"}, 1000))
check("项目集合含主表条目 + LSI 条目(官方原文)",
      big.item_collection_bytes("P#1") == 2000, str(big.item_collection_bytes("P#1")))
check("增长型写越过上限 → ItemCollectionSizeLimitExceededException",
      raises(lambda: big.put(Item("P#1", "S#2", {"created_at": "b"}, 1000)),
             ItemCollectionSizeLimitExceededException))
check("缩小集合的写仍被允许(官方原文)",
      big.put(Item("P#1", "S#1", {"created_at": "a"}, 500)).table_units == 1)
check("上限常量为 10 GB", LSI_ITEM_COLLECTION_LIMIT == 10 * 1024 ** 3)
nolimit = Table("NoLsi")
nolimit.put(Item("P#1", "S#1", {}, MAX_ITEM_BYTES))
check("没有 LSI 的表不受项目集合上限约束(官方原文)",
      nolimit.item_collection_bytes("P#1") == MAX_ITEM_BYTES)
check("不同分区键值各有独立项目集合",
      big.item_collection_bytes("P#2") == 0)

print("\n[13] 投影属性配额与索引条目大小")
check("投影属性合计上限为 100(官方)",
      MAX_PROJECTED_ATTRS == 100 and projected_attr_count(
          [Index("a", "LSI", "x", "INCLUDE", ["p", "q"]),
           Index("b", "GSI", "y", "INCLUDE", ["p"], throughput=(1, 1))]) == 3)
check("同一属性名进两个索引计 2 次(官方原文)",
      projected_attr_count([Index("a", "LSI", "x", "INCLUDE", ["p"]),
                            Index("b", "GSI", "y", "INCLUDE", ["p"], throughput=(1, 1))]) == 2)
check("KEYS_ONLY / ALL 不计入配额",
      projected_attr_count([Index("a", "LSI", "x", "ALL"), Index("b", "GSI", "y", "KEYS_ONLY",
                                                                 throughput=(1, 1))]) == 0)
it = Item("P#1", "S#1", {"a": "x" * 100}, 500)
check("ALL 投影索引条目与主表等大",
      Index("i", "LSI", "s", "ALL").entry_size(it) == 500)
check("KEYS_ONLY 条目远小于主表条目",
      Index("i", "LSI", "s", "KEYS_ONLY").entry_size(it) < 100)

print("\n[14] 单表设计键构造")
check("Order 实体键:USER#u1 / ORDER#2026",
      build_key("Order", user="u1", ts="2026") == ("USER#u1", "ORDER#2026"))
check("User 实体键:USER#u1 / PROFILE",
      build_key("User", user="u1") == ("USER#u1", "PROFILE"))

print("\n[15] 分区限流与自适应容量边界")
led = PartitionLedger()
for _ in range(10):
    led.spend("P#uniform", write=100)
check("均匀摊到 10 个分区键值:单键 1000 写/秒不越界", led.throttled() == [])
led2 = PartitionLedger()
led2.spend("P#hot", write=1001)
check("单个分区键值 1001 写/秒 → 被限流(自适应容量抬不动单分区硬顶)",
      led2.throttled() == ["P#hot"])
check("热点键定位:取写量最大的键", led2.busiest() == ("P#hot", 1001))
led3 = PartitionLedger()
led3.spend("P#r", read=3001)
check("单分区读超 3000 → 被限流", led3.throttled() == ["P#r"])
led3.reset()
check("重置后无用量", led3.throttled() == [] and led3.busiest() == ("", 0))

# =====================================================================
print("\n" + "=" * 68)
print("断言总数 %d,失败 %d" % (PASS + len(FAIL), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("全部通过")
