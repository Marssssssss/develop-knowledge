// Prometheus TSDB 存储模型 —— Go 版自检。
//
// 运行：go run .
package main

import (
	"fmt"
	"math"
	"os"
	"sort"
)

var pass, failed int

func check(label string, cond bool, detail string) {
	if cond {
		pass++
		return
	}
	failed++
	fmt.Printf("FAIL  %s | %s\n", label, detail)
}

func approxF(a, b, tol float64) bool { return math.Abs(a-b) < tol }

func eqInts(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func eqFloats(a, b []float64) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if !approxF(a[i], b[i], 1e-9) {
			return false
		}
	}
	return true
}

func groupA() {
	u, ok := UlidEncode(1625000000000, 0)
	check("A ULID 为 26 字符", ok && len(u) == 26, u)
	ts, rnd, ok := UlidDecode(u)
	check("A ULID 编解码往返一致", ok && ts == 1625000000000 && rnd == 0,
		fmt.Sprintf("%d %d", ts, rnd))

	umax, _ := UlidEncode(1<<48-1, 1<<80-1)
	check("A 全 1 值首字符为 7（最高 2 位留空）", umax[0] == '7', umax)
	tsMax, rndMax, ok := UlidDecode(umax)
	check("A 边界值往返一致",
		ok && tsMax == 1<<48-1 && rndMax == 1<<80-1, umax)

	sts := []uint64{1625000000000, 1625000000001, 1625000003600, 1625000007000}
	us := make([]string, len(sts))
	for i, t := range sts {
		us[i], _ = UlidEncode(t, 0)
	}
	sortedUs := append([]string{}, us...)
	sort.Strings(sortedUs)
	check("A ULID 字典序 == 时间序（块名可直接按名排序）",
		eqStrs(us, sortedUs), fmt.Sprint(us))

	u0, _ := UlidEncode(1625000000000, 0)
	u1, _ := UlidEncode(1625000000000, 1)
	check("A 同一毫秒内靠 80 位随机数破解并列", u0 < u1, u0+" "+u1)

	_, okOut := UlidEncode(1<<48, 0)
	_, _, okShort := UlidDecode("short")
	check("A 非法输入被拒绝", !okOut && !okShort, "")
}

