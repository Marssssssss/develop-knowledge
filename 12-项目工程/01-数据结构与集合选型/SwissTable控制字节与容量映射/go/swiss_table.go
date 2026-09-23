// Package main —— Swiss table 两派（Abseil / hashbrown）的控制字节与容量映射，Go 侧同口径实现。
//
// 常量逐条照抄 absl/container/internal/hashtable_control_bytes.h、
// raw_hash_set.h 与 rust-lang/hashbrown 的 src/control/tag.rs、src/raw.rs。
package main

const (
	abslKwidthSSE2    = 16
	abslKwidthGeneric = 8

	abslEmpty    = -128 // 0x80
	abslDeleted  = -2   // 0xFE
	abslSentinel = -1   // 0xFF

	hbEmpty   = 0xFF // hashbrown Tag::EMPTY
	hbDeleted = 0x80 // hashbrown Tag::DELETED
)

// asI8 把 0..255 折成 C++ int8_t。
func asI8(b int) int {
	b &= 0xFF
	if b >= 128 {
		return b - 256
	}
	return b
}

// asU8 取低 8 位。
func asU8(b int) int { return b & 0xFF }

// abslH1 复刻 `size_t H1(size_t hash) { return hash; }`：直接用低位当起始偏移。
func abslH1(h uint64) uint64 { return h }

// abslH2 复刻 `H2(hash) = hash >> 57`：**最高** 7 位。
func abslH2(h uint64) uint64 { return h >> (64 - 7) }

// hbH1 复刻 `fn h1(hash: u64) -> usize`：低位。
func hbH1(h uint64) uint64 { return h }

// hbTagFull 复刻 `Tag::full`：`(hash >> (MIN_HASH_LEN*8-7)) & 0x7f`。
func hbTagFull(h uint64, minHashLen int) uint64 {
	return (h >> uint(minHashLen*8-7)) & 0x7f
}

// countlZero 复刻 absl::countl_zero（64 位）。
func countlZero(n uint64) int {
	if n == 0 {
		return 64
	}
	return 64 - bits64Len(n)
}

func bits64Len(n uint64) int {
	k := 0
	for n > 0 {
		k++
		n >>= 1
	}
	return k
}

// isValidCapacity 复刻 `((n + 1) & n) == 0 && n > 0`。
func isValidCapacity(n uint64) bool { return ((n+1)&n) == 0 && n > 0 }

// normalizeCapacity 复刻 `n ? ~size_t{} >> countl_zero(n) : 1`：向上取到 2^k-1。
func normalizeCapacity(n uint64) uint64 {
	if n == 0 {
		return 1
	}
	return ^uint64(0) >> uint(countlZero(n))
}

// nextCapacity 复刻 `n * 2 + 1`。
func nextCapacity(n uint64) uint64 { return n*2 + 1 }

// previousCapacity 复刻 `n / 2`。
func previousCapacity(n uint64) uint64 { return n / 2 }

// maxCapacityForLoadFactorOne 复刻 `Group::kWidth * 4 - 1`。
func maxCapacityForLoadFactorOne(kwidth int) int { return kwidth*4 - 1 }

// capacityToGrowth 复刻 CapacityToGrowth：小表留一个空槽，大表 7/8。
func capacityToGrowth(capacity uint64, kwidth int) uint64 {
	if capacity <= uint64(maxCapacityForLoadFactorOne(kwidth)) {
		if capacity >= uint64(kwidth-1) {
			return capacity - 1
		}
		return capacity
	}
	return capacity - capacity/8
}

// sizeToCapacity 复刻 SizeToCapacity（装载率的反函数）。
func sizeToCapacity(size uint64, kwidth int) uint64 {
	if size == 0 {
		return 0
	}
	extra := uint64(0)
	if size >= uint64(kwidth/2) {
		extra = 1
	}
	leadingZeros := countlZero(size + extra)
	if size < uint64(maxCapacityForLoadFactorOne(kwidth)) {
		return ^uint64(0) >> uint(leadingZeros)
	}
	const kLast3Bits = uint64(7) << (64 - 3)
	maxSizeForNext := kLast3Bits >> uint(leadingZeros)
	if size > maxSizeForNext {
		leadingZeros--
	}
	return ^uint64(0) >> uint(leadingZeros)
}

// abslProbeSeq 复刻 probe_seq：offset 以槽为单位，步长 = kWidth，掩码就是 capacity（2^k-1）。
func abslProbeSeq(h1Value, capacity uint64, kwidth, limit int) []uint64 {
	offset := h1Value & capacity
	index := uint64(0)
	out := make([]uint64, 0, limit)
	for i := 0; i < limit; i++ {
		out = append(out, offset)
		index += uint64(kwidth)
		offset = (offset + index) & capacity
	}
	return out
}

// hbCapacityToBuckets 复刻 hashbrown 的 capacity_to_buckets（小表查下限表，大表 cap*8/7 取幂）。
func hbCapacityToBuckets(cap, width, elemSize int) int {
	if cap < 15 {
		minCap := 3
		switch {
		case width == 16 && elemSize <= 1:
			minCap = 14
		case (width == 16 && elemSize <= 3) || (width == 8 && elemSize <= 1):
			minCap = 7
		}
		if cap < minCap {
			cap = minCap
		}
		if cap < 4 {
			return 4
		}
		if cap < 8 {
			return 8
		}
		return 16
	}
	adjusted := (cap * 8) / 7
	k := 0
	v := adjusted - 1
	for v > 0 {
		k++
		v >>= 1
	}
	return 1 << k
}

// hbBucketMaskToCapacity 复刻 bucket_mask_to_capacity。
func hbBucketMaskToCapacity(bucketMask int) int {
	if bucketMask < 8 {
		return bucketMask
	}
	return ((bucketMask + 1) / 8) * 7
}

// hbProbeSeq 复刻 hashbrown 的 ProbeSeq：stride 每次加 Group::WIDTH。
func hbProbeSeq(h1Value uint64, bucketMask, width, limit int) []uint64 {
	pos := h1Value & uint64(bucketMask)
	stride := uint64(0)
	out := make([]uint64, 0, limit)
	for i := 0; i < limit; i++ {
		out = append(out, pos)
		stride += uint64(width)
		pos = (pos + stride) & uint64(bucketMask)
	}
	return out
}

// 语义判据：两派对 "full" 的定义一致，对 EMPTY/DELETED 的字节值互换。
func abslIsFull(ctrl int) bool        { return asI8(ctrl) >= 0 }
func abslIsEmpty(ctrl int) bool       { return asI8(ctrl) == abslEmpty }
func abslIsDeleted(ctrl int) bool     { return asI8(ctrl) == abslDeleted }
func abslIsEmptyOrDeleted(c int) bool { return asI8(c) < 0 }

func hbIsFull(tag int) bool         { return asU8(tag)&0x80 == 0 }
func hbIsSpecial(tag int) bool      { return asU8(tag)&0x80 != 0 }
func hbSpecialIsEmpty(tag int) bool { return asU8(tag)&0x01 != 0 }
func hbIsEmpty(tag int) bool        { return asU8(tag) == hbEmpty }
func hbIsDeleted(tag int) bool      { return asU8(tag) == hbDeleted }
