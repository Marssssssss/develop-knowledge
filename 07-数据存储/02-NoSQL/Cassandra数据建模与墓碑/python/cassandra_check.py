"""
cassandra_check.py — 自检:Cassandra 建模(主键/分区规模)与墓碑、压缩策略

断言对应 apache/cassandra 文档源文件中的明文规则(出处见另两个文件的文件头)。

运行: python3 cassandra_check.py
"""
from __future__ import annotations

import sys

from cassandra_modeling import (
    MAX_PARTITION_BYTES, MAX_PARTITION_VALUES, AccessPattern, PartitionStats,
    Schema, denormalization_cost, design_tables, lwt_budget, make_schema, mv_risk,
    parse_primary_key,
)
from cassandra_tombstone import (
    DEFAULT_GC_GRACE_SECONDS, LCS_L0_STCS_TRIGGER, STCS_MIN_THRESHOLD, Cell, SSTable,
    delete_with_tombstone, lcs_l0_failsafe, lcs_promote, purge_everywhere, purgeable,
    read, repair, sstableexpiredblockers, space_amplification_risk, stcs_trigger,
    ttl_expired, twcs_out_of_order, twcs_windows,
)

DAY = 86400
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


# =====================================================================
print("\n[1] 主键解析:第一个分量生成分区键,其余是聚簇键(官方原文)")
check("PRIMARY KEY (id) → 分区键 [id]、无聚簇键", parse_primary_key("id") == (["id"], []))
check("PRIMARY KEY (id,c) → 分区键 [id]、聚簇键 [c]",
      parse_primary_key("id,c") == (["id"], ["c"]))
check("PRIMARY KEY ((id1,id2),c1,c2) → 复合分区键 + 两个聚簇键",
      parse_primary_key("(id1,id2),c1,c2") == (["id1", "id2"], ["c1", "c2"]))
check("括号分量的空白被规范化",
      parse_primary_key("( id1 , id2 ), c1") == (["id1", "id2"], ["c1"]))
s1 = make_schema("magazine_name", "id")
check("单字段主键的表文本形态正确", s1.primary_key_text() == "PRIMARY KEY (id)",
      s1.primary_key_text())
s2 = make_schema("magazine_publisher", "publisher,id")
check("复合主键文本形态正确", s2.primary_key_text() == "PRIMARY KEY (publisher,id)",
      s2.primary_key_text())
s3 = make_schema("t", "(id1,id2),c1,c2")
check("复合分区键文本形态带括号", s3.primary_key_text() == "PRIMARY KEY ((id1,id2),c1,c2)",
      s3.primary_key_text())

print("\n[2] 分区定位:官方要求把查询涉及的分区数压到最小")
check("分区键等值绑定 → 命中单个分区", s2.single_partition(["publisher"]))
check("只绑聚簇键 → 无法定位分区(需过滤全部)", s2.needs_filtering(["id"]))
check("分区键+聚簇键都绑定 → 仍是单分区",
      s2.single_partition(["publisher", "id"]))
check("复合分区键只绑一半 → 仍需过滤",
      s3.needs_filtering(["id1"]))
check("复合分区键全绑 → 单分区", s3.single_partition(["id1", "id2"]))
check("命中分区数:单分区查询为 1", s2.partitions_touched(["publisher"], 5000) == 1)
check("命中分区数:未绑定分区键时为全部分区(5000)",
      s2.partitions_touched(["id"], 5000) == 5000)

print("\n[3] 排序只能落在聚簇键前缀上")
p_sort = AccessPattern("q1", "Magazine", where_eq=["publisher"], order_by=["id"])
check("ORDER BY 命中聚簇键前缀 → 可排序", p_sort.sortable(s2))
p_bad = AccessPattern("q2", "Magazine", where_eq=["publisher"], order_by=["name"])
check("ORDER BY 非聚簇键 → 不可排序", not p_bad.sortable(s2))

print("\n[4] 分区规模指引:值个数 < 100,000、磁盘 < 100 MB(官方原文)")
check("常量为 100,000 与 100 MB",
      MAX_PARTITION_VALUES == 100000 and MAX_PARTITION_BYTES == 100 * 1024 * 1024)
check("恰好达线不告警", PartitionStats(MAX_PARTITION_VALUES, MAX_PARTITION_BYTES).warnings() == [])
check("值个数超线告警",
      PartitionStats(MAX_PARTITION_VALUES + 1, 0).warnings()[0].startswith("partition_values_over_100k"))
check("磁盘超线告警",
      PartitionStats(0, MAX_PARTITION_BYTES + 1).warnings()[0].startswith("partition_disk_over_100mb"))
check("两项都超 → 两条告警", len(PartitionStats(200000, 200 * 1024 * 1024).warnings()) == 2)

