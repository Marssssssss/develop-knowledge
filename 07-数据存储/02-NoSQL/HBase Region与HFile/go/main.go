package main

import "fmt"

func main() {
	p := NewIncreasingPolicy(defaultMemstoreFlush, defaultMaxFileSize, 0)

	fmt.Println("=== 1. 默认分裂策略 IncreasingToUpperBound ===")
	fmt.Printf("  memstore flush = %dMB -> initialSize = %dMB（2 倍）\n",
		defaultMemstoreFlush/mb, p.InitialSize/mb)
	fmt.Printf("  hbase.hregion.max.filesize = %dMB\n", defaultMaxFileSize/mb)
	fmt.Println("  阈值 = min(maxFileSize, initialSize x region 数^3)")
	for _, n := range []int{0, 1, 2, 3, 4, 10, 100, 101} {
		fmt.Printf("    region 数 %3d -> 阈值 %8dMB\n", n, p.SizeToCheck(n)/mb)
	}

	fmt.Println()
	fmt.Println("=== 2. 一张表的分裂过程 ===")
	for _, pair := range SplitSequence(p, 1, 6) {
		fmt.Printf("  %d 个 region 时，超过 %8dMB 就分裂\n", pair[0], pair[1]/mb)
	}

	fmt.Println()
	fmt.Println("=== 3. 与 ConstantSize 的差别 ===")
	c := NewConstantPolicy(defaultMaxFileSize, 0.5, defaultJitter)
	fmt.Printf("  ConstantSize          : 恒为 %dMB\n", c.Threshold/mb)
	fmt.Printf("  IncreasingToUpperBound: 单 region 时只要 %dMB，是它的 1/40\n", p.SizeToCheck(1)/mb)

	fmt.Println()
	fmt.Println("=== 4. jitter：错峰分裂 ===")
	for _, r := range []float64{0.0, 0.25, 0.5, 0.75, 1.0} {
		fmt.Printf("    random=%.2f -> rate=%+.4f -> 阈值 %dMB\n",
			r, jitterRate(r, defaultJitter),
			constantSizeThreshold(defaultMaxFileSize, r, defaultJitter)/mb)
	}
	lo := constantSizeThreshold(defaultMaxFileSize, 0.0, defaultJitter) / mb
	hi := constantSizeThreshold(defaultMaxFileSize, 1.0, defaultJitter) / mb
	fmt.Printf("  两个 region 的实际阈值可能差 %dMB\n", hi-lo)

	fmt.Println()
	fmt.Println("=== 5. 覆盖 initialSize ===")
	p2 := NewIncreasingPolicy(defaultMemstoreFlush, defaultMaxFileSize, 64*mb)
	fmt.Printf("  显式配置 64MB -> 2 个 region 时阈值 %dMB\n", p2.SizeToCheck(2)/mb)

	fmt.Println()
	fmt.Println("=== 6. HFile 版本 ===")
	fmt.Printf("  默认 hfile.format.version = %d（trailer 用 protobuf 序列化）\n", hfileDefaultVersion())
	for _, v := range []int{2, 3, 4} {
		fmt.Printf("    v%d 可写 = %v\n", v, canWriteHFile(v))
	}
}
