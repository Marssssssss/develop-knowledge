"""DynamoDB 容量模型自检（期望值全部手算，见注释）。"""

import sys

from capacity import (
    RCU_PER_PARTITION,
    WCU_PER_PARTITION,
    Table,
    is_hot,
    max_ops_per_partition,
    random_suffix,
    rcu_for,
    read_fanout,
    wcu_for,
    write_shard_suffix,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAILED: " + label
    PASS += 1


def eq(a, b, label):
    ok(a == b, "%s (got %r want %r)" % (label, a, b))


# ---- 1. 容量单位换算 ----
eq(wcu_for(1024), 1, "1KB 写 = 1 WCU")
eq(wcu_for(1), 1, "不足 1KB 也是 1 WCU（向上取整）")
eq(wcu_for(1025), 2, "1025B 写 = 2 WCU")
eq(wcu_for(2048), 2, "2KB 写 = 2 WCU")
eq(rcu_for(4096), 1, "4KB 强一致读 = 1 RCU")
eq(rcu_for(4097), 2, "4097B 强一致读 = 2 RCU")
eq(rcu_for(4096, consistent=False), 0.5, "4KB 最终一致读 = 0.5 RCU")
eq(rcu_for(8192, consistent=False), 1.0, "8KB 最终一致读 = 1 RCU")

# ---- 2. 官方 20KB 例子：5 RCU -> 600 次/秒 ----
eq(rcu_for(20480), 5, "20KB 强一致读 = 5 RCU（ceil(20480/4096)）")
eq(max_ops_per_partition(20480), 600.0, "3000/5 = 600 次/秒")
eq(max_ops_per_partition(4096), 3000.0, "4KB 时就是分区上限 3000")
eq(max_ops_per_partition(4096, consistent=False), 6000.0, "最终一致读翻倍到 6000")
eq(RCU_PER_PARTITION, 3000, "分区读上限 3000 RCU")
eq(WCU_PER_PARTITION, 1000, "分区写上限 1000 WCU")

# 分区数是硬顶：预置再多的表容量，单分区也吃不到更多
t = Table(partitions=1, provisioned_rcu=100000, adaptive=True)
served, throttled = t.serve([5000])
eq(served[0], 3000, "单分区只给到 3000 RCU，超出即限流")
eq(throttled, 2000, "5000 - 3000 = 2000 被限流")

# ---- 3. adaptive capacity：冷分区的余量能借给热分区 ----
# 4 分区、8000 RCU，均分 2000；负载 [2800,1500,1500,1500]，总量 7300 < 8000
na = Table(partitions=4, provisioned_rcu=8000, adaptive=False)
na_served, na_throttled = na.serve([2800, 1500, 1500, 1500])
eq(na_served[0], 2000, "无 adaptive 时热分区只能拿均分的 2000")
eq(na_throttled, 800, "无 adaptive 时限流 800")

ad = Table(partitions=4, provisioned_rcu=8000, adaptive=True)
ad_served, ad_throttled = ad.serve([2800, 1500, 1500, 1500])
eq(ad_served[0], 2800, "有 adaptive 时热分区借到冷分区余量，吃满需求")
eq(ad_throttled, 0, "有 adaptive 时不限流")
ok(ad_throttled < na_throttled, "adaptive 确实降低了限流量")
# 成对构造：同样的负载，只差 adaptive 开关
eq(sum(ad_served), 7300, "adaptive 下总服务量等于总需求")
eq(sum(na_served), 6500, "无 adaptive 下总服务量只有 6500（浪费了 1500）")

# 借也借不出硬顶：需求超过 3000 的部分照样限流
over = Table(partitions=4, provisioned_rcu=8000, adaptive=True)
over_served, over_throttled = over.serve([3500, 1500, 1500, 1500])
eq(over_served[0], 3000, "adaptive 也冲不过 3000 的单分区硬顶")
eq(over_throttled, 500, "3500-3000 = 500 仍被限流")

# 总量不够时，adaptive 也变不出容量
short = Table(partitions=2, provisioned_rcu=2000, adaptive=True)
s_served, s_throttled = short.serve([1500, 1500])
eq(sum(s_served), 2000, "表总容量只有 2000，最多服务 2000")
eq(s_throttled, 1000, "需求 3000 里 1000 被限流")

# ---- 4. 热分区判据 ----
eq(is_hot([100, 100, 100, 100]), [], "完全均匀时没有热分区")
eq(is_hot([1000, 100, 100, 100]), [0], "一个分区吃掉 10 倍 -> 热分区")
# fair = 700/4 = 175，阈值 1.5*175 = 262.5
eq(is_hot([400, 100, 100, 100]), [0], "400 > 262.5 -> 热")
# fair = 580/4 = 145，阈值 1.5*145 = 217.5；180 不到阈值
eq(is_hot([180, 100, 100, 100]), [], "180 < 217.5，不算热")
# 边界成对：[180,100,100,100] 时 sum=480、fair=120、阈值正好 180，严格大于才判热
eq(is_hot([180, 100, 100, 100]), [], "等于阈值不算热（严格大于）")
eq(is_hot([181, 100, 100, 100]), [0], "刚过阈值就算热")

# ---- 5. 写分片后缀（官方给的算法：码点乘积 mod 200 再 +1）----
eq(write_shard_suffix("a"), 98, "'a' -> 97 %% 200 + 1 = 98")
# 'abc' = 97*98*99 = 941094；941094 % 200 = 94；+1 = 95
eq(write_shard_suffix("abc"), 95, "'abc' -> 941094 %% 200 + 1 = 95")
eq(write_shard_suffix("abc", shards=10), 5, "换成 10 个分片：941094 % 10 + 1 = 5")
# 同一个 order id 每次算出的后缀必须一致（否则读不到）
eq(write_shard_suffix("order-42"), write_shard_suffix("order-42"), "计算后缀是确定性的")
eq(write_shard_suffix(""), 2, "空串乘积为 1，1 %% 200 仍是 1，+1 = 2")

# ---- 6. 写分片的代价：读要扇出 ----
eq(read_fanout(200), 200, "200 个后缀 = 200 次 Query")
eq(random_suffix("2014-07-09", 7), "2014-07-09.7", "随机后缀拼在日期后面")
eq(len(set(random_suffix("2014-07-09", i) for i in range(1, 201))), 200, "200 个后缀互不重复")

print("PASS=%d" % PASS)
sys.exit(0)
