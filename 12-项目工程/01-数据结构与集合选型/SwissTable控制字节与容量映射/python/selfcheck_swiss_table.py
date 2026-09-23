"""663 自检：Abseil 与 hashbrown 的每一个常量都来自实读源码。"""

from __future__ import annotations

import sys

from swiss_table import (
    ABSL_DELETED,
    ABSL_EMPTY,
    ABSL_KWIDTH_GENERIC,
    ABSL_KWIDTH_SSE2,
    ABSL_SENTINEL,
    HB_DELETED,
    HB_EMPTY,
    MASK64,
    absl_h1,
    absl_h2,
    absl_is_deleted,
    absl_is_empty,
    absl_is_empty_or_deleted,
    absl_is_full,
    absl_probe_seq,
    as_i8,
    as_u8,
    capacity_to_growth,
    countl_zero,
    hb_bucket_mask_to_capacity,
    hb_capacity_to_buckets,
    hb_h1,
    hb_is_deleted,
    hb_is_empty,
    hb_is_full,
    hb_probe_seq,
    hb_special_is_empty,
    hb_tag_full,
    is_valid_capacity,
    max_capacity_for_load_factor_one,
    next_capacity,
    normalize_capacity,
    previous_capacity,
    size_to_capacity,
)

PASS = 0


def check(cond, msg):
    global PASS
    assert cond, msg
    PASS += 1


# ------------------------------------------- 一、哨兵字节：两派正好相反
check(as_u8(ABSL_EMPTY) == 0x80, "Abseil kEmpty = -128 = 0x80")
check(as_u8(ABSL_DELETED) == 0xFE, "Abseil kDeleted = -2 = 0xFE")
check(as_u8(ABSL_SENTINEL) == 0xFF, "Abseil kSentinel = -1 = 0xFF")
check(HB_EMPTY == 0xFF, "hashbrown Tag::EMPTY = 0xFF")
check(HB_DELETED == 0x80, "hashbrown Tag::DELETED = 0x80")
check(as_u8(ABSL_EMPTY) == HB_DELETED, "**Abseil 的 EMPTY 就是 hashbrown 的 DELETED**")
check(as_u8(ABSL_SENTINEL) == HB_EMPTY, "**Abseil 的 SENTINEL 就是 hashbrown 的 EMPTY**")
check(as_u8(ABSL_DELETED) not in (HB_EMPTY, HB_DELETED), "0xFE 在 hashbrown 里既不是 EMPTY 也不是 DELETED")
# 源码里那串 static_assert：三个哨兵都要有最高位
for c in (ABSL_EMPTY, ABSL_DELETED, ABSL_SENTINEL):
    check(c & 0x80 != 0, f"源码 static_assert：{c} 必须置最高位")
# 且 EMPTY/DELETED 要有一个"共同未置位"、而 SENTINEL 在该位上置位
check((~ABSL_EMPTY & ~ABSL_DELETED & ABSL_SENTINEL & 0x7F) != 0,
      "源码 static_assert：EMPTY 与 DELETED 共享一个未置位且 SENTINEL 在该位置位")

# ------------------------------------------------- 二、is_full 的两套写法
check(absl_is_full(0x00) and absl_is_full(0x7F), "Abseil：int8 非负即 full")
check(not absl_is_full(0x80) and not absl_is_full(0xFF), "Abseil：置最高位即 special")
check(hb_is_full(0x00) and hb_is_full(0x7F), "hashbrown：b & 0x80 == 0 即 full")
check(not hb_is_full(0x80) and not hb_is_full(0xFF), "hashbrown：置最高位即 special")
for b in range(256):
    check(absl_is_full(b) == hb_is_full(b), f"两派对 0x{b:02X} 的 full 判断必须一致")
check(absl_is_empty(0x80) and hb_is_deleted(0x80), "0x80：Abseil 说 EMPTY，hashbrown 说 DELETED")
check(absl_is_deleted(0xFE) and not hb_is_empty(0xFE) and not hb_is_deleted(0xFE),
      "0xFE：Abseil 说 DELETED，hashbrown 说是 full 数据位")
check(hb_special_is_empty(0xFF), "hashbrown：EMPTY = 0xFF，最低位为 1")
check(not hb_special_is_empty(0x80), "hashbrown：DELETED = 0x80，最低位为 0")
check(hb_is_empty(0xFF) and hb_is_deleted(0x80), "hashbrown 的两个判定")
# Abseil 的 IsEmptyOrDeleted 只看符号位，比 hashbrown 的查表更省
check(absl_is_empty_or_deleted(0x80) and absl_is_empty_or_deleted(0xFE)
      and absl_is_empty_or_deleted(0xFF), "Abseil：int8 < 0 即 EMPTY 或 DELETED")

