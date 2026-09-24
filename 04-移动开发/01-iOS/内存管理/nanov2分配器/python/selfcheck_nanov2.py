"""Nano V2 分配器模型的自检(实跑)。

期望值对照 libmalloc@main 的 nano_zone_common.h / nanov2_zone.h /
nanov2_malloc.c 逐条手算过。
"""

from nanov2_const import (NANO_MAX_SIZE, NANO_REGIME_QUANTA_SIZE, NANO_SIZE_CLASSES,
                          BLOCK_SIZE, ARENA_SIZE, REGION_SIZE, BLOCKS_PER_ARENA,
                          ARENAS_PER_REGION, MAX_SLOTS_PER_BLOCK, BLOCKS_PER_UNIT,
                          TOTAL_BLOCK_UNITS, MAX_CURRENT_BLOCKS,
                          BLOCK_UNITS_BY_SIZE_CLASS, SLOT_NULL, SLOT_GUARD, SLOT_BUMP,
                          SLOT_FULL, SLOT_CAN_MADVISE, SLOT_MADVISING, SLOT_MADVISED,
                          NEXT_SLOT_VALID_MASK, nano_common_good_size,
                          size_class_from_size, size_from_size_class,
                          slots_by_size_class, block_units_to_arena_tables)
from nanov2_model import (Layout, block_index_to_meta_index, meta_index_to_block_index,
                          Block, Arena, allocation_block_index)

_P = [0]
_F = []


def ok(c, m):
    if c:
        _P[0] += 1
    else:
        _F.append(m)
        print("FAIL:", m)


def eq(a, b, m):
    ok(a == b, "%s (got %r want %r)" % (m, a, b))


# ---------- 尺寸类 ----------
eq(nano_common_good_size(0), 16, "good_size(0) 兜到 16")
eq(nano_common_good_size(1), 16, "good_size(1) 兜到 16")
eq(nano_common_good_size(16), 16, "good_size(16) 不变")
eq(nano_common_good_size(17), 32, "good_size(17) 抬到 32")
eq(nano_common_good_size(255), 256, "good_size(255) 抬到 256")
eq(nano_common_good_size(256), 256, "good_size(256) 不变")
eq(size_class_from_size(1), 0, "size=1 落在第 0 类")
eq(size_class_from_size(16), 0, "size=16 落在第 0 类")
eq(size_class_from_size(17), 1, "size=17 落在第 1 类")
eq(size_class_from_size(256), 15, "size=256 落在第 15 类")
eq(size_from_size_class(0), 16, "第 0 类是 16 字节")
eq(size_from_size_class(15), 256, "第 15 类是 256 字节")
eq(size_from_size_class(15), NANO_MAX_SIZE, "最大尺寸类就是 NANO_MAX_SIZE")

# ---------- 几何 ----------
eq(BLOCK_SIZE, 16384, "块大小 16KB")
eq(BLOCKS_PER_ARENA, 4096, "每 arena 4096 个块")
eq(ARENAS_PER_REGION, 8, "每 region 8 个 arena")
eq(MAX_SLOTS_PER_BLOCK, 1024, "16 字节类的块有 1024 个槽")
eq(TOTAL_BLOCK_UNITS, 64, "一个 arena 共 64 个块单元")
eq(sum(BLOCK_UNITS_BY_SIZE_CLASS), 64, "block_units 表总和是 64")
eq(len(BLOCK_UNITS_BY_SIZE_CLASS), NANO_SIZE_CLASSES, "block_units 表长度是尺寸类数")

# ---------- 槽位与浪费 ----------
want_slots = [1024, 512, 341, 256, 204, 170, 146, 128, 113, 102, 93, 85, 78, 73, 68, 64]
got_slots = [slots_by_size_class(i) for i in range(NANO_SIZE_CLASSES)]
eq(got_slots, want_slots, "各尺寸类的块内槽位数")
want_waste = [0, 0, 16, 0, 64, 64, 32, 0, 112, 64, 16, 64, 160, 32, 64, 0]
got_waste = [BLOCK_SIZE - slots_by_size_class(i) * size_from_size_class(i)
             for i in range(NANO_SIZE_CLASSES)]
eq(got_waste, want_waste, "块内因除不尽而浪费的字节数")
eq(got_slots[2] * size_from_size_class(2) + got_waste[2], BLOCK_SIZE,
   "槽位占用加浪费等于整块")

