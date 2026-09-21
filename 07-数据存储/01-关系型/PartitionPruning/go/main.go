package main

import "fmt"

func i64(v int64) *int64 { return &v }

func main() {
	fmt.Println("声明式分区与分区裁剪(Go 转写版)")

	fmt.Println("\n== 1. RANGE 边界: 含下界不含上界 ==")
	r1 := RangePartition{Label: "p1_10", Lower: i64(1), Upper: i64(10)}
	r2 := RangePartition{Label: "p10_20", Lower: i64(10), Upper: i64(20)}
	for _, v := range []int64{1, 9, 10, 20} {
		hit := []string{}
		if r1.MatchValue(v) {
			hit = append(hit, r1.Name())
		}
		if r2.MatchValue(v) {
			hit = append(hit, r2.Name())
		}
		where := "无"
		if len(hit) > 0 {
			where = fmt.Sprint(hit)
		}
		fmt.Printf("  值 %-3d -> %s\n", v, where)
	}

	months := []Partition{
		RangePartition{Label: "y2006m02", Lower: i64(20060201), Upper: i64(20060301)},
		RangePartition{Label: "y2006m03", Lower: i64(20060301), Upper: i64(20060401)},
		RangePartition{Label: "y2007m11", Lower: i64(20071101), Upper: i64(20071201)},
		RangePartition{Label: "y2007m12", Lower: i64(20071201), Upper: i64(20080101)},
		RangePartition{Label: "y2008m01", Lower: i64(20080101), Upper: i64(20080201)},
	}
	plan := NewPrunePlan(months, true).WithClauses([][2]interface{}{{"=", int64(20060315)}})
	surv, removed := plan.PlannerPrune()
	fmt.Println("  = 2006-03-15 存活:", surv, "裁掉:", removed)

	fmt.Println("\n== 2. HASH 与 LIST ==")
	hs := []Partition{
		HashPartition{Label: "h0", Modulus: 4, Remainder: 0},
		HashPartition{Label: "h1", Modulus: 4, Remainder: 1},
		HashPartition{Label: "h2", Modulus: 4, Remainder: 2},
		HashPartition{Label: "h3", Modulus: 4, Remainder: 3},
	}
	for _, v := range []interface{}{"user-1", "user-2", "user-3"} {
		hits, _ := Prune(hs, "=", v)
		fmt.Printf("  %v -> %v\n", v, hits)
	}
	ls := []Partition{
		ListPartition{Label: "east", Values: map[string]bool{"BJ": true, "SH": true}},
		ListPartition{Label: "west", Values: map[string]bool{"CD": true}},
		ListPartition{Label: "other", IsDefault: true},
	}
	hits, _ := Prune(ls, "=", "BJ")
	fmt.Println("  LIST city='BJ' ->", hits, "(DEFAULT 永远保留)")

	fmt.Println("\n== 3. 三阶段 ==")
	p2 := NewPrunePlan(months, true).WithClauses([][2]interface{}{{"=", int64(20060315)}})
	s2, rem2, locked := p2.InitialPrune()
	fmt.Println(p2.Render(s2, rem2))
	fmt.Println("  仍被加锁的分区数:", len(locked))
	loops, never := p2.ExecPrune([]interface{}{int64(20060210), int64(20071205), int64(20080110)}, "=")
	fmt.Println("  loops:", loops)
	fmt.Println("  never executed:", never)

	fmt.Println("\n== 4. enable_partition_pruning = off ==")
	off := NewPrunePlan(months, false).WithClauses([][2]interface{}{{"=", int64(20060315)}})
	s3, rem3 := off.PlannerPrune()
	fmt.Println("  存活:", s3, "裁掉:", rem3)
}
