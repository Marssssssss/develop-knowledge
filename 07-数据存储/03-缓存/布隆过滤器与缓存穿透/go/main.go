package main

import (
	"errors"
	"fmt"
	"math"
)

var errInvalid = errors.New("invalid argument")

// hv 演示用的确定性哈希对：第 i 个元素占第 i*8 .. i*8+7 号位。
func hv(i int) (int, int) { return i * 8, 1 }

func main() {
	fmt.Println("=== 1. 位宽与哈希数 ===")
	for _, e := range []float64{0.1, 0.01, 0.001, 0.0001} {
		bpe := CalcBpe(e)
		bits := int(10000 * bpe)
		fmt.Printf("  error=%-8v bpe=%6.3f  1万元素=%7d 位 ≈ %6.2f KB  hashes=%d\n",
			e, bpe, bits, float64(bits)/8/1024, int(math.Ceil(math.Ln2*bpe)))
	}

	fmt.Println("\n=== 2. BF.RESERVE 的参数命运 ===")
	for _, c := range []struct {
		err float64
		exp int
		ns  bool
	}{
		{0.01, -1, false}, {0.5, -1, false}, {0.01, 0, false}, {0.01, 4, false},
		{0.01, 2, true},
	} {
		e, _, x, opts, err := BfReserveValidate(c.err, 100, c.exp, c.ns)
		if err != nil {
			fmt.Printf("  error=%v expansion=%d nonscaling=%v -> 拒绝: %v\n",
				c.err, c.exp, c.ns, err)
			continue
		}
		kind := "scaling"
		if opts&BloomOptNoScaling != 0 {
			kind = "NONSCALING"
		}
		fmt.Printf("  error=%v expansion=%d nonscaling=%v -> error=%v, expansion=%d, %s\n",
			c.err, c.exp, c.ns, e, x, kind)
	}

	fmt.Println("\n=== 3. scalable 链：误差逐代收紧、容量按 growth 翻倍 ===")
	chain, _ := NewChain(100, 0.01, BloomOptForce64|BloomOptNoRound, 2)
	fmt.Printf("  首链: error=%v 容量=%d hashes=%d bits=%d\n",
		chain.cur().Inner.Error, chain.cur().Inner.Entries,
		chain.cur().Inner.Hashes, chain.cur().Inner.Bits)
	for i := 0; i < 100; i++ {
		chain.Add(hv(i))
	}
	chain.Add(hv(100))
	for i, l := range chain.Filters {
		fmt.Printf("  链[%d]: error=%.4f 容量=%d hashes=%d 已装=%d\n",
			i, l.Inner.Error, l.Inner.Entries, l.Inner.Hashes, l.Size)
	}

	fmt.Println("\n=== 4. 缓存穿透防护 ===")
	hit := true
	for i := 0; i < 100; i++ {
		if !chain.Check(hv(i)) {
			hit = false
		}
	}
	fmt.Println("  已写入的 100 个 key 全部命中过滤器:", hit)
	miss := 0
	for i := 1000; i < 1100; i++ {
		if !chain.Check(hv(i)) {
			miss++
		}
	}
	fmt.Printf("  1000~1099 号 key 被判定「一定不存在」的有 %d 个（其余为假阳性）\n", miss)

	fmt.Println("\n=== 5. 布谷鸟过滤器 ===")
	cf, err := NewCuckooFilter(1024, 2, 20, 2)
	if err != nil {
		fmt.Println("  创建失败:", err)
		return
	}
	fmt.Printf("  容量 1024 / bucketSize 2 -> numBuckets=%d 槽位=%d\n",
		cf.NumBuckets, cf.NumBuckets*cf.BucketSize)
	p := GetLookupParams(123456)
	fmt.Printf("  hash=123456 -> fp=%d h1桶=%d h2桶=%d\n",
		p.Fp, p.I1%cf.NumBuckets, p.I2%cf.NumBuckets)
	for i := 0; i < 300; i++ {
		cf.InsertUnique((i * 2654435761) % (1 << 32))
	}
	fmt.Printf("  插入 300 个后: numItems=%d 子过滤器=%d\n", cf.NumItems, cf.NumFilters)
	fmt.Println("  查已插入的存在:", cf.Check(2654435761%(1<<32)))
	fmt.Println("  查未插入的:", cf.Check(0xDEADBEEF))
}