print("\n[5] 查询驱动建模:一张表服务一个查询(官方原文)")
pat_user = AccessPattern("by_user", "Order", where_eq=["user"], order_by=["ts"])
pat_pub = AccessPattern("by_publisher", "Order", where_eq=["publisher"], order_by=["ts"])
tables = design_tables("Order", [pat_user, pat_pub])
check("两个查询 → 两张表", len(tables) == 2)
check("按查询设计的表名可读", [t.name for t in tables] == ["order_by_user", "order_by_publisher"])
check("分区键来自查询的第一个等值字段", tables[0].partition_key == ["user"])
check("排序字段落在聚簇键上", tables[0].clustering == ["ts"])
check("同一实体进多张表 = 数据被复制(官方原文)", denormalization_cost(tables, 512)["copies"] == 2)
check("反规范化写放大:同一实体写 N 份", denormalization_cost(tables, 512)["bytes_written"] == 1024)

print("\n[6] LWT 与物化视图")
s_ok = make_schema("a", "id")
s_lwt = make_schema("b", "id")
s_lwt.lwt_ops = 1
s_many = make_schema("c", "id")
s_many.lwt_ops = 5
check("无条件更新 → no_lwt", lwt_budget([s_ok]) == "no_lwt")
check("少量 LWT 可接受(官方:应压到最少)", lwt_budget([s_lwt]).startswith("acceptable"))
check("大量 LWT → minimize 提示", lwt_budget([s_many]).startswith("minimize"))
s_mv = make_schema("d", "id")
s_mv.mvs = ["mv1"]
check("用物化视图 → 标注 experimental(官方原文)", mv_risk(s_mv) == "experimental")
check("未用物化视图 → none", mv_risk(s_ok) == "none")

# =====================================================================
print("\n[7] 读路径:墓碑遮挡更早时间戳(官方原文)")
rows = [("P1", "C1", Cell(ts=50, value="v1")), ("P1", "C1", Cell(ts=100, tombstone=True))]
check("墓碑(ts=100)遮住更早的 v1(ts=50) → 读到空",
      read(rows, "P1", "C1") is None)
check("宽限期内新写 v2(ts=150)胜过墓碑 → 读到 v2",
      read(rows + [("P1", "C1", Cell(ts=150, value="v2"))], "P1", "C1") == "v2")
check("无墓碑时取时间戳最大者",
      read([("P1", "C1", Cell(ts=10, value="old")),
            ("P1", "C1", Cell(ts=20, value="new"))], "P1", "C1") == "new")
check("墓碑不影响其他分区键", read(rows, "P2", "C1") is None)
check("墓碑不遮挡同分区的其他聚簇键",
      read(rows + [("P1", "C2", Cell(ts=10, value="other"))], "P1", "C2") == "other")

print("\n[8] TTL 到期被当作删除处理(官方 TTL 注记)")
check("TTL 未到期 → 不视为过期", not ttl_expired(Cell(ts=0, value="v", ttl=100), 50))
check("TTL 到期 → 视为过期", ttl_expired(Cell(ts=0, value="v", ttl=100), 101))
check("无 TTL 的单元格永不过期", not ttl_expired(Cell(ts=0, value="v"), 10 ** 9))

print("\n[9] 墓碑清除的三个条件(官方原文)")
tomb = Cell(ts=1000, tombstone=True, deleted_at=1000)
t_old = SSTable("old", [("P1", "C1", Cell(ts=500, value="v1"))])
t_other = SSTable("other", [("P2", "C1", Cell(ts=500, value="x"))])
now_inside = 1000 + DEFAULT_GC_GRACE_SECONDS - 1
now_after = 1000 + DEFAULT_GC_GRACE_SECONDS + 1
check("官方默认 gc_grace = 864000 秒(10 天)", DEFAULT_GC_GRACE_SECONDS == 10 * DAY)
check("宽限期内不可清除",
      purgeable(tomb, now_inside, "P1", [t_old], [t_old]) == (False, "within_grace_period"))
ok, why = purgeable(tomb, now_after, "P1", [t_old], [t_old])
check("过期 + 遮蔽 SSTable 同在本次压缩 → 可清除", ok and why == "purgeable", why)
ok2, why2 = purgeable(tomb, now_after, "P1", [], [t_old, t_other])
check("过期但遮蔽 SSTable 不在本次压缩 → 不可清除",
      (not ok2) and why2.startswith("shadowing_sstable_outside_compaction"), why2)
check("遮蔽 SSTable 里只有别的分区 → 不构成阻碍(官方只要求含该分区的旧数据)",
      purgeable(tomb, now_after, "P1", [t_old], [t_old, t_other])[0])
ok3, why3 = purgeable(tomb, now_after, "P1", [t_old], [t_old], repaired=False,
                      only_purge_repaired=True)
check("only_purge_repaired_tombstones 且未修复 → 不可清除",
      (not ok3) and why3 == "only_purge_repaired_tombstones", why3)
