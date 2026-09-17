#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go 1.24 size class 表与纯函数（数据模块）。

数据逐行取自官方源码 `src/runtime/sizeclasses.go`（Go 1.24.0）的注释表与数组；
`roundupsize` 逻辑取自 `src/runtime/msize.go`。由 `inject_tables.py` 从官方源码
生成/复核，避免手工抄录 68×5 项常量出错。

被 python/main.py 以 `from go_sizeclasses import *` 使用。
"""

PAGE_SIZE = 8192
SMALL_SIZE_DIV = 8          # sizeclasses.go: smallSizeDiv
SMALL_SIZE_MAX = 1024       # sizeclasses.go: smallSizeMax
LARGE_SIZE_DIV = 128        # sizeclasses.go: largeSizeDiv
MAX_SMALL_SIZE = 32768      # gc.MaxSmallSize
TINY_SIZE = 16              # gc.TinySize（malloc.go: maxTinySize）
TINY_SIZE_CLASS = 2         # gc.TinySizeClass；源码自断言 SizeClassToSize[2] == 16
# msize.go 的小对象判据是 `reqSize <= maxSmallSize - gc.MallocHeaderSize`。
# 该常量定义在 internal/runtime/gc 包内；本模型只覆盖 noscan 路径（不加该偏移），
# 故这里把边界显式建模 —— noscan 路径下 32761..32768 的结果与边界取值无关。
MALLOC_HEADER_SIZE = 8
MAX_SMALL_REQUEST = MAX_SMALL_SIZE - MALLOC_HEADER_SIZE

# ---------------------------------------------------------------- 官方表数据
# class_to_size：索引即 size class，class 0 未使用（0 字节）
CLASS_TO_SIZE = [
    0, 8, 16, 24, 32, 48, 64, 80, 96, 112,
    128, 144, 160, 176, 192, 208, 224, 240, 256, 288,
    320, 352, 384, 416, 448, 480, 512, 576, 640, 704,
    768, 896, 1024, 1152, 1280, 1408, 1536, 1792, 2048, 2304,
    2688, 3072, 3200, 3456, 4096, 4864, 5376, 6144, 6528, 6784,
    6912, 8192, 9472, 9728, 10240, 10880, 12288, 13568, 14336, 16384,
    18432, 19072, 20480, 21760, 24576, 27264, 28672, 32768,
]
# class_to_allocnpages：每个 span 占几个 8 KB 页
CLASS_TO_ALLOCNPAGES = [
    0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2,
    1, 2, 1, 2, 1, 3, 2, 3, 1, 3, 2, 3, 4, 5, 6, 1, 7, 6,
    5, 4, 3, 5, 7, 2, 9, 7, 5, 8, 3, 10, 7, 4,
]
# 官方注释表的五个可核验列（class 1..67）
OFFICIAL_OBJECTS = [
    1024, 512, 341, 256, 170, 128, 102, 85, 73, 64,
    56, 51, 46, 42, 39, 36, 34, 32, 28, 25,
    23, 21, 19, 18, 17, 16, 14, 12, 11, 10,
    9, 8, 7, 6, 11, 5, 9, 4, 7, 3,
    8, 5, 7, 2, 5, 3, 4, 5, 6, 7,
    1, 6, 5, 4, 3, 2, 3, 4, 1, 4,
    3, 2, 3, 1, 3, 2, 1,
]
OFFICIAL_TAIL_WASTE = [
    0, 0, 8, 0, 32, 0, 32, 32, 16, 0,
    128, 32, 96, 128, 80, 128, 32, 0, 128, 192,
    96, 128, 288, 128, 32, 0, 128, 512, 448, 512,
    128, 0, 128, 512, 896, 512, 256, 0, 256, 128,
    0, 384, 384, 0, 256, 256, 0, 128, 256, 768,
    0, 512, 512, 0, 128, 0, 256, 0, 0, 0,
    128, 0, 256, 0, 128, 0, 0,
]
# max waste 百分比放大 100 倍存整数（表中 87.50% → 8750），避免浮点等值断言
OFFICIAL_MAX_WASTE_BP = [
    8750, 4375, 2924, 2188, 3152, 2344, 1907, 1595, 1356, 1172,
    1182, 973, 959, 925, 812, 815, 662, 586, 1216, 1180,
    988, 951, 1071, 837, 682, 605, 1233, 1548, 1393, 1394,
    1552, 1240, 1241, 1555, 1400, 1400, 1557, 1245, 1246, 1559,
    1247, 622, 883, 1560, 1665, 1092, 1248, 623, 436, 337,
    1561, 1428, 364, 499, 624, 1145, 999, 535, 1249, 1111,
    357, 687, 625, 1145, 1000, 491, 1250,
]
# size_to_class128：长度 (32768-1024)/128 + 1 = 249
OFFICIAL_SIZE_TO_CLASS128 = [
    32, 33, 34, 35, 36, 37, 37, 38, 38, 39, 39, 40, 40, 40, 41, 41, 41, 42, 43, 43, 44,
    44, 44, 44, 44, 45, 45, 45, 45, 45, 45, 46, 46, 46, 46, 47, 47, 47, 47, 47, 47, 48,
    48, 48, 49, 49, 50, 51, 51, 51, 51, 51, 51, 51, 51, 51, 51, 52, 52, 52, 52, 52, 52,
    52, 52, 52, 52, 53, 53, 54, 54, 54, 54, 55, 55, 55, 55, 55, 56, 56, 56, 56, 56, 56,
    56, 56, 56, 56, 56, 57, 57, 57, 57, 57, 57, 57, 57, 57, 57, 58, 58, 58, 58, 58, 58,
    59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 60, 60, 60, 60, 60,
    60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 61, 61, 61, 61, 61, 62, 62, 62, 62, 62,
    62, 62, 62, 62, 62, 62, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 64, 64, 64, 64, 64,
    64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 65, 65, 65, 65,
    65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 66, 66, 66, 66,
    66, 66, 66, 66, 66, 66, 66, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67,
    67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67,
]


def build_size_to_class(table_size, div, base):
    """由 class_to_size 反推「字节数 → class」表（与 mksizeclasses.go 同逻辑）。"""
    out = []
    for i in range(table_size // div + 1):
        want = base + i * div
        cls = 1
        while CLASS_TO_SIZE[cls] < want:
            cls += 1
        out.append(cls)
    return out


SIZE_TO_CLASS8 = build_size_to_class(SMALL_SIZE_MAX, SMALL_SIZE_DIV, 0)
SIZE_TO_CLASS128 = build_size_to_class(MAX_SMALL_SIZE - SMALL_SIZE_MAX, LARGE_SIZE_DIV,
                                       SMALL_SIZE_MAX)


def size_class_of(blk):
    """blk 字节落在哪个 size class；大对象（页对齐）返回 -1。"""
    try:
        return CLASS_TO_SIZE.index(blk)
    except ValueError:
        return -1


def roundupsize(size):
    """复刻 runtime/msize.go 的 noscan 小对象路径；大对象按页取整。"""
    if size <= MAX_SMALL_REQUEST:
        if size <= SMALL_SIZE_MAX - SMALL_SIZE_DIV:
            return CLASS_TO_SIZE[SIZE_TO_CLASS8[div_round_up(size, SMALL_SIZE_DIV)]]
        return CLASS_TO_SIZE[SIZE_TO_CLASS128[div_round_up(size - SMALL_SIZE_MAX,
                                                          LARGE_SIZE_DIV)]]
    return div_round_up(size, PAGE_SIZE) * PAGE_SIZE