# ------------------------------------------------------- 三、哈希切分
check(absl_h1(0x1234) == 0x1234, "Abseil H1 就是 hash 本身（低位当偏移）")
check(absl_h2(0xFF00000000000000) == 0x7F, "Abseil H2 = 最高 7 位")
check(absl_h2(MASK64) == 0x7F, "全 1 的 H2 是 0x7F")
check(absl_h2(1 << 57) == 0x01, "第 57 位是 H2 的最低位（H2 = hash >> 57）")
check(hb_h1(0x1234) == 0x1234, "hashbrown h1 = hash as usize，同样用低位")
check(hb_tag_full(0xFF00000000000000) == 0x7F, "hashbrown Tag::full 也是最高 7 位")
check(hb_tag_full(MASK64) == 0x7F, "全 1 的 tag 是 0x7F")
# 32 位平台上 MIN_HASH_LEN = 4 ⇒ 位移 4*8-7 = 25
check(hb_tag_full(0xFE000000, min_hash_len=4) == 0x7F,
      "32 位：Tag::full 取 hash >> 25（FxHash 返回 usize 的场景）")
# 三派对比：Abseil/hashbrown 取高位，Go 取低位 —— Go 是异类
check(absl_h2(MASK64) == hb_tag_full(MASK64), "Abseil 与 hashbrown 的 tag 一致（都取最高 7 位）")

# --------------------------------- 四、Abseil 容量体系：2^k − 1
check(is_valid_capacity(1) and is_valid_capacity(3) and is_valid_capacity(7)
      and is_valid_capacity(15) and is_valid_capacity(63), "1/3/7/15/63 是合法容量")
check(not is_valid_capacity(0), "0 不合法")
check(not is_valid_capacity(2) and not is_valid_capacity(4) and not is_valid_capacity(8),
      "2 的幂**不**是 Abseil 的合法容量")
check(countl_zero(0) == 64 and countl_zero(1) == 63 and countl_zero(1 << 63) == 0, "countl_zero")
for n, want in [(1, 1), (2, 3), (3, 3), (4, 7), (5, 7), (7, 7), (8, 15), (9, 15),
                (16, 31), (100, 127), (1000, 1023)]:
    check(normalize_capacity(n) == want, f"NormalizeCapacity({n}) 应为 {want}，实得 {normalize_capacity(n)}")
for n in (1, 3, 7, 15, 63, 1023):
    check(next_capacity(n) == 2 * n + 1, f"NextCapacity({n}) = {2 * n + 1}")
    check(is_valid_capacity(next_capacity(n)), f"NextCapacity({n}) 仍是合法容量")
    check(previous_capacity(next_capacity(n)) == n, f"PreviousCapacity(NextCapacity({n})) == {n}")
check(max_capacity_for_load_factor_one(16) == 63, "kWidth=16 时是 16*4-1 = 63")
check(max_capacity_for_load_factor_one(8) == 31, "kWidth=8 时是 31")

