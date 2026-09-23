"""Swiss table 的两派实现：Abseil `raw_hash_set` 与 Rust `hashbrown`。

对照的是这两份原文：

- `abseil-cpp/absl/container/internal/hashtable_control_bytes.h`：
  `ctrl_t{kEmpty=-128, kDeleted=-2, kSentinel=-1}`、`Group::kWidth = 16`（SSE2）/ 8（generic）；
- `abseil-cpp/absl/container/internal/raw_hash_set.h`：
  `H1(hash) = hash`、`H2(hash) = hash >> 57`、`IsValidCapacity`、`NormalizeCapacity`、
  `NextCapacity = n*2+1`、`kMaxCapacityForLoadFactorOne = kWidth*4-1`、
  `CapacityToGrowth`、`SizeToCapacity`、`probe_seq`（步长 = Width）；
- `rust-lang/hashbrown/src/control/tag.rs`：`Tag::EMPTY = 0xFF`、`Tag::DELETED = 0x80`、
  `is_full = b & 0x80 == 0`、`special_is_empty = b & 0x01 != 0`、`Tag::full(hash)` 取最高 7 位；
- `rust-lang/hashbrown/src/raw.rs`：`capacity_to_buckets`、`bucket_mask_to_capacity`、
  `ProbeSeq`（`stride += Group::WIDTH`）、`h1(hash) = hash as usize`。

一句话概括差异：**Abseil 的容量是 2^k − 1（多一个 sentinel 槽），hashbrown 的桶数是 2^k；
两者的 EMPTY / DELETED 哨兵字节正好互换。**
"""

from __future__ import annotations

MASK64 = (1 << 64) - 1

# ------------------------------------------------------------------ Abseil
ABSL_KWIDTH_SSE2 = 16
ABSL_KWIDTH_GENERIC = 8

ABSL_EMPTY = -128          # 0x80
ABSL_DELETED = -2          # 0xFE
ABSL_SENTINEL = -1         # 0xFF

# ---------------------------------------------------------------- hashbrown
HB_EMPTY = 0xFF
HB_DELETED = 0x80


def as_i8(b: int) -> int:
    """把 0..255 折成 C++ `int8_t`（有符号）。"""
    b &= 0xFF
    return b - 256 if b >= 128 else b


def as_u8(b: int) -> int:
    return b & 0xFF


# ------------------------------------------------------------- 哈希切分
def absl_h1(h: int, bits: int = 64) -> int:
    """`inline size_t H1(size_t hash) { return hash; }` —— 直接用低位当起始偏移。"""
    return h & (1 << bits) - 1


def absl_h2(h: int, bits: int = 64) -> int:
    """`H2(hash) = hash >> (sizeof(size_t)*8 - 7)` —— **最高** 7 位。"""
    return (h & (1 << bits) - 1) >> (bits - 7)


def hb_h1(h: int) -> int:
    """`fn h1(hash: u64) -> usize { hash as usize }` —— 同样是低位。"""
    return h & MASK64


def hb_tag_full(h: int, min_hash_len: int = 8) -> int:
    """`Tag::full`：`(hash >> (MIN_HASH_LEN*8 - 7)) & 0x7f` —— 也是**最高** 7 位。

    `MIN_HASH_LEN = min(size_of::<usize>(), size_of::<u64>())`，64 位平台是 8（字节），
    所以位移量是 8*8-7 = 57；32 位平台上是 4*8-7 = 25（因为 FxHash 之类返回 usize）。
    """
    return ((h & MASK64) >> (min_hash_len * 8 - 7)) & 0x7F


# ------------------------------------------------- Abseil 容量体系（2^k - 1）
def countl_zero(n: int, bits: int = 64) -> int:
    n &= (1 << bits) - 1
    return bits if n == 0 else bits - n.bit_length()


def is_valid_capacity(n: int) -> bool:
    """`((n + 1) & n) == 0 && n > 0` —— 合法容量形如 1, 3, 7, 15, …"""
    return ((n + 1) & n) == 0 and n > 0


def normalize_capacity(n: int, bits: int = 64) -> int:
    """`n ? ~size_t{} >> countl_zero(n) : 1` —— 向上取到 2^k − 1。"""
    if n == 0:
        return 1
    return ((1 << bits) - 1) >> countl_zero(n, bits)


def next_capacity(n: int) -> int:
    """`n * 2 + 1` —— 2^k − 1 的"下一个"仍然是 2^(k+1) − 1。"""
    return n * 2 + 1


def previous_capacity(n: int) -> int:
    return n // 2


def max_capacity_for_load_factor_one(kwidth: int = ABSL_KWIDTH_SSE2) -> int:
    """`Group::kWidth * 4 - 1`：kWidth=16 时是 63。"""
    return kwidth * 4 - 1