# ---------- arena 两张映射表 ----------
first, last, ptr_offset = block_units_to_arena_tables()
eq(first[0], 1, "第 0 类从块 1 开始(块 0 是元数据块)")
eq(last[0], 127, "第 0 类到块 127 为止")
eq(first[1], 128, "第 1 类从块 128 开始")
eq(last[15], BLOCKS_PER_ARENA - 1, "最后一类到块 4095 为止")
eq(len(ptr_offset), TOTAL_BLOCK_UNITS, "ptr_offset_to_size_class 共 64 项")
eq(ptr_offset[0], 0, "第 0 个单元属于第 0 类")
eq(ptr_offset[1], 0, "第 1 个单元也属于第 0 类(该类占 2 单元)")
eq(ptr_offset[2], 1, "第 2 个单元开始属于第 1 类")
eq(ptr_offset[63], 15, "最后一个单元属于第 15 类")
ok(all(last[i] == first[i] + BLOCK_UNITS_BY_SIZE_CLASS[i] * BLOCKS_PER_UNIT - 1
       for i in range(1, NANO_SIZE_CLASSES)), "last 等于 first 加单元数乘 64 减 1")
eq(last[0], first[0] + BLOCK_UNITS_BY_SIZE_CLASS[0] * BLOCKS_PER_UNIT - 2,
   "第 0 类还要再扣掉块 0(元数据块)")

# ---------- 地址位域 ----------
ios = Layout(ios=True, signature=0x6)
eq(ios.region_bits, 0, "iOS 变体没有 region 位")
eq(ios.signature_bits, 35, "iOS 变体签名占 35 位")
eq(ios.block_shift, 14, "block 位从第 14 位起")
eq(ios.arena_shift, 26, "arena 位从第 26 位起")
eq(ios.region_shift, 29, "签名位从第 29 位起")
a = ios.encode(offset=0x30, block=5, arena=2)
d = ios.decode(a)
eq(d["offset"], 0x30, "编码后 offset 可还原")
eq(d["block"], 5, "编码后 block 可还原")
eq(d["arena"], 2, "编码后 arena 可还原")
eq(d["signature"], 0x6, "编码后签名可还原")
eq(ios.has_valid_signature(a), True, "签名校验通过")
eq(ios.has_valid_signature(a ^ (1 << 40)), False, "改一位签名就校验失败")
osx = Layout(ios=False, signature=0x6)
eq(osx.region_bits, 15, "macOS 变体有 15 位 region")
eq(osx.signature_bits, 20, "macOS 变体签名占 20 位")
b = osx.encode(offset=0x10, block=1, arena=3, region=7)
eq(osx.decode(b)["region"], 7, "macOS 变体 region 可还原")
eq((0x6 << 44), 0x600000000000, "macOS 的 nano 基址 = 签名左移 44 位")

# ---------- 块号与元数据下标 ----------
eq(block_index_to_meta_index(0), 0, "0 的映射仍是 0")
eq(block_index_to_meta_index(1), 64, "块 1 的元数据在下标 64")
eq(block_index_to_meta_index(64), 1, "高低 6 位互换")
eq(block_index_to_meta_index(4095), 4095, "全 1 映射到自身")
ok(all(block_index_to_meta_index(block_index_to_meta_index(i)) == i for i in range(4096)),
   "映射是对合:做两次回到原值")
eq(block_index_to_meta_index(0x3FF), meta_index_to_block_index(0x3FF),
   "两个方向用的是同一个公式")

# ---------- 块状态:bump 到满 ----------
blk = Block(0)
eq(blk.slots, 1024, "16 字节类一个块 1024 个槽")
eq(blk.free_count, 1023, "新块 free_count 是 slots-1")
eq(blk.next_slot, SLOT_BUMP, "新块 next_slot 是 SLOT_BUMP")
eq(blk.can_allocate_from(), True, "新块可分配")
s0 = blk.allocate()
eq(s0, 0, "第一次 bump 取槽 0")
eq(blk.free_count, 1022, "分配后 free_count 减 1")
eq(blk.next_slot, SLOT_BUMP, "未取完时 next_slot 仍是 SLOT_BUMP")
s1 = blk.allocate()
eq(s1, 1, "第二次 bump 取槽 1")
for _ in range(1022):
    blk.allocate()
eq(blk.next_slot, SLOT_FULL, "取完最后一格后 next_slot 变成 SLOT_FULL")
eq(blk.free_count, 1023, "满块的 free_count 是 -1 回绕成 0x3FF")
eq(blk.can_allocate_from(), False, "满块不可再分配")
eq(blk.allocate(), None, "满块分配返回 None")
eq(blk.allocated_count(), 1024, "满块已分出 1024 个槽")

