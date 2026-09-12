// Lucene/Elasticsearch 段合并策略演示 —— Go 标准库版（与 Python 等价）
//
// 参考：
//   * Elastic blog, "Lucene's Handling of Deleted Documents"
//   * Elastic blog, "Performance Considerations for Elasticsearch Indexing"
//
// 关键事实：
//   1. segment 不可变，更新 = 新增 segment + 老 segment 内 bitset 标记删除
//   2. 删除只在合并时回收空间
//   3. TieredMergePolicy 的合并评分：score ∝ size · (1 + reclaimDeletesWeight · delRatio)
//   4. ES 默认 20 MB/s 合并 IO 节流
package main

import (
	"fmt"
	"strings"
)

const (
	SegmentsPerTier      = 10
	MaxMergedSegment     = 5 * 1024 * 1024 * 1024 // 5 GB
	FloorSegment         = 2 * 1024 * 1024        // 2 MB
	ReclaimDeletesWeight = 2.0
	MergeThrottleMBps    = 20.0
	SecondsPerDay        = 86400
)

type Segment struct {
	ID        string
	Size      int
	NumDocs   int
	DelDocs   int
	LiveDocs_ int // cached
	DelRatio_ float64
}

func newSeg(id string, size, docs, dels int) Segment {
	s := Segment{ID: id, Size: size, NumDocs: docs, DelDocs: dels}
	s.LiveDocs_ = docs - dels
	if docs > 0 {
		s.DelRatio_ = float64(dels) / float64(docs)
	}
	return s
}

func (s Segment) liveDocs() int       { return s.LiveDocs_ }
func (s Segment) delRatio() float64   { return s.DelRatio_ }

// tierOf: 计算段所属 tier（log scale, 与 TieredMergePolicy 一致）
func tierOf(size int) int {
	if size <= FloorSegment {
		return 0
	}
	sizeNorm := size
	if sizeNorm > MaxMergedSegment {
		sizeNorm = MaxMergedSegment
	}
	tier := 0
	cap := FloorSegment
	for cap < sizeNorm {
		cap *= 2
		tier++
	}
	return tier
}

// mergeScore: 大小 × (1 + reclaim · 删除比)
func mergeScore(s Segment) float64 {
	return float64(s.Size) * (1.0 + ReclaimDeletesWeight*s.delRatio())
}

// findMergeCandidates: 简化的合并候选选择——每个 tier 内按评分升序
func findMergeCandidates(segs []Segment, maxInMerge int) []Segment {
	byTier := map[int][]Segment{}
	for _, s := range segs {
		t := tierOf(s.Size)
		byTier[t] = append(byTier[t], s)
	}
	bestScore := 1e18
	var best []Segment
	for _, segs := range byTier {
		if len(segs) < 2 {
			continue
		}
		sortedSegs := make([]Segment, len(segs))
		copy(sortedSegs, segs)
		for i := 1; i < len(sortedSegs); i++ {
			for j := i; j > 0 && mergeScore(sortedSegs[j-1]) > mergeScore(sortedSegs[j]); j-- {
				sortedSegs[j-1], sortedSegs[j] = sortedSegs[j], sortedSegs[j-1]
			}
		}
		k := maxInMerge
		if k > len(sortedSegs) {
			k = len(sortedSegs)
		}
		if k < 2 {
			continue
		}
		subset := sortedSegs[:k]
		var total float64
		for _, s := range subset {
			total += mergeScore(s)
		}
		if total < bestScore {
			bestScore = total
			best = subset
		}
	}
	return best
}

func fmtSize(n int) string {
	units := []string{"B", "KB", "MB", "GB"}
	x := float64(n)
	for i, u := range units {
		if x < 1024 || i == len(units)-1 {
			return fmt.Sprintf("%.1f%s", x, u)
		}
		x /= 1024
	}
	return fmt.Sprintf("%dB", n)
}