func eqStrs(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func groupB() {
	check("B 块时间窗 = 2h", BlockDuration == 7200, fmt.Sprint(BlockDuration))
	check("B chunk 段 512MB / WAL 段 128MB / 至少 3 段 / 默认保留 15d",
		ChunkSegmentSize == 512*1024*1024 && WalSegmentSize == 128*1024*1024 &&
			MinWalSegments == 3 && DefaultRetention == 15*86400, "")
}

func groupC() {
	check("C 保留 15d → 上限 = 36h",
		approxF(MaxCompactedSpanOf(15*86400), 36*3600, 1e-9),
		fmt.Sprint(MaxCompactedSpanOf(15*86400)))
	check("C 保留 90d → 上限 = 9d",
		approxF(MaxCompactedSpanOf(90*86400), 9*86400, 1e-9), "")
	check("C 保留 365d → 10% 超过顶，取 31d",
		approxF(MaxCompactedSpanOf(365*86400), MaxCompactedSpan, 1e-9) &&
			MaxCompactedSpan == 31*86400, "")

	l15 := CompactionLadder(15 * 86400)
	want := []float64{7200, 14400, 28800, 57600, 115200}
	check("C 15d 保留下阶梯 = 2/4/8/16/32h 共 5 级", eqFloats(l15, want), fmt.Sprint(l15))
	check("C 阶梯末级 ≤ 上限且下一级会超出",
		l15[len(l15)-1] <= MaxCompactedSpanOf(15*86400) &&
			MaxCompactedSpanOf(15*86400) < l15[len(l15)-1]*2, "")
	check("C 365d 保留下阶梯到 9 级", len(CompactionLadder(365*86400)) == 9,
		fmt.Sprint(len(CompactionLadder(365*86400))))
	check("C 保留 4h 时上限 0.4h < 基础块 2h，阶梯为空",
		len(CompactionLadder(4*3600)) == 0, fmt.Sprint(CompactionLadder(4*3600)))
}

func groupD() {
	s15 := 15 * 86400.0
	check("D 15d / 10 万样本每秒 / 1.5B 每样本 → 194.4 GB",
		approxF(NeededDiskSpace(s15, 100000, 1.5), 1.944e11, 1e6),
		fmt.Sprint(NeededDiskSpace(s15, 100000, 1.5)))
	check("D 每样本 1B（官方下界）→ 129.6 GB",
		approxF(NeededDiskSpace(s15, 100000, 1.0), 1.296e11, 1e6), "")
	check("D 每样本 2B（官方上界）→ 259.2 GB",
		approxF(NeededDiskSpace(s15, 100000, 2.0), 2.592e11, 1e6), "")
	check("D 上下界比恰为 2",
		approxF(NeededDiskSpace(s15, 1, 2.0)/NeededDiskSpace(s15, 1, 1.0), 2.0, 1e-12), "")
}

func groupE() {
	check("E 至少 3 段 → 384 MiB 下限", 3*WalSegmentSize == 384*1024*1024, "")
	n := WalSegmentsFor(2*3600, 100000, 2.0)
	check("E 2h 原始数据（10 万样本/s、每样本 2B）→ 11 段", n == 11, fmt.Sprint(n))
	check("E 空负载回落到 3 段下限", WalSegmentsFor(0, 0, 2.0) == 3, "")
	check("E 1h 数据 → 6 段（段数随时间线性增长）",
		WalSegmentsFor(3600, 100000, 2.0) == 6, "")

	refs := []int{0, 1, 2, 3, 4, 5, 6, 7, 8, 9}
	shards := WalShards(refs, 4)
	sizes := []int{len(shards[0]), len(shards[1]), len(shards[2]), len(shards[3])}
	check("E 重放按 ref % workers 分片：4 个 worker 拿 3/3/2/2 条",
		eqInts(sizes, []int{3, 3, 2, 2}), fmt.Sprint(sizes))
	flat := []int{}
	for _, s := range shards {
		flat = append(flat, s...)
	}
	sort.Ints(flat)
	check("E 分片是划分：并集恰好覆盖全部 ref 且不重不漏", eqInts(flat, refs), fmt.Sprint(flat))
	check("E ref=7 归属 worker 3（7 % 4）", len(shards[3]) == 2 && shards[3][1] == 7,
		fmt.Sprint(shards[3]))
}

func groupG() {
	ix := NewInvertedIndex()
	ix.Add(1, map[string]string{"method": "GET", "handler": "/api"})
	ix.Add(2, map[string]string{"handler": "/api"})
	ix.Add(5, map[string]string{"method": "GET", "handler": "/api"})
	ix.Add(8, map[string]string{"handler": "/api"})
	for _, r := range []int{12, 47, 103} {
		ix.Add(r, map[string]string{"method": "GET"})
	}
	check("G posting list 升序（merge-join 的前提）",
		eqInts(ix.Postings("method", "GET"), []int{1, 5, 12, 47, 103}),
		fmt.Sprint(ix.Postings("method", "GET")))
	check("G 另一条 posting 与官方示例一致",
		eqInts(ix.Postings("handler", "/api"), []int{1, 2, 5, 8}), "")

	refs, comps := ix.Intersect([][2]string{{"method", "GET"}, {"handler", "/api"}})
	check("G 交集 = [1, 5]", eqInts(refs, []int{1, 5}), fmt.Sprint(refs))
	check("G merge-join 比较 4 次 ≤ n+m = 9（有序表线性合并）",
		comps == 4 && comps <= 5+4, fmt.Sprint(comps))

	empty, c2 := ix.Intersect([][2]string{{"method", "POST"}})
	check("G 不存在的标签值 → 空集且 0 次比较", len(empty) == 0 && c2 == 0, "")
	all, _ := ix.Intersect(nil)
	check("G 无 matcher → 全量 ref",
		eqInts(all, []int{1, 2, 5, 8, 12, 47, 103}), fmt.Sprint(all))
}

func main() {
	groupA()
	groupB()
	groupC()
	groupD()
	groupE()
	groupG()
	fmt.Println("------------------------------------------------------------")
	if failed > 0 {
		fmt.Printf("断言失败 %d 项 / 通过 %d 项\n", failed, pass)
		os.Exit(1)
	}
	fmt.Printf("全部 %d 项断言通过\n", pass)
}