def capacity_to_growth(capacity: int, kwidth: int = ABSL_KWIDTH_SSE2) -> int:
    """`CapacityToGrowth`：这张 2^k−1 容量的表最多能装多少。

    小容量（<= kWidth*4-1）只留**一个**空槽（且 `capacity >= kWidth-1` 才留）；
    大容量按 7/8（`capacity - capacity/8`）。
    """
    if capacity <= max_capacity_for_load_factor_one(kwidth):
        return capacity - (1 if capacity >= kwidth - 1 else 0)
    return capacity - capacity // 8


def size_to_capacity(size: int, kwidth: int = ABSL_KWIDTH_SSE2, bits: int = 64) -> int:
    """`SizeToCapacity`：`USABLE` 的反函数，给 size 算一张不用再扩的表。"""
    if size == 0:
        return 0
    leading_zeros = countl_zero(size + (1 if size >= kwidth // 2 else 0), bits)
    if size < max_capacity_for_load_factor_one(kwidth):
        return ((1 << bits) - 1) >> leading_zeros
    k_last3 = 7 << (bits - 3)
    max_size_for_next = k_last3 >> leading_zeros
    leading_zeros -= 1 if size > max_size_for_next else 0
    return ((1 << bits) - 1) >> leading_zeros


def absl_probe_seq(h1_value: int, capacity: int, kwidth: int = ABSL_KWIDTH_SSE2,
                   limit: int | None = None):
    """`probe_seq`：offset 以 **槽** 为单位，步长是 `Width`（16），落在 `[0, capacity]`。

    源码：
        offset_ = h1 & capacity
        next(): index_ += Width; offset_ += index_; offset_ &= capacity
    注意 `capacity` 在这里当**掩码**用（容量就是 2^k−1），所以 `& capacity` 就是 mod 2^k。
    """
    n = (capacity + 1) // kwidth if limit is None else limit
    offset = h1_value & capacity
    index = 0
    for _ in range(n):
        yield offset
        index += kwidth
        offset = (offset + index) & capacity


# ------------------------------------------- hashbrown 容量体系（2^k 个桶）
def hb_capacity_to_buckets(cap: int, width: int = 16, elem_size: int = 8) -> int:
    """`capacity_to_buckets`：小表按 `(Group::WIDTH, size)` 查一张下限表，大表 `cap*8/7` 取幂。"""
    if cap < 15:
        if width == 16 and elem_size <= 1:
            min_cap = 14
        elif (width == 16 and elem_size <= 3) or (width == 8 and elem_size <= 1):
            min_cap = 7
        else:
            min_cap = 3
        cap = max(min_cap, cap)
        if cap < 4:
            return 4
        if cap < 8:
            return 8
        return 16
    adjusted = (cap * 8) // 7
    return 1 << (adjusted - 1).bit_length() if adjusted > 1 else 1


def hb_bucket_mask_to_capacity(bucket_mask: int) -> int:
    """`bucket_mask_to_capacity`：桶数 ≤ 8 时capacity = 桶数−1，大表按 7/8。"""
    if bucket_mask < 8:
        return bucket_mask
    return ((bucket_mask + 1) // 8) * 7


def hb_probe_seq(h1_value: int, bucket_mask: int, width: int = 16,
                 limit: int | None = None):
    """`ProbeSeq`：pos 以槽为单位，`stride` 每次加 `Group::WIDTH`。"""
    n = (bucket_mask + 1) // width if limit is None else limit
    pos = h1_value & bucket_mask
    stride = 0
    for _ in range(n):
        yield pos
        stride = (stride + width) & MASK64
        pos = (pos + stride) & bucket_mask


# ------------------------------------------------------------ 语义差异表
def absl_is_full(ctrl: int) -> bool:
    """Abseil：`full` 是 `int8_t` 非负（最高位为 0）。"""
    return as_i8(ctrl) >= 0


def absl_is_empty(ctrl: int) -> bool:
    return as_i8(ctrl) == ABSL_EMPTY


def absl_is_deleted(ctrl: int) -> bool:
    return as_i8(ctrl) == ABSL_DELETED


def absl_is_empty_or_deleted(ctrl: int) -> bool:
    return as_i8(ctrl) < 0


def hb_is_full(tag: int) -> bool:
    """hashbrown：`self.0 & 0x80 == 0`。"""
    return as_u8(tag) & 0x80 == 0


def hb_is_special(tag: int) -> bool:
    return as_u8(tag) & 0x80 != 0


def hb_special_is_empty(tag: int) -> bool:
    """`self.0 & 0x01 != 0` —— 只看**最低**一位。"""
    return as_u8(tag) & 0x01 != 0


def hb_is_empty(tag: int) -> bool:
    return as_u8(tag) == HB_EMPTY


def hb_is_deleted(tag: int) -> bool:
    return as_u8(tag) == HB_DELETED
