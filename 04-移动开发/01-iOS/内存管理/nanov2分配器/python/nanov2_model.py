"""Nano V2 分配器的地址布局与块分配状态机。

转写自 apple-oss-distributions/libmalloc@main:
  src/nano_zone_common.h   —— 尺寸类与 good_size
  src/nanov2_zone.h        —— 地址位域、块元数据位域、next_slot 特殊值
  src/nanov2_malloc.c      —— 两张映射表的构建、allocate/free 内联函数
"""

from nanov2_const import (NANO_SIZE_CLASSES, BLOCKS_PER_UNIT_SHIFT, BLOCKS_PER_UNIT,
                          MAX_CURRENT_BLOCKS_MASK, SLOT_NULL, SLOT_GUARD, SLOT_BUMP,
                          SLOT_FULL, SLOT_CAN_MADVISE, SLOT_MADVISING, SLOT_MADVISED,
                          NEXT_SLOT_VALID_MASK, size_class_from_size,
                          size_from_size_class, slots_by_size_class,
                          block_units_to_arena_tables)


class Layout:
    """nanov2_addr_s 的位域布局。iOS 变体没有 region 位。"""

    def __init__(self, ios=True, signature=0x6):
        self.ios = ios
        self.offset_bits = 14
        self.block_bits = 12
        self.arena_bits = 3
        self.region_bits = 0 if ios else 15
        self.signature_bits = 35 if ios else 20
        self.signature = signature

    @property
    def block_shift(self):
        return self.offset_bits

    @property
    def arena_shift(self):
        return self.offset_bits + self.block_bits

    @property
    def region_shift(self):
        return self.arena_shift + self.arena_bits

    def encode(self, offset, block, arena, region=0):
        a = offset | (block << self.block_shift) | (arena << self.arena_shift)
        if not self.ios:
            a |= region << self.region_shift
        return a | (self.signature << self.region_shift)

    def decode(self, addr):
        d = {
            "offset": addr & ((1 << self.offset_bits) - 1),
            "block": (addr >> self.block_shift) & ((1 << self.block_bits) - 1),
            "arena": (addr >> self.arena_shift) & ((1 << self.arena_bits) - 1),
        }
        if not self.ios:
            d["region"] = (addr >> self.region_shift) & ((1 << self.region_bits) - 1)
        d["signature"] = addr >> self.region_shift
        return d

    def has_valid_signature(self, addr):
        return (addr >> self.region_shift) == self.signature


def block_index_to_meta_index(i):
    """nanov2_block_index_to_meta_index:高低 6 位互换,是对合运算。"""
    return ((i >> 6) | (i << 6)) & 0xFFF


def meta_index_to_block_index(i):
    return ((i >> 6) | (i << 6)) & 0xFFF


class Block:
    """一个块的元数据 + 槽位空闲链表。

    free_count 的口径是"空闲槽位数 - 1"(10 位无符号,满块时为 -1 即 0x3FF),
    所以 slot_full 判据是 free_count == 0:表示"拿完这一格就一个都不剩"。
    """

    FREE_MASK = (1 << 10) - 1

    def __init__(self, size_class, in_use=True):
        self.size_class = size_class
        self.slots = slots_by_size_class(size_class)
        self.next_slot = SLOT_BUMP
        self.free_count = self.slots - 1
        self.gen_count = 0
        self.in_use = in_use
        self.slot_next = {}   # 槽位 -> 空闲链表上的下一个 next_slot
        self.slot_guard = {}  # 槽位 -> 是否写了 double_free_guard

    # ---- 判定 ----
    def is_active(self):
        return self.next_slot not in (SLOT_NULL, SLOT_MADVISING, SLOT_MADVISED, SLOT_GUARD)

    def can_allocate_from(self):
        return bool(self.in_use) and self.next_slot != SLOT_FULL

    def bits(self):
        return (self.next_slot | (self.free_count << 11) |
                (self.gen_count << 21) | ((1 if self.in_use else 0) << 31))

    def allocated_count(self):
        """已分出去的槽位数:slots - free_count - 1(满块时 free_count 回绕,需修正)。"""
        fc = self.free_count
        if self.next_slot == SLOT_FULL and fc == self.FREE_MASK:
            return self.slots
        return self.slots - fc - 1

    # ---- 分配 ----
    def allocate(self):
        """复刻 nanov2_allocate_from_block_inline,返回槽位下标或 None。"""
        if not self.can_allocate_from():
            return None
        slot_full = self.free_count == 0
        if self.next_slot == SLOT_BUMP or self.next_slot == SLOT_CAN_MADVISE:
            # 空闲链表为空,bump 取下一个从未用过的槽
            new_next = SLOT_FULL if slot_full else SLOT_BUMP
            slot = self.slots - self.free_count - 1
            from_free_list = False
        else:
            slot = self.next_slot - 1          # next_slot 是 1-based
            from_free_list = True
            new_next = SLOT_FULL if slot_full else self.slot_next.get(slot, SLOT_NULL)
        self.next_slot = new_next
        self.free_count = (self.free_count - 1) & self.FREE_MASK
        self.gen_count = (self.gen_count + 1) & self.FREE_MASK
        if from_free_list:
            # 校验 double_free_guard;校验必须在 cmpxchg 之后做
            if not self.slot_guard.get(slot, False):
                return ("corrupt", slot)
            self.slot_guard[slot] = False
        return slot

    # ---- 释放 ----
    def free(self, slot):
        """复刻 nanov2_free_to_block_inline,返回是否该块可以 madvise。"""
        was_full = self.next_slot == SLOT_FULL
        prev_next = self.next_slot            # 释放前的块头,要接到被释放槽位的后面
        new_free = (self.free_count + 1) & self.FREE_MASK
        self.slot_guard[slot] = True
        freeing_last_active = (not was_full) and new_free == self.slots - 1
        if freeing_last_active:
            self.slot_next[slot] = SLOT_NULL
            self.next_slot = SLOT_BUMP if self.in_use else SLOT_CAN_MADVISE
        else:
            self.slot_next[slot] = SLOT_BUMP if was_full else prev_next
            self.next_slot = slot + 1
        self.free_count = new_free
        self.gen_count = (self.gen_count + 1) & self.FREE_MASK
        return self.next_slot == SLOT_CAN_MADVISE


class Arena:
    """一个 arena:4096 个块,块 0 是元数据块。"""

    def __init__(self, aslr_cookie=0):
        self.first, self.last, self.ptr_offset = block_units_to_arena_tables()
        self.aslr_cookie = aslr_cookie

    def first_block_for_size_class(self, sc):
        return self.first[sc] ^ self.aslr_cookie

    def last_block_for_size_class(self, sc):
        return self.last[sc] ^ self.aslr_cookie

    def next_block_for_size_class(self, sc, block_index):
        """nanov2_next_block_for_size_class:解扰 -> 比较 -> 再扰。"""
        b = block_index ^ self.aslr_cookie
        if b == self.last[sc]:
            return None
        b = self.first[sc] if b == self.last[sc] else b + 1
        return b ^ self.aslr_cookie

    def size_class_for_block(self, block_index):
        """nanov2_size_class_for_ptr 的块号部分:解扰后 >> 6 查表。"""
        logical = block_index ^ self.aslr_cookie
        return self.ptr_offset[logical >> BLOCKS_PER_UNIT_SHIFT]


def allocation_block_index(cpu):
    """nanov2_get_allocation_block_index:默认按 CPU 号取模。"""
    return cpu & MAX_CURRENT_BLOCKS_MASK