# ---------- 释放:从满块回到可分配 ----------
madv = blk.free(7)
eq(blk.next_slot, 8, "释放后 next_slot 指向该槽(1-based)")
eq(blk.slot_next[7], SLOT_BUMP, "满块释放时该槽的 next 写成 SLOT_BUMP")
eq(blk.free_count, 0, "满块释放一个后 free_count 从 0x3FF 加 1 回绕到 0")
eq(madv, False, "块仍 in_use,不触发 madvise")
eq(blk.allocate(), 7, "接下来从空闲链表拿到刚释放的槽 7")
eq(blk.next_slot, SLOT_FULL, "拿掉唯一空闲槽后又变成满块")

# ---------- 释放:链表串接 ----------
b2 = Block(3)   # 64 字节类,256 个槽
eq(b2.slots, 256, "64 字节类一个块 256 个槽")
eq(b2.free_count, 255, "新块 free_count = 255")
for _ in range(256):
    b2.allocate()
eq(b2.next_slot, SLOT_FULL, "填满")
b2.free(10)
b2.free(20)
eq(b2.slot_next[20], 11, "后释放的槽指向前一次释放的槽")
eq(b2.free_count, 1, "两个空闲槽时 free_count 是 1")
eq(b2.allocate(), 20, "后释放的先被分配(LIFO)")
eq(b2.allocate(), 10, "再分配先释放的那个")
eq(b2.next_slot, SLOT_FULL, "两个都拿完又满了")

# ---------- 释放最后一个活跃槽 ----------
b3 = Block(7)   # 128 字节类,128 槽
b3.allocate()
eq(b3.free_count, 126, "分出一格后 free_count = slots-2")
eq(b3.free(0), False, "块仍 in_use,放空后不触发 madvise")
eq(b3.next_slot, SLOT_BUMP, "放掉最后一个活跃槽后回到 SLOT_BUMP")
eq(b3.free_count, 127, "此时 free_count 回到 slots-1")

# 从满块释放第一格不会走 free_last_active 分支(was_full 把它排除了)
b3b = Block(7)
for _ in range(128):
    b3b.allocate()
eq(b3b.next_slot, SLOT_FULL, "填满 128 字节类的块")
eq(b3b.free(0), False, "满块的第一格释放不走放空分支")
eq(b3b.next_slot, 1, "满块释放后 next_slot 直接指向该槽")
eq(b3b.free_count, 0, "满块释放第一格后 free_count 回绕到 0")

b4 = Block(7)
b4.allocate()
b4.in_use = False
eq(b4.free(0), True, "块已停用且被放空 -> 返回 True 表示可 madvise")
eq(b4.next_slot, SLOT_CAN_MADVISE, "停用的空块进入 SLOT_CAN_MADVISE")
eq(b4.is_active(), True, "CAN_MADVISE 的块仍算活跃")
b4.next_slot = SLOT_MADVISED
eq(b4.is_active(), False, "MADVISED 的块不算活跃")
b4.next_slot = SLOT_GUARD
eq(b4.is_active(), False, "GUARD 块不算活跃")

# ---------- 双释放哨兵 ----------
b5 = Block(0)
b5.allocate()
b5.allocate()
b5.free(0)
r = b5.allocate()
eq(r, 0, "从空闲链表取回槽 0(哨兵正确)")
b5.slot_guard[1] = False
b5.next_slot = 2
res = b5.allocate()
eq(res, ("corrupt", 1), "哨兵被抹掉时会报 corruption")

# ---------- ASLR 混淆与尺寸类反查 ----------
arena = Arena(aslr_cookie=0x123)
eq(arena.first_block_for_size_class(0), 1 ^ 0x123, "首块号与 aslr cookie 异或")
eq(arena.size_class_for_block(1 ^ 0x123), 0, "解扰后能反查回第 0 类")
eq(arena.size_class_for_block(128 ^ 0x123), 1, "第 1 类的首块反查正确")
eq(arena.size_class_for_block(3968 ^ 0x123), 15, "第 15 类的首块反查正确")
eq(arena.next_block_for_size_class(0, (126) ^ 0x123), (127) ^ 0x123,
   "next_block 在同类中前进一格")
eq(arena.next_block_for_size_class(0, (127) ^ 0x123), None,
   "走到 last 之后返回 None")

# ---------- CPU 索引 ----------
eq(allocation_block_index(0), 0, "CPU 0 用第 0 个当前块")
eq(allocation_block_index(63), 63, "CPU 63 用第 63 个当前块")
eq(allocation_block_index(64), 0, "CPU 64 回绕到第 0 个")
eq(MAX_CURRENT_BLOCKS, 64, "每个尺寸类最多 64 个当前块")

print("assertions passed: %d, failed: %d" % (_P[0], len(_F)))
if _F:
    raise SystemExit(1)
