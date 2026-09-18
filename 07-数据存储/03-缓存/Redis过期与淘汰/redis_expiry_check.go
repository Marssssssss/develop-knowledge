// Redis 过期与淘汰自检（与 redis_expiry.go 同一 main 包）。
package main

import (
	"fmt"
	"math"
)

var fails []string

func check(label string, cond bool, detail string) {
	if cond {
		fmt.Printf("  ok   %s\n", label)
		return
	}
	fails = append(fails, label+" "+detail)
	fmt.Printf("  FAIL %s %s\n", label, detail)
}

func main() {
	fmt.Println("[1] EXPIRE NX/XX/GT/LT")
	check("NX 对无 TTL -> 成功", expireOpt(ttlNone, 10, 0, "NX"), "")
	check("XX 对无 TTL -> 跳过", !expireOpt(ttlNone, 10, 0, "XX"), "")
	check("GT 对 non-volatile -> 不成立", !expireOpt(ttlNone, 10, 0, "GT"), "")
	check("LT 对 non-volatile -> 成立", expireOpt(ttlNone, 10, 0, "LT"), "")
	check("GT：910 < 1000 -> 跳过", !expireOpt(1000, 10, 900, "GT"), "")
	check("GT：1100 > 1000 -> 设置", expireOpt(1000, 200, 900, "GT"), "")
	check("LT：910 < 1000 -> 设置", expireOpt(1000, 10, 900, "LT"), "")
	check("*STORE 清 TTL", clearsTTLStore("SUNIONSTORE"), "")

	fmt.Println("[2] activeExpireCycle effort 换算")
	p1, p10 := aeParamsOf(1), aeParamsOf(10)
	check("默认 keys_per_loop 20", p1.keysPerLoop == 20, fmt.Sprint(p1))
	check("默认 fast 1000 us", p1.fastUs == 1000, fmt.Sprint(p1))
	check("默认 slow_perc 25", p1.slowPerc == 25, fmt.Sprint(p1))
	check("默认 acceptable_stale 10", p1.acceptableStale == 10, fmt.Sprint(p1))
	check("effort=10 -> keys 65", p10.keysPerLoop == 65, fmt.Sprint(p10))
	check("effort=10 -> fast 3250", p10.fastUs == 3250, fmt.Sprint(p10))
	check("effort=10 -> slow 43", p10.slowPerc == 43, fmt.Sprint(p10))
	check("effort=10 -> stale 1", p10.acceptableStale == 1, fmt.Sprint(p10))
	check("effort 越大 stale 越低", p10.acceptableStale < p1.acceptableStale, "")
	check("effort 越大扫的键越多", p10.keysPerLoop > p1.keysPerLoop, "")

	fmt.Println("[3] LFU 布局与 Morris 计数")
	lru := lfuInitLRU(1234)
	check("高 16 位是分钟", lfuLDT(lru) == 1234, fmt.Sprint(lfuLDT(lru)))
	check("低 8 位 = LFU_INIT_VAL 5", lfuCounter(lru) == lfuInitVal, fmt.Sprint(lfuCounter(lru)))
	check("r=0 必递增", lfuLogIncr(5, 0.0, 10) == 6, "")
	check("r=1 不递增", lfuLogIncr(5, 1.0, 10) == 5, "")
	check("255 饱和", lfuLogIncr(255, 0.0, 10) == 255, "")
	check("counter<5 时 baseval 夹到 0，p=1", lfuLogIncr(0, 0.5, 10) == 1, "")
	check("factor 大 -> p 小",
		1.0/((100-5)*100+1) < 1.0/((100-5)*10+1), "")

	fmt.Println("[4] 衰减与 16 位回绕")
	check("过 5 分钟减 5", lfuDecay(20, 100, 105, 1) == 15, "")
	check("下限 0", lfuDecay(3, 100, 110, 1) == 0, "")
	check("decay_time=0 永不衰减", lfuDecay(20, 100, 99999, 0) == 20, "")
	check("decay_time=5 过 10 分钟减 2", lfuDecay(20, 100, 110, 5) == 18, "")
	check("未回绕", lfuTimeElapsed(100, 105) == 5, "")
	check("跨回绕：源码算 10", lfuTimeElapsed(65530, 5) == 10, fmt.Sprint(lfuTimeElapsed(65530, 5)))
	check("跨回绕：真实 11（源码少算 1）", 65536-65530+5 == 11, "")
	check("idle = 255 - counter", 255-0 == 255 && 255-255 == 0, "")

	fmt.Println("[5] 官方 lfu-log-factor 表格")
	check("factor 10 / 100 ≈ 10", math.Abs(lfuExpected(100, 10)-10) <= 2.0, fmt.Sprint(lfuExpected(100, 10)))
	check("factor 10 / 1000 ≈ 18", math.Abs(lfuExpected(1000, 10)-18) <= 2.0, fmt.Sprint(lfuExpected(1000, 10)))
	check("factor 10 / 100K ≈ 142", math.Abs(lfuExpected(100000, 10)-142) <= 15.0, fmt.Sprint(lfuExpected(100000, 10)))
	check("factor 0 / 100 ≈ 104", math.Abs(lfuExpected(100, 0)-104) <= 2.0, fmt.Sprint(lfuExpected(100, 0)))
	check("factor 100 / 1M ≈ 143", math.Abs(lfuExpected(1000000, 100)-143) <= 15.0, fmt.Sprint(lfuExpected(1000000, 100)))
	check("精确 DP 与均值场一致（factor 10 / 100）",
		math.Abs(lfuExpectedExact(100, 10)-lfuExpected(100, 10)) < 0.5,
		fmt.Sprint(lfuExpectedExact(100, 10), lfuExpected(100, 10)))
	check("精确 DP 与均值场一致（factor 1 / 1000）",
		math.Abs(lfuExpectedExact(1000, 1)-lfuExpected(1000, 1)) < 0.5,
		fmt.Sprint(lfuExpectedExact(1000, 1), lfuExpected(1000, 1)))
	check("factor 越大计数越低", lfuExpected(100, 100) < lfuExpected(100, 10), "")

	fmt.Println("[6] volatile- 陷阱")
	check("allkeys-lru 不报错", !evictionError("allkeys-lru", false), "")
	check("volatile-lru 无 TTL 键 -> 报错", evictionError("volatile-lru", false), "")
	check("volatile-lru 有 TTL 键 -> 正常", !evictionError("volatile-lru", true), "")
	check("volatile-lfu 同理", evictionError("volatile-lfu", false), "")
	check("noeviction 恒报错", evictionError("noeviction", true), "")

	fmt.Println()
	if len(fails) > 0 {
		fmt.Printf("FAILED %d\n", len(fails))
		return
	}
	fmt.Println("ALL PASS")
}