# ------------------------------------- 五、CapacityToGrowth（7/8 与"留一格"）
# kWidth = 16：capacity < kWidth-1 = 15 时一个空槽都不留
check(capacity_to_growth(7) == 7, "capacity 7 < 15 ⇒ 不留空槽（整张表在一个组里）")
check(capacity_to_growth(15) == 14, "capacity 15 >= 15 ⇒ 留一个空槽")
check(capacity_to_growth(31) == 30, "31 -> 30")
check(capacity_to_growth(63) == 62, "63 -> 62")
check(capacity_to_growth(127) == 112, "127 > 63 ⇒ 127 - 127//8 = 127-15 = 112")
check(capacity_to_growth(1023) == 896, "1023 - 1023//8 = 1023-127 = 896")
for cap_ in (127, 255, 511, 1023, 4095):
    check(capacity_to_growth(cap_) == cap_ - cap_ // 8, f"{cap_} 走 7/8 分支")
# kWidth = 8 时门槛变成 31
check(capacity_to_growth(7, kwidth=ABSL_KWIDTH_GENERIC) == 6,
      "kWidth=8：capacity 7 >= 7 ⇒ 留一个空槽得 6")
check(capacity_to_growth(3, kwidth=ABSL_KWIDTH_GENERIC) == 3,
      "kWidth=8：capacity 3 < 7 ⇒ 不留")

# -------------------------------------------- 六、SizeToCapacity 自洽性
for size in list(range(1, 200)) + [1000, 5000, 20000]:
    cap_ = size_to_capacity(size)
    check(is_valid_capacity(cap_), f"SizeToCapacity({size}) = {cap_} 必须是合法容量")
    check(capacity_to_growth(cap_) >= size,
          f"SizeToCapacity({size}) = {cap_} 装不下 {size}")
check(size_to_capacity(0) == 0, "size 0 -> capacity 0")
check(size_to_capacity(1) == 1, "size 1 -> capacity 1")
check(size_to_capacity(7) == 7, "size 7 -> capacity 7")
check(size_to_capacity(8) == 15, "size 8 (>= kWidth/2 = 8) -> capacity 15")
check(size_to_capacity(14) == 15, "size 14 -> 15")
check(size_to_capacity(15) == 31, "size 15 -> 31")

# ----------------------------------- 七、hashbrown 容量体系：2^k 个桶
check(hb_capacity_to_buckets(1) == 4, "cap<15 且 min_cap=3 ⇒ 4 个桶（不建 2 桶表）")
check(hb_capacity_to_buckets(3) == 4, "cap 3 -> 4")
check(hb_capacity_to_buckets(7) == 8, "cap 7 -> 8")
check(hb_capacity_to_buckets(14) == 16, "cap 14 -> 16")
check(hb_capacity_to_buckets(15) == 32, "cap 15 >= 15 ⇒ 15*8/7 = 17 -> 32")
check(hb_capacity_to_buckets(28) == 32, "28*8/7 = 32 -> 32")
check(hb_capacity_to_buckets(100) == 128, "100*8/7 = 114 -> 128")
check(hb_capacity_to_buckets(1000) == 2048, "1142 -> 2048")
# 单字节元素在 WIDTH=16 时下限被抬到 14（避免 ctrl_align 造成的巨大浪费）
check(hb_capacity_to_buckets(1, width=16, elem_size=1) == 16,
      "WIDTH=16 且元素 1 字节 ⇒ min_cap 14 ⇒ 16 个桶")
check(hb_capacity_to_buckets(1, width=16, elem_size=8) == 4, "元素 8 字节 ⇒ min_cap 3")
check(hb_capacity_to_buckets(1, width=8, elem_size=1) == 8, "WIDTH=8 且元素 1 字节 ⇒ min_cap 7")
for b in (4, 8, 16, 32, 128, 2048):
    check(b & (b - 1) == 0, f"capacity_to_buckets 的结果 {b} 必须是 2 的幂")

# ------------------------------------- 八、bucket_mask_to_capacity 的 7/8
check(hb_bucket_mask_to_capacity(3) == 3, "4 桶 -> capacity 3（留一个空槽）")
check(hb_bucket_mask_to_capacity(7) == 7, "8 桶 -> capacity 7")
check(hb_bucket_mask_to_capacity(15) == 14, "16 桶 -> 16/8*7 = 14")
check(hb_bucket_mask_to_capacity(31) == 28, "32 桶 -> 28")
check(hb_bucket_mask_to_capacity(127) == 112, "128 桶 -> 112")
check(hb_bucket_mask_to_capacity(1023) == 896, "1024 桶 -> 896（与 Go 的 1024 槽表一致）")

# ---------------------------------------- 九、探测序列（两派都是三角推进）
# Abseil：步长 = kWidth = 16，掩码是 capacity（2^k - 1）
seq = list(absl_probe_seq(0, 127, kwidth=16))
check(seq == [0, 16, 48, 96, 32, 112, 80, 64],
      f"Abseil 三角推进（步长 16、掩码 127），实得 {seq}")
seq2 = list(absl_probe_seq(0, 127, kwidth=16, limit=8))
check(len(set(seq2)) == 8, "8 个组，组起点互不相同")
# hashbrown：stride 从 0 起，每次 +WIDTH
seqh = list(hb_probe_seq(0, 127, width=16, limit=8))
check(seqh == seq2, f"两派的组起点序列完全一致：{seqh} vs {seq2}")
for buckets in (16, 32, 64, 128):
    mask = buckets - 1
    groups = buckets // 16
    s = list(hb_probe_seq(0, mask, width=16, limit=groups))
    check(len(set(s)) == groups, f"{buckets} 桶 / {groups} 组：三角探测遍历每组一次")
    check(all(x % 16 == 0 for x in s), "组起点必然是 16 的倍数")
# 换成 kWidth=8 时步长也跟着变
seq8 = list(absl_probe_seq(0, 127, kwidth=8, limit=4))
check(seq8 == [0, 8, 24, 48], f"kWidth=8 的推进步长是 8，实得 {seq8}")

# --------------------------------- 十、Go 与两派：容量形状与 h2 方向
# Go：capacity 是 2^k（无 sentinel），h2 取**低** 7 位
go_h2 = lambda h: h & 0x7F              # noqa: E731  （对应 662 的 h2）
check(go_h2(MASK64) == 0x7F, "Go 的 h2 取低 7 位，全 1 也得到 0x7F（与两派数值巧合相同）")
check(go_h2(0x80) == 0x00 and absl_h2(0x80) == 0x00, "小 hash 下三者可能相同，看不出方向差异")
check(go_h2(1 << 57) == 0x00 and absl_h2(1 << 57) == 0x01,
      "**第 57 位**：Abseil 认它是 tag 的最低位，Go 完全看不见它")
check(go_h2(0x7F) == 0x7F and absl_h2(0x7F) == 0x00,
      "**低 7 位**：Go 全盘接收，Abseil 一点都不用")

print(f"OK: {PASS} assertions passed")
sys.exit(0)
