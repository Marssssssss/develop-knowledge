# -*- coding: utf-8 -*-
"""分片与路由键空间 —— 演示入口。

场景均可在真实 Vitess 上复现: 键空间语义来自官方《Sharding》文档, vindex 映射来自
`go/vt/vtgate/vindexes/` 源码。
"""

from keyranges import parse_shard_name, validate_partition, generate_shard_ranges
from vindexes import get as get_vindex
from router import route_equal, route_range, route_unconstrained, reshard

TWO = ["-80", "80-"]
FOUR = ["-40", "40-80", "80-c0", "c0-"]


def hr(title):
    print("\n== %s ==" % title)


def scenario_keyspace():
    hr("1. 键范围: 含起点、不含终点; 0x80 是正中值")
    print("分区示例:", FOUR, "合法?", validate_partition(FOUR))
    for k in (b"\x3f", b"\x40", b"\x7f", b"\x80", b"\xc0", b"\x80\x00"):
        hit = [s for s in FOUR if parse_shard_name(s).contains(k)]
        print("  ksId %-10s -> %s" % ("0x" + k.hex(), hit))
    print("  左对齐使右侧 0 可省略: 0x80 与 0x8000 是同一个值(见 canonical)")


def scenario_vindex():
    hr("2. Primary Vindex 决定数据落在哪一片")
    ids = list(range(10000))
    for name in ("numeric", "reverse_bits"):
        vi = get_vindex(name)
        r = route_equal(name, ids, TWO)
        print("  %-13s cost=%d 首位 ksId=%s 分布=%s" % (
            name, vi.cost, vi.hash(1).hex(), {k: len(v) for k, v in r["shards"].items()}))
    print("  numeric 是**位模式**映射, 连续小 id 全挤在 key space 低端 -> 100% 落一片")
    print("  reverse_bits 把最低位搬到最高位 -> 按 id 奇偶精确对半(各 5000)")


def scenario_fanout():
    hr("3. 路由 fan-out: 单分片 / 键范围 / scatter")
    print("  WHERE id IN (1)            reverse_bits:", route_equal("reverse_bits", [1], FOUR)["fanout"])
    print("  WHERE id IN (1,5)          reverse_bits:", route_equal("reverse_bits", [1, 5], FOUR)["fanout"])
    print("  WHERE id IN (1,2)          reverse_bits:", route_equal("reverse_bits", [1, 2], FOUR)["fanout"])
    print("  WHERE id BETWEEN 0 AND 2^63 numeric   :", route_range("numeric", 0, 1 << 63, TWO)["fanout"])
    print("  WHERE id BETWEEN 1 AND 100  reverse_bits:",
          route_range("reverse_bits", 1, 100, TWO)["kind"], "(无 RangeMap, 只能 scatter)")
    print("  无分片键条件                        :", route_unconstrained(FOUR)["fanout"], "(全分片)")


def scenario_reshard():
    hr("4. Resharding: 键不动, 只有分片边界动")
    new_shards, kids = reshard(FOUR, "80-c0")
    print("  80-c0 ->", kids, " 新分区:", new_shards, validate_partition(new_shards))
    num = get_vindex("numeric")
    for i in (0x7fffffffffffffff, 0x8000000000000123, 0xa000000000000001, 0xffffffffffffffff):
        before = [s for s in FOUR if parse_shard_name(s).contains(num.hash(i))]
        after = [s for s in new_shards if parse_shard_name(s).contains(num.hash(i))]
        print("  id=0x%x  切分前 %s -> 切分后 %s" % (i, before, after))


if __name__ == "__main__":
    print("Vitess 分片与路由键空间(官方文档 + vindex 源码逐行转写)")
    scenario_keyspace()
    scenario_vindex()
    scenario_fanout()
    scenario_reshard()
    print("\n切分粒度提示: 官方 GenerateShardRanges 按 2 的幂等分,", generate_shard_ranges(4))
