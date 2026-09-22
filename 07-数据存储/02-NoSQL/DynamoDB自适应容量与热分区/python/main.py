"""热分区场景：时间序列表以日期为 partition key，写分片前后的限流对比。"""

from capacity import (
    Table,
    is_hot,
    max_ops_per_partition,
    rcu_for,
    read_fanout,
    wcu_for,
    write_shard_suffix,
)


def demo():
    print("=== 1. 容量单位换算 ===")
    for size in (512, 1024, 2048, 4096, 20480):
        print("  item %6dB -> 写 %2d WCU / 强一致读 %2d RCU / 单分区上限 %.0f 次每秒"
              % (size, wcu_for(size), rcu_for(size), max_ops_per_partition(size)))

    print()
    print("=== 2. 时间序列表：日期当 partition key 的热分区 ===")
    # 一年 365 个日期键，今天的写入全落一个键
    today_load = 2800
    others = 40
    loads = [today_load] + [others] * 9
    print("  负载: 今天 %d RCU，其余 9 个日期各 %d RCU，表预置 8000 RCU / 10 分区"
          % (today_load, others))
    print("  判为热分区的下标: %s" % is_hot(loads))
    for adaptive in (False, True):
        t = Table(partitions=10, provisioned_rcu=8000, adaptive=adaptive)
        served, throttled = t.serve(loads)
        print("  adaptive=%-5s -> 服务 %d / 限流 %d（热分区拿到 %d）"
              % (adaptive, sum(served), throttled, served[0]))

    print()
    print("=== 3. 写分片：把今天这一个键拆成 N 个 ===")
    day = "2014-07-09"
    for shards in (10, 50, 200):
        per = today_load / float(shards)
        print("  拆成 %3d 个后缀 -> 每键 %.1f RCU（分区上限 3000），读一天要 %d 次 Query"
              % (shards, per, read_fanout(shards)))

    print()
    print("=== 4. 计算后缀 vs 随机后缀 ===")
    for oid in ("order-42", "order-43", "abc"):
        print("  %-10s -> 后缀 %3d（可反算，GetItem 直接命中）"
              % (oid, write_shard_suffix(oid)))
    print("  随机后缀不可反算：GetItem 必须扫全部后缀，只有批量读场景才划算")

    print()
    print("=== 5. 借不出硬顶：需求超过 3000 照样限流 ===")
    t = Table(partitions=4, provisioned_rcu=20000, adaptive=True)
    served, throttled = t.serve([5000, 0, 0, 0])
    print("  表预置 20000 RCU，单键 5000 RCU -> 服务 %d / 限流 %d"
          % (served[0], throttled))
    print("  结论：加表级预置容量救不了单分区热点，只能改键设计")


if __name__ == "__main__":
    demo()
