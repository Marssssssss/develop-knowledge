# -*- coding: utf-8 -*-
"""自检: 键空间语义 / vindex 映射 / 路由 fan-out / resharding。

断言全部来自官方文档与源码事实, 不用"我印象中的 Vitess"。
"""

from keyranges import (canonical, parse_shard_name, validate_partition,
                       generate_shard_ranges, split_shard, locate)
from vindexes import get as get_vindex, reverse64, HASH_DOC
from router import route_equal, route_range, route_unconstrained, reshard

PASS = [0]
FAIL = []


def ok(cond, label):
    if cond:
        PASS[0] += 1
    else:
        FAIL.append(label)


TWO = ["-80", "80-"]
FOUR = ["-40", "40-80", "80-c0", "c0-"]

# --- 1. 键范围: 含起点、不含终点 -------------------------------------------
kr = parse_shard_name("40-80")
ok(kr.contains(b"\x40"), "起点含: 0x40 属于 40-80")
ok(kr.contains(b"\x7f\xff"), "0x7fff 属于 40-80")
ok(not kr.contains(b"\x80"), "终点不含: 0x80 不属于 40-80")
ok(not kr.contains(b"\x3f"), "0x3f 不属于 40-80")
ok(parse_shard_name("-80").contains(b"\x7f\xff\xff"), "空起点代表最小值")
ok(parse_shard_name("80-").contains(b"\x80"), "0x80 属于 80-")
ok(parse_shard_name("-").contains(b"\xff" * 8), "'-' 是全区间")

# --- 2. 左对齐: 右侧 0 无意义 ------------------------------------------------
ok(canonical(b"\x80") == canonical(b"\x80\x00"), "左对齐: 0x80 == 0x8000")
ok(not parse_shard_name("-80").contains(b"\x80\x00"), "0x8000 不在 -80(它等于中点 0x80)")
ok(parse_shard_name("80-").contains(b"\x80\x00"), "0x8000 落在 80-(起点含)")
ok(locate(TWO, b"\x80\x00") == ["80-"], "locate(0x8000) 唯一命中 80-")

# --- 3. 分区完整性 -----------------------------------------------------------
ok(validate_partition(FOUR)[0], "四个分片构成完整分区")
ok(validate_partition(TWO)[0], "两个分片构成完整分区")
ok(not validate_partition(["40-80", "80-"])[0], "缺空起点 -> 不完整")
ok(validate_partition(["-40", "40-"])[0], "40- 的终点为空, 是合法分区")
ok(not validate_partition(["-40", "40-80"])[0], "最右分片终点非空 -> 不完整")
ok(not validate_partition(["-40", "80-"])[0], "中间有洞 -> 不完整")
ok(not validate_partition(["-80", "40-"])[0], "重叠 -> 不完整")
ok(validate_partition(["-80", "80-c0", "c0-dc00", "dc00-dc80", "dc80-"])[0],
   "文档里的不等分五分片也是完整分区")

# --- 4. GenerateShardRanges(2 的幂等分) --------------------------------------
ok(generate_shard_ranges(2) == ["-80", "80-"], "2 分片 = -80 / 80-")
ok(generate_shard_ranges(4) == FOUR, "4 分片 = -40 / 40-80 / 80-c0 / c0-")
ok(generate_shard_ranges(8)[0] == "-20" and generate_shard_ranges(8)[1] == "20-40", "8 分片从 -20 起")
ok(len(generate_shard_ranges(16)) == 16, "16 分片")
try:
    generate_shard_ranges(3)
    ok(False, "非幂次应报错")
except ValueError:
    ok(True, "非幂次分片数直接报错(不编造官方行为)")

# --- 5. vindex 映射(逐条对拍源码) --------------------------------------------
num = get_vindex("numeric")
ok(num.hash(1) == b"\x00" * 7 + b"\x01", "numeric(1) = 大端 8 字节")
ok(num.unhash(num.hash(0x1234)) == 0x1234, "numeric 可逆")
ok(num.hash(0).hex() == "0000000000000000", "numeric(0) 全零")
ok(num.cost == 0 and get_vindex("binary").cost == 0, "binary/numeric 的 Cost = 0")
ok(get_vindex("reverse_bits").cost == 1, "reverse_bits 的 Cost = 1")
ok("DES" in HASH_DOC, "hash vindex 只记录定义(DES 未重造)")