check("边界:恰好等于 gc_grace 仍算宽限期内",
      purgeable(tomb, 1000 + DEFAULT_GC_GRACE_SECONDS, "P1", [t_old], [t_old])[0] is False)

print("\n[10] 完全过期的 SSTable 与 sstableexpiredblockers")
live = SSTable("live", [("P1", "C1", Cell(ts=10, value="v", ttl=100))])
expired = SSTable("expired", [("P3", "C1", Cell(ts=10, value="v", ttl=1))])
check("只含墓碑/过期 TTL 的文件判定为 fully expired",
      expired.expired_only(now=10 ** 6) and not live.expired_only(now=50))
res = sstableexpiredblockers([expired, SSTable("other", [("P9", "C1", Cell(ts=9, value="x"))])],
                             now=10 ** 6)
check("无遮蔽 → 可丢弃", res["droppable"] == ["expired"], str(res))
res2 = sstableexpiredblockers([expired, SSTable("keep", [("P3", "C1", Cell(ts=20, value="x"))])],
                              now=10 ** 6)
check("有同分区未过期文件遮蔽 → 被挡下并列出阻塞者",
      res2["droppable"] == [] and res2["blocked"]["expired"] == ["keep"], str(res2))

print("\n[11] 僵尸数据复活场景(官方三节点示例)")
no_tomb = [["A"], [], ["A"]]
no_tomb = repair(no_tomb, (1, 2))
no_tomb = repair(no_tomb, (0, 1))
check("先删数据(不写墓碑)+ 修复 → 已删数据被复制回来(zombie)",
      all("A" in n for n in no_tomb), str(no_tomb))

with_tomb = [["A"], ["A"], ["A"]]
with_tomb, applied = delete_with_tombstone(with_tomb, [0, 1], "A")
check("在两个节点写入墓碑(第三个节点离线,没收到)", applied == 2)
check("离线节点仍留着活数据", with_tomb[2] == ["A"])
nodes_after = repair(with_tomb, (1, 2))
check("墓碑参与修复 → 删除状态被正确传播,数据不复活",
      all("A" not in n for n in nodes_after[1:]), str(nodes_after))

zombie = [["A"], ["A"], ["A"]]
zombie, _ = delete_with_tombstone(zombie, [0, 1], "A")
zombie = purge_everywhere(zombie, "A", 2)
check("宽限期外加压缩把墓碑清掉后,集群里再无删除记录",
      zombie[0] == [] and zombie[1] == [] and zombie[2] == ["A"], str(zombie))
zombie = repair(zombie, (0, 2))
check("此时修复会把数据复制回去 = 官方描述的 zombie", "A" in zombie[0], str(zombie))

print("\n[12] 压缩策略:STCS 分桶与空间放大")
check("STCS 默认攒够 4 个 SSTable 触发", STCS_MIN_THRESHOLD == 4)
check("4 个大小相近(100/102/98/101)→ 触发",
      stcs_trigger([100, 102, 98, 101]) is not None)
check("3 个不够触发", stcs_trigger([100, 100, 100]) is None)
check("4 个但大小悬殊(1/100/200/4000)→ 不落进同一桶",
      stcs_trigger([1, 100, 200, 4000]) is None,
      str(stcs_trigger([1, 100, 200, 4000])))
check("空间放大风险:最大 SSTable 占了大半 → 标 high",
      space_amplification_risk([1000, 1000, 1000, 9000]).startswith("high"))
check("分布均匀 → moderate", space_amplification_risk([100, 100, 100, 100]) == "moderate")
check("空输入 → none", space_amplification_risk([]) == "none")

print("\n[13] 压缩策略:LCS 层级与 L0 兜底")
info = lcs_promote([100, 1600, 16000, 500000], base=160)
check("每层目标是上一层的 10 倍(官方原文)",
      info["targets"][:4] == [160, 1600, 16000, 160000], str(info["targets"]))
check("超目标的层被标出(第 4 层 500000 > 160000)", info["over_target_levels"] == [3])
check("L0 超过 32 个 SSTable → 先在 L0 走 STCS 兜底(官方原文)",
      lcs_l0_failsafe(LCS_L0_STCS_TRIGGER + 1) == "stcs_in_l0"
      and lcs_l0_failsafe(LCS_L0_STCS_TRIGGER) == "lcs")

print("\n[14] 压缩策略:TWCS 时间窗口")
w = twcs_windows([0, 10, 60, 61, 130], window_seconds=60)
check("按 60 秒窗口分桶", sorted(w) == [0, 1, 2] and w[1] == [60, 61], str(w))
check("乱序写入会被识别(官方 Operational Concerns:TWCS 最怕乱序)",
      twcs_out_of_order([100, 50, 60], 60) and not twcs_out_of_order([50, 60, 100], 60))

# =====================================================================
print("\n" + "=" * 68)
print("断言总数 %d,失败 %d" % (PASS + len(FAIL), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("全部通过")
