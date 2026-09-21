package main

import (
	"encoding/hex"
	"fmt"
)

var (
	two  = []string{"-80", "80-"}
	four = []string{"-40", "40-80", "80-c0", "c0-"}
)

func main() {
	fmt.Println("Vitess 分片与路由键空间(Go 转写版)")

	fmt.Println("\n== 1. 键范围: 含起点不含终点 ==")
	fmt.Println(four, validatePartition(four))
	for _, h := range []string{"3f", "40", "7f", "80", "c0", "8000"} {
		raw, err := hex.DecodeString(h)
		if err != nil {
			continue
		}
		fmt.Printf("  ksId 0x%-6s -> %v\n", h, locate(four, raw))
	}

	fmt.Println("\n== 2. vindex 分布对比(10000 个连续 id) ==")
	ids := make([]uint64, 0, 10000)
	for i := uint64(0); i < 10000; i++ {
		ids = append(ids, i)
	}
	for _, name := range []string{"numeric", "reverse_bits"} {
		r, err := routeEqual(name, ids, two)
		if err != nil {
			fmt.Println("  err:", err)
			continue
		}
		counts := map[string]int{}
		for k, v := range r.Shards {
			counts[k] = len(v)
		}
		v, _ := lookupVindex(name)
		fmt.Printf("  %-13s cost=%d fanout=%d 分布=%v\n", name, v.Cost, r.Fanout, counts)
	}

	fmt.Println("\n== 3. 路由 fan-out ==")
	r1, _ := routeEqual("reverse_bits", []uint64{1, 5}, four)
	r2, _ := routeEqual("reverse_bits", []uint64{1, 2}, four)
	r3, _ := routeRange("numeric", 0, 1<<63, two)
	r4, _ := routeRange("reverse_bits", 1, 100, two)
	fmt.Println("  IN (1,5)   reverse_bits fanout =", r1.Fanout)
	fmt.Println("  IN (1,2)   reverse_bits fanout =", r2.Fanout)
	fmt.Println("  BETWEEN 0 AND 2^63 numeric fanout =", r3.Fanout)
	fmt.Println("  BETWEEN 1 AND 100 reverse_bits kind =", r4.Kind)
	fmt.Println("  无分片键    fanout =", routeUnconstrained(four).Fanout)

	fmt.Println("\n== 4. resharding ==")
	newShards, kids, err := reshard(four, "80-c0")
	if err != nil {
		fmt.Println("  err:", err)
		return
	}
	fmt.Println("  80-c0 ->", kids, " 新分区:", newShards, validatePartition(newShards))
	for _, id := range []uint64{0x7fffffffffffffff, 0x8000000000000123, 0xa000000000000001, 0xffffffffffffffff} {
		ksid, _ := hashID("numeric", id)
		fmt.Printf("  id=0x%x 切分前 %v -> 切分后 %v\n", id, locate(four, ksid), locate(newShards, ksid))
	}
	gen, _ := generateShardRanges(4)
	fmt.Println("\n  GenerateShardRanges(4) =", gen)
}