func main() {
	segs := []Segment{
		newSeg("A", 1_500_000_000, 1_000_000, 300_000), // 1.5 GB · 30%
		newSeg("B", 800_000_000, 600_000, 150_000),     // 800 MB · 25%
		newSeg("C", 500_000_000, 400_000, 80_000),      // 500 MB · 20%
		newSeg("D", 100_000_000, 80_000, 20_000),       // 100 MB · 25%
		newSeg("E", 50_000_000, 40_000, 4_000),         // 50 MB · 10%
		newSeg("F", 12_000_000, 10_000, 8_000),         // 12 MB · 80% 删除!
		newSeg("G", 5_000_000, 4_000, 800),             // 5 MB · 20%
		newSeg("H", 1_200_000, 1_000, 300),             // 1.2 MB · 30%
	}

	fmt.Println(strings.Repeat("=", 76))
	fmt.Println("Demo 1 · 一个 shard 内的 segment 清单")
	fmt.Println(strings.Repeat("=", 76))
	fmt.Printf("\n  %4s %10s %4s %12s %6s %12s\n", "id", "size", "tier", "live/del", "del%", "merge score")
	for _, s := range segs {
		t := tierOf(s.Size)
		sc := mergeScore(s)
		fmt.Printf("  %4s %10s %4d %6d/%-5d %5.1f%% %10.2f GB-eq\n",
			s.ID, fmtSize(s.Size), t, s.liveDocs(), s.DelDocs,
			s.delRatio()*100, sc/1e9)
	}

	fmt.Println("\n" + strings.Repeat("=", 76))
	fmt.Println("Demo 2 · TieredMergePolicy 选出的下一组合并候选")
	fmt.Println(strings.Repeat("=", 76))
	cand := findMergeCandidates(segs, 4)
	if len(cand) > 0 {
		names := []string{}
		for _, s := range cand {
			names = append(names, s.ID)
		}
		fmt.Printf("\n  选中合并: %v\n", names)
		for _, s := range cand {
			fmt.Printf("    · %s: %s (%.1f%% 删除)\n", s.ID, fmtSize(s.Size), s.delRatio()*100)
		}
		// 合并后：删除文档被实际剔除
		var totalLive int
		var recovered int
		for _, s := range cand {
			totalLive += s.liveDocs()
			recovered += s.DelDocs
		}
		fmt.Printf("  → 合并后: 1 segment with %d live docs\n", totalLive)
		fmt.Printf("    实际回收的删除文档: %d 个\n", recovered)
	}

	fmt.Println("\n" + strings.Repeat("=", 76))
	fmt.Println("Demo 3 · forceMerge(1) —— 把整个 index 压成 1 segment")
	fmt.Println(strings.Repeat("=", 76))
	var totalSize, totalLive int
	for _, s := range segs {
		totalSize += s.Size
		totalLive += s.liveDocs()
	}
	fmt.Printf("  合并前: %d segments, 总 %s / %d live docs\n",
		len(segs), fmtSize(totalSize), totalLive)
	fmt.Printf("  合并后: 1 segment, 总 %s / %d live docs\n",
		fmtSize(totalSize), totalLive)
	fmt.Println("  适用：写入停止后归档 / 时间序列冷数据冻结（ILM 'forcemerge' action）")
	fmt.Println("  不适用：仍在写入的 index（合并完还会被新段打乱）")

	fmt.Println("\n" + strings.Repeat("=", 76))
	fmt.Printf("Demo 4 · 默认 %.0f MB/s 节流决定后台合并 IO\n", MergeThrottleMBps)
	fmt.Println(strings.Repeat("=", 76))
	dailyGB := MergeThrottleMBps * SecondsPerDay / 1024
	fmt.Printf("  全时每秒 %.0f MB → %.1f GB/day\n", MergeThrottleMBps, dailyGB)
	for _, v := range []float64{10, 50, 200, 500, 1000} {
		ratio := v / dailyGB
		tag := "✓ 充裕（合并跟得上写入）"
		switch {
		case ratio < 0.5:
			tag = "✓ OK"
		case ratio >= 1:
			tag = "❌ 限速会拖死合并，可能出现 'now throttling indexing'"
		}
		fmt.Printf("  日新增 %5.0f GB: 占用合并 IO %5.1f%%  %s\n", v, ratio*100, tag)
	}
}