rb = get_vindex("reverse_bits")
ok(rb.hash(1) == b"\x80" + b"\x00" * 7, "reverse_bits(1) = 0x8000000000000000")
ok(reverse64(0) == 0 and reverse64(1) == (1 << 63), "Reverse64 的边界")
for v in (0, 1, 255, 2 ** 63, 2 ** 64 - 1, 0x0123456789abcdef):
    ok(rb.unhash(rb.hash(v)) == v, "reverse_bits 可逆 v=%d" % v)
ok(get_vindex("binary").hash(b"\xab\xcd") == b"\xab\xcd", "binary 是恒等映射")

# --- 6. 分布: numeric 聚集 vs reverse_bits 打散 -------------------------------
ids = list(range(10000))
n_route = route_equal("numeric", ids, TWO)
r_route = route_equal("reverse_bits", ids, TWO)
ok(n_route["fanout"] == 1 and len(n_route["shards"]["-80"]) == 10000,
   "numeric: 0..9999 全部落 -80(位模式映射不打散)")
ok(r_route["fanout"] == 2, "reverse_bits: 两个分片都命中")
ok(len(r_route["shards"]["-80"]) == 5000 and len(r_route["shards"]["80-"]) == 5000,
   "reverse_bits: 按 id 奇偶精确 5000/5000(最高位 = id 的最低位)")
ok(all(len(locate(TWO, rb.hash(i))) == 1 for i in ids[:50]), "每个 id 只落一个分片")

# --- 7. 路由 fan-out -----------------------------------------------------------
ok(route_equal("reverse_bits", [1, 5], FOUR)["shards"].keys() == {"80-c0"},
   "reverse_bits: id=1 -> 0x80.., id=5 -> 0xa0.., 同落 80-c0")
ok(route_equal("reverse_bits", [1, 3], FOUR)["fanout"] == 2,
   "reverse_bits: id=3 -> 0xc0.., 与 id=1 分处两片")
ok(route_equal("reverse_bits", [1, 2], FOUR)["fanout"] == 2, "相邻 id 反被拆到两片")
ok(route_unconstrained(FOUR)["fanout"] == 4, "无分片键 -> scatter 4")
ok(route_range("numeric", 0, 1 << 63, TWO)["fanout"] == 1, "numeric 的 [0,2^63) 只压一个分片")
ok(route_range("numeric", 0, (1 << 64) - 1, TWO)["fanout"] == 2, "跨过中点后才 fan-out 2")
try:
    num.hash(1 << 64)
    ok(False, "numeric 应拒绝 uint64 上溢")
except OverflowError:
    ok(True, "numeric 的输入是 uint64, 2^64 上溢")
ok(route_range("reverse_bits", 1, 100, TWO)["kind"] == "scatter",
   "reverse_bits 无 RangeMap -> 退化为 scatter")

# --- 8. resharding: 键不动, 只有边界动 ----------------------------------------
new_shards, kids = reshard(FOUR, "80-c0")
ok(kids == ["80-a0", "a0-c0"], "80-c0 对半切成 80-a0 / a0-c0")
ok(len(new_shards) == 5, "切一片后分区变 5 片")
ok(validate_partition(new_shards)[0], "切完仍是完整分区")
for i in (1, 2, 3, 0x7fffffffffffffff, 0x8000000000000123, 0xa000000000000001):
    before = locate(FOUR, num.hash(i))
    after = locate(new_shards, num.hash(i))
    ok(len(before) == 1, "切分前唯一命中 (id=%d)" % i)
    ok(len(after) == 1, "切分后唯一命中 (id=%d)" % i)
    if before and before[0] != "80-c0":
        ok(after == before, "非目标分片不动 (id=%d)" % i)
    elif before:
        ok(after[0] in kids, "目标分片的键落进它的两个子分片之一 (id=%d)" % i)
ok(all(len(locate(new_shards, num.hash(i))) == 1 for i in ids[:200]), "切分后定位仍唯一")

if __name__ == "__main__":
    print("PASS=%d FAIL=%d" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
