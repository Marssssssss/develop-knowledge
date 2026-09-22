package main

import "fmt"

func main() {
	fmt.Println("=== 1. 容量单位换算 ===")
	for _, size := range []int{512, 1024, 2048, 4096, 20480} {
		fmt.Printf("  item %6dB -> 写 %2.0f WCU / 强一致读 %2.0f RCU / 单分区上限 %.0f 次每秒\n",
			size, wcuFor(size), rcuFor(size, true), maxOpsPerPartition(size, true))
	}

	fmt.Println()
	fmt.Println("=== 2. 时间序列表：日期当 partition key 的热分区 ===")
	loads := []float64{2800}
	for i := 0; i < 9; i++ {
		loads = append(loads, 40)
	}
	fmt.Printf("  判为热分区的下标: %v\n", isHot(loads, 0.5))
	for _, adaptive := range []bool{false, true} {
		t := Table{Partitions: 10, Provisioned: 8000, Adaptive: adaptive}
		served, throttled := t.Serve(loads)
		fmt.Printf("  adaptive=%-5v -> 服务 %.0f / 限流 %.0f（热分区拿到 %.0f）\n",
			adaptive, sum(served), throttled, served[0])
	}

	fmt.Println()
	fmt.Println("=== 3. 写分片：把今天这一个键拆成 N 个 ===")
	for _, shards := range []int{10, 50, 200} {
		fmt.Printf("  拆成 %3d 个后缀 -> 每键 %.1f RCU，读一天要 %d 次 Query\n",
			shards, 2800.0/float64(shards), readFanout(shards))
	}

	fmt.Println()
	fmt.Println("=== 4. 计算后缀（可反算） ===")
	for _, oid := range []string{"order-42", "order-43", "abc"} {
		fmt.Printf("  %-10s -> 后缀 %3d\n", oid, writeShardSuffix(oid, 200))
	}
	fmt.Printf("  随机后缀示例: %s\n", randomSuffix("2014-07-09", 7))

	fmt.Println()
	fmt.Println("=== 5. 借不出硬顶 ===")
	t := Table{Partitions: 4, Provisioned: 20000, Adaptive: true}
	served, throttled := t.Serve([]float64{5000, 0, 0, 0})
	fmt.Printf("  表预置 20000 RCU，单键 5000 RCU -> 服务 %.0f / 限流 %.0f\n", served[0], throttled)
}
