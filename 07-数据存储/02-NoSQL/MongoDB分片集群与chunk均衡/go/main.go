package main

import "fmt"

func main() {
	fmt.Println("=== 1. 均衡阈值：3 x range size ===")
	fmt.Printf("  默认 range size = %dMB -> 阈值 %dMB\n", defaultChunkMB, migrationThreshold(defaultChunkMB))
	for _, sizes := range [][]int{{1000, 600}, {1000, 616}, {1000, 617}, {900, 800, 700}} {
		state := "视为均衡"
		if needsBalancing(sizes, defaultChunkMB) {
			state = "触发迁移"
		}
		fmt.Printf("  %-22v 差 %4dMB -> %s\n", sizes, maxOf(sizes)-minOf(sizes), state)
	}

	fmt.Println()
	fmt.Println("=== 2. 并发迁移数 = floor(n/2) ===")
	for n := 1; n <= 8; n++ {
		fmt.Printf("  %d 个 shard -> 最多 %d 个并发迁移\n", n, maxParallelMigrations(n))
	}

	fmt.Println()
	fmt.Println("=== 3. 自动分裂 ===")
	for _, size := range []int{129, 256, 900, 5000} {
		splits, parts, each := splitPlan(size, defaultChunkMB)
		fmt.Printf("  %5dMB -> 分裂 %d 次，%2d 块 x %.1fMB\n", size, splits, parts, each)
	}

	fmt.Println()
	fmt.Println("=== 4. jumbo chunk 诊断 ===")
	cs := []*Chunk{
		{Low: "a", High: "b", SizeMB: 200, DistinctKeys: 1},
		{Low: "a", High: "b", SizeMB: 200, DistinctKeys: 5},
		{Low: "2026-09-22", High: "2026-09-23", SizeMB: 500, DistinctKeys: 1},
	}
	for _, c := range cs {
		c.Evaluate(defaultChunkMB)
		fmt.Printf("  %s\n", c)
		if c.Jumbo {
			fmt.Println("     -> 不可分裂，需 refine shard key 或 reshard")
		} else {
			fmt.Println("     -> 可 splitAt，均衡器能处理")
		}
	}

	fmt.Println()
	fmt.Println("=== 5. 迁移状态机（异步删除）===")
	m := &Migration{Name: "chunk-1", AsyncDelete: true}
	fmt.Printf("  步骤数 = %d\n", len(steps))
	for i := 0; i < 8; i++ {
		m.Advance()
		fmt.Printf("  advance %d -> step=%d done=%v deleted=%v\n", i+1, m.Step, m.Done, m.Deleted)
		if m.Deleted {
			break
		}
	}

	fmt.Println()
	fmt.Println("=== 6. 一轮均衡过程 ===")
	rounds, moves := balanceRound([]int{1000, 600, 600}, defaultChunkMB)
	for i, r := range rounds {
		fmt.Printf("  第 %d 次迁移后: %v (差 %dMB)\n", i+1, r, maxOf(r)-minOf(r))
	}
	if len(rounds) > 0 {
		last := rounds[len(rounds)-1]
		fmt.Printf("  共 %d 次迁移，最终差值 %dMB（阈值 %dMB）\n",
			moves, maxOf(last)-minOf(last), migrationThreshold(defaultChunkMB))
	}
}
