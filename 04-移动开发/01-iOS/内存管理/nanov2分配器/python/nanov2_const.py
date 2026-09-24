"""常量与尺寸类表:来自 libmalloc 的 nano_zone_common.h / nanov2_zone.h / nanov2_malloc.c。"""

# ---- nano_zone_common.h ----
NANO_MAX_SIZE = 256                  # 桶大小 {16, 32, ..., 256}
SHIFT_NANO_QUANTUM = 4
NANO_REGIME_QUANTA_SIZE = 1 << SHIFT_NANO_QUANTUM   # 16
NANO_QUANTA_MASK = NANO_REGIME_QUANTA_SIZE - 1
NANO_SIZE_CLASSES = NANO_MAX_SIZE // NANO_REGIME_QUANTA_SIZE   # 16

# iOS 变体(MALLOC_TARGET_IOS)
SHIFT_NANO_SIGNATURE_IOS = 29
NANOZONE_SIGNATURE_BITS_IOS = 35
# 非 iOS 变体(TARGET_OS_OSX 等)
SHIFT_NANO_SIGNATURE_OSX = 44
NANOZONE_SIGNATURE_BITS_OSX = 20
NANOZONE_SIGNATURE_OSX = 0x6

# ---- nanov2_zone.h ----
OFFSET_BITS = 14
BLOCK_BITS = 12
ARENA_BITS = 3
REGION_BITS_OSX = 15
REGION_BITS_IOS = 0

BLOCK_SIZE = 1 << OFFSET_BITS                 # 16KB
ARENA_SIZE = 64 * 1024 * 1024                 # 64MB
REGION_SIZE = 512 * 1024 * 1024               # 512MB
BLOCKS_PER_ARENA = ARENA_SIZE // BLOCK_SIZE   # 4096
ARENAS_PER_REGION = REGION_SIZE // ARENA_SIZE  # 8
MAX_SLOTS_PER_BLOCK = BLOCK_SIZE // NANO_REGIME_QUANTA_SIZE   # 1024

# ---- nanov2_malloc.c ----
BLOCKS_PER_UNIT_SHIFT = 6
BLOCKS_PER_UNIT = 1 << BLOCKS_PER_UNIT_SHIFT  # 64
TOTAL_BLOCK_UNITS = BLOCKS_PER_ARENA // BLOCKS_PER_UNIT       # 64
MAX_CURRENT_BLOCKS = 64
MAX_CURRENT_BLOCKS_MASK = MAX_CURRENT_BLOCKS - 1

# 每个尺寸类在 arena 里占多少个"单元"(1 单元 = 64 个块),总和必须是 64
BLOCK_UNITS_BY_SIZE_CLASS = [2, 10, 11, 10, 5, 3, 3, 4, 3, 2, 2, 2, 2, 2, 1, 2]

# 块元数据里 next_slot 的特殊值
SLOT_NULL = 0x000
SLOT_GUARD = 0x7fa
SLOT_BUMP = 0x7fb
SLOT_FULL = 0x7fc
SLOT_CAN_MADVISE = 0x7fd
SLOT_MADVISING = 0x7fe
SLOT_MADVISED = 0x7ff

NEXT_SLOT_BITS = 11
NEXT_SLOT_VALID_MASK = 0x7FF
FREE_COUNT_BITS = 10
GEN_COUNT_BITS = 10


def nano_common_good_size(size):
    """_nano_common_good_size:向上取整到 16 的倍数,且至少 16。"""
    if size <= NANO_REGIME_QUANTA_SIZE:
        return NANO_REGIME_QUANTA_SIZE
    return ((size + NANO_REGIME_QUANTA_SIZE - 1) >> SHIFT_NANO_QUANTUM) << SHIFT_NANO_QUANTUM


def size_class_from_size(size):
    """nanov2_size_class_from_size:howmany(size, 16) - 1。"""
    return ((size + NANO_REGIME_QUANTA_SIZE - 1) // NANO_REGIME_QUANTA_SIZE) - 1


def size_from_size_class(sc):
    """nanov2_size_from_size_class:(sc + 1) * 16。"""
    return (sc + 1) * NANO_REGIME_QUANTA_SIZE


def slots_by_size_class(sc):
    """块内槽位数:块大小整除分配大小,余数即浪费。"""
    return BLOCK_SIZE // size_from_size_class(sc)


def block_units_to_arena_tables(units=BLOCK_UNITS_BY_SIZE_CLASS):
    """复刻 nanov2_configure_once 里两张表的构建。

    first/last 表:块 0 留给元数据块,故第 0 类的起始是 1 而不是 0。
    ptr_offset_to_size_class:把逻辑块号 >> 6(即"单元号")映射到尺寸类。
    """
    total = sum(u * BLOCKS_PER_UNIT for u in units)
    assert total == BLOCKS_PER_ARENA, "block_units 总和必须凑满一个 arena"
    first, last = [], []
    next_offset = 1
    first.append(next_offset)
    next_offset = units[0] * BLOCKS_PER_UNIT
    last.append(next_offset - 1)
    for i in range(1, NANO_SIZE_CLASSES):
        first.append(next_offset)
        next_offset += units[i] * BLOCKS_PER_UNIT
        last.append(next_offset - 1)
    assert next_offset == BLOCKS_PER_ARENA
    ptr_offset = []
    for i, u in enumerate(units):
        ptr_offset.extend([i] * u)
    assert len(ptr_offset) == TOTAL_BLOCK_UNITS
    return first, last, ptr_offset
