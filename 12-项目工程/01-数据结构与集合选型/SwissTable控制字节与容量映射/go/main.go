package main

import "fmt"

func main() {
	fmt.Println("== 控制字节哨兵：两派正好互换 ==")
	fmt.Printf("  Abseil    kEmpty=0x%02X  kDeleted=0x%02X  kSentinel=0x%02X\n",
		uint8(asI8(abslEmpty)), uint8(asI8(abslDeleted)), uint8(asI8(abslSentinel)))
	fmt.Printf("  hashbrown EMPTY =0x%02X  DELETED  =0x%02X  （没有 sentinel）\n", hbEmpty, hbDeleted)
	fmt.Println("  Abseil 的 kEmpty 就是 hashbrown 的 DELETED；kSentinel 就是 hashbrown 的 EMPTY")

	fmt.Println("\n== 哈希切分：两派都取最高 7 位 ==")
	for _, h := range []uint64{0x7F, 0x80, 1 << 57, 0xFF00000000000000, ^uint64(0)} {
		fmt.Printf("  hash=0x%016X  Abseil H2=0x%02X  hashbrown tag=0x%02X  Go h2=0x%02X\n",
			h, abslH2(h), hbTagFull(h, 8), h&0x7F)
	}

	fmt.Println("\n== 容量形状：Abseil 2^k-1，hashbrown 2^k ==")
	for _, n := range []uint64{1, 2, 4, 8, 9, 100, 1000} {
		fmt.Printf("  Abseil NormalizeCapacity(%d) = %d\n", n, normalizeCapacity(n))
	}
	for _, n := range []int{1, 3, 7, 14, 15, 28, 100, 1000} {
		fmt.Printf("  hashbrown capacity_to_buckets(%d) = %d\n", n, hbCapacityToBuckets(n, 16, 8))
	}

	fmt.Printf("\n== Abseil CapacityToGrowth（kMax=%d）==\n", maxCapacityForLoadFactorOne(16))
	for _, c := range []uint64{3, 7, 15, 31, 63, 127, 255, 1023} {
		fmt.Printf("  capacity=%-6d growth=%-6d (%.3f)\n",
			c, capacityToGrowth(c, 16), float64(capacityToGrowth(c, 16))/float64(c))
	}

	fmt.Println("\n== hashbrown bucket_mask_to_capacity ==")
	for _, buckets := range []int{4, 8, 16, 32, 128, 1024} {
		cap := hbBucketMaskToCapacity(buckets - 1)
		fmt.Printf("  桶数=%-6d capacity=%-6d (%.3f)\n", buckets, cap, float64(cap)/float64(buckets))
	}

	fmt.Println("\n== 探测序列：两派组起点一致 ==")
	a := abslProbeSeq(0, 127, 16, 8)
	b := hbProbeSeq(0, 127, 16, 8)
	fmt.Printf("  Abseil   : %v\n  hashbrown: %v\n  相等=%v\n", a, b, equalU64(a, b))
	fmt.Printf("  Abseil kWidth=8: %v\n", abslProbeSeq(0, 127, 8, 8))
}

func equalU64(x, y []uint64) bool {
	if len(x) != len(y) {
		return false
	}
	for i := range x {
		if x[i] != y[i] {
			return false
		}
	}
	return true
}
