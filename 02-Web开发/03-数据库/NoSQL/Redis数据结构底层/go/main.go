// Redis 底层数据结构（双表渐进式 rehash 字典 + 跳表 + 紧凑编码阈值）的 Go 版对照实现。
//
// 口径来源（实读）：
//   - Redis unstable src/dict.c：ht_table[2]/ht_size_exp[2]/ht_used[2]/rehashidx；
//     dict_force_resize_ratio = 4；扩容判定 1:1（或允许 resize 时 4 倍强制）、收缩判定 1:8 与 1:32；
//     "Prepare a second hash table for incremental rehashing"；一步 = 搬一个桶且最多探望 n*10 个
//     空桶；ht_used[0]==0 时把 ht[1] 拷回 ht[0]；查找遍历两张表并跳过 table0 中 idx < rehashidx
//     的桶；rehash 期间插入进 ht[1]；安全迭代器 dictPauseRehashing()。
//   - Redis unstable src/server.h / src/t_zset.c：ZSKIPLIST_MAXLEVEL 32、ZSKIPLIST_P 0.25；
//     zslRandomLevel 几何分布；zslInsertNode 用 update[]/rank[] 维护 span；zslGetRank 为 1-based；
//     level 0 的 backward 指针支撑 ZREVRANGE；允许重复 score。
//   - Redis 官方 "Memory optimization"：hash 512/64、zset 128/64、intset 512、set-listpack 128/64。
//
// 运行：go run .（dict.go / skiplist.go / main.go 同属 package main；本机无 Go 工具链，
// 代码经人工审查 + 结构校验 + 逐行等价 Python 转写验证）
package main

import (
	"fmt"
	"os"
)

// ---------------------------------------------------------------- 紧凑编码阈值
func zsetEncoding(sizeHint, valLenHint int) string {
	if sizeHint <= 128 && valLenHint <= 64 {
		return "listpack"
	}
	return "skiplist"
}

func hashEncoding(fields, maxLen int) string {
	if fields <= 512 && maxLen <= 64 {
		return "listpack"
	}
	return "hashtable"
}

var checks = struct{ pass, fail int }{}

func check(label string, cond bool, detail string) {
	if cond {
		checks.pass++
		fmt.Printf("  [PASS] %s\n", label)
		return
	}
	checks.fail++
	fmt.Printf("  [FAIL] %s :: %s\n", label, detail)
}

func main() {
	fmt.Println("1) 字典：初始化、1:1 扩容、渐进式 rehash、两表并存查找")
	d := newDict()
	d.insert("a", "1")
	check("首次插入后容量 = 4、rehashidx = -1", d.size(0) == initialSize && d.rehashidx == -1, fmt.Sprint(d.log))
	for i := 0; i < 4; i++ {
		d.insert(fmt.Sprintf("k%d", i), fmt.Sprint(i))
	}
	check("第 5 个键触发扩容并进入 rehash", d.rehashing() && len(d.log) == 2, fmt.Sprint(d.log))
	check("新表是 ≥ used+1 的最小 2 的幂（8）", d.size(1) == 8, fmt.Sprint(d.size(1)))
	missing := ""
	for _, k := range []string{"a", "k0", "k1", "k2", "k3"} {
		if _, ok := d.find(k); !ok {
			missing += k
		}
	}
	check("两张表并存期间所有键可查（跳过已迁移桶）", missing == "", missing)
	check("查找顺带搬桶（steps 增加）", d.steps > 0, fmt.Sprint(d.steps))

	d2 := newDict()
	for i := 0; i < 5; i++ {
		d2.insert(fmt.Sprintf("x%d", i), "v")
	}
	check("已进入 rehash 状态", d2.rehashing(), fmt.Sprint(d2.log))
	before := d2.used[1]
	d2.paused++ // 模拟安全迭代器在场：冻结搬运
	d2.insert("brand_new", "v")
	d2.paused--
	check("rehash 期间新键计入 ht[1]", d2.used[1] == before+1 && d2.rehashing(), fmt.Sprint(d2.used))
	v, ok := d2.find("brand_new")
	check("新键在两张表并存期间可查", ok && v == "v", v)

	d3 := newDict()
	for i := 0; i < 60; i++ {
		d3.insert(fmt.Sprintf("n%d", i), fmt.Sprint(i))
	}
	d3.rehash(1000)
	check("rehash 完成后只剩一张表、容量为 2 的幂且 ≥ 元素数",
		!d3.rehashing() && d3.used[1] == 0 && d3.size(0) >= d3.total() && d3.size(0)&(d3.size(0)-1) == 0,
		fmt.Sprint(d3.size(0), d3.total()))
	bad := 0
	for i := 0; i < 60; i++ {
		if _, ok := d3.find(fmt.Sprintf("n%d", i)); !ok {
			bad++
		}
	}
	check("60 个键全部可查（链地址法，无覆盖丢失）", bad == 0, fmt.Sprint(bad))

	fmt.Println("\n2) 跳表：顺序、rank、反向指针、层数分布")
	seed := uint64(12345)
	randFn := func() float64 {
		seed = seed*6364136223846793005 + 1442695040888963407
		return float64(seed>>11) / float64(uint64(1)<<53)
	}
	sl := newSkipList(randFn)
	for i := 0; i < 200; i++ {
		sl.insert((i*37)%51, fmt.Sprintf("e%03d", i))
	}
	items := sl.items()
	ordered := len(items) == 200
	for i := 1; i < len(items); i++ {
		if !keyLess(items[i-1].score, items[i-1].ele, items[i].score, items[i].ele) {
			ordered = false
		}
	}
	check("遍历顺序严格递增（score 升序、同分按 ele）", ordered, fmt.Sprint(len(items)))
	rev := sl.reverseItems()
	check("level 0 的 backward 指针给出反向序（ZREVRANGE）",
		len(rev) == 200 && rev[0] == items[199] && rev[199] == items[0], fmt.Sprint(rev[0]))
	ranksOK := true
	for i, it := range items {
		if sl.getRank(it.score, it.ele) != i+1 {
			ranksOK = false
		}
	}
	check("getRank 与遍历位置一致（1-based）", ranksOK, "")

	levels := make([]int, 60000)
	for i := range levels {
		levels[i] = zslRandomLevel(randFn)
	}
	sum, ge2, ge3, mx := 0, 0, 0, 0
	for _, l := range levels {
		sum += l
		if l >= 2 {
			ge2++
		}
		if l >= 3 {
			ge3++
		}
		if l > mx {
			mx = l
		}
	}
	avg := float64(sum) / float64(len(levels))
	check("平均层数 ≈ 1/(1-P) = 1.333（±0.03）", avg > 1.303 && avg < 1.363, fmt.Sprintf("%.4f", avg))
	check("P(level ≥ 2) ≈ 0.25（±0.01）", float64(ge2)/60000 > 0.24 && float64(ge2)/60000 < 0.26,
		fmt.Sprintf("%.4f", float64(ge2)/60000))
	check("P(level ≥ 3) ≈ 0.0625", float64(ge3)/60000 > 0.05 && float64(ge3)/60000 < 0.08,
		fmt.Sprintf("%.4f", float64(ge3)/60000))
	check("层数不超过 ZSKIPLIST_MAXLEVEL(32)", mx <= zslMaxLevel, fmt.Sprint(mx))

	fmt.Println("\n3) 紧凑编码阈值（Redis ≥ 7.0）")
	check("zset 128/64 → listpack；129 或 65 字节 → skiplist",
		zsetEncoding(128, 64) == "listpack" && zsetEncoding(129, 64) == "skiplist" && zsetEncoding(128, 65) == "skiplist", "")
	check("hash 512/64 → listpack；513 → hashtable",
		hashEncoding(512, 64) == "listpack" && hashEncoding(513, 64) == "hashtable", "")

	fmt.Printf("\n断言结果：pass=%d fail=%d\n", checks.pass, checks.fail)
	if checks.fail > 0 {
		os.Exit(1)
	}
}
