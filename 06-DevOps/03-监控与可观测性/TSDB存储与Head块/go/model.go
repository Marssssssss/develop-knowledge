// Package main: Prometheus TSDB 存储模型的 Go 实现。
//
// 事实依据（官方 docs/storage.md，本机实读）：
//   - 样本按 2 小时分组为块；块含 chunks/（每段最大 512 MB）、index、meta.json、tombstones
//   - WAL 位于 wal/，每段 128 MB，至少保留 3 段（高流量实例保留更多以保证 ≥2h 原始数据）
//   - 压缩后单块跨度上限 = min(保留期 10%, 31 天)；压缩期间源块与新块共存
//   - 容量公式 retention_time_seconds * ingested_samples_per_second * bytes_per_sample，
//     每样本平均 1~2 字节；默认保留 15d
//   - 倒排索引为 label → 升序 series ref 列表，多 matcher 取交集（merge-join）
package main

import (
	"math"
	"sort"
)

// 官方默认常量。
const (
	BlockDuration    = 2 * 3600.0
	ChunkSegmentSize = 512 * 1024 * 1024
	WalSegmentSize   = 128 * 1024 * 1024
	MinWalSegments   = 3
	DefaultRetention = 15 * 86400.0
	MaxCompactedSpan = 31 * 86400.0
)

const crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

// ULID = 48 位毫秒时间戳 + 80 位随机数 = 128 位，编码成 26 个 Crockford Base32
// 字符（26*5 = 130 位，最高 2 位恒为 0）。字段布局保证字典序等于时间序。

func bits5(hi, lo uint64, p int) uint64 {
	if p >= 64 {
		return (hi >> uint(p-64)) & 0x1F
	}
	if p+5 <= 64 {
		return (lo >> uint(p)) & 0x1F
	}
	n := p + 5 - 64 // 落在 hi 里的位数
	high := hi & ((uint64(1) << uint(n)) - 1)
	return (high << uint(64-p)) | (lo >> uint(p))
}

func set5(hi, lo *uint64, p int, v uint64) {
	if p >= 64 {
		*hi |= (v & 0x1F) << uint(p-64)
		return
	}
	if p+5 <= 64 {
		*lo |= (v & 0x1F) << uint(p)
		return
	}
	n := p + 5 - 64
	*hi |= (v >> uint(64-p)) & ((uint64(1) << uint(n)) - 1)
	*lo |= (v & ((uint64(1) << uint(64-p)) - 1)) << uint(p)
}

// UlidEncode 由时间戳（毫秒）与 80 位随机数生成 ULID 字符串。
func UlidEncode(tsMs, rand80 uint64) (string, bool) {
	if tsMs >= 1<<48 {
		return "", false
	}
	hi := (tsMs << 16) | (rand80 >> 64)
	lo := rand80
	buf := make([]byte, 26)
	for k := 0; k < 26; k++ {
		buf[k] = crockford[bits5(hi, lo, 125-5*k)]
	}
	return string(buf), true
}

// UlidDecode 还原出 (时间戳毫秒, 80 位随机数)。
func UlidDecode(s string) (uint64, uint64, bool) {
	if len(s) != 26 {
		return 0, 0, false
	}
	var hi, lo uint64
	for k := 0; k < 26; k++ {
		idx := -1
		for i := 0; i < len(crockford); i++ {
			if crockford[i] == s[k] {
				idx = i
				break
			}
		}
		if idx < 0 {
			return 0, 0, false
		}
		set5(&hi, &lo, 125-5*k, uint64(idx))
	}
	ts := hi >> 16
	rnd := ((hi & 0xFFFF) << 64) | lo
	return ts, rnd, true
}

// MaxCompactedSpanOf 返回压缩后单块跨度上限 = min(保留期 10%, 31 天)。
func MaxCompactedSpanOf(retentionS float64) float64 {
	if 0.10*retentionS < MaxCompactedSpan {
		return 0.10 * retentionS
	}
	return MaxCompactedSpan
}

// CompactionLadder 从 2h 起按 2 倍递增，列出不超过上限的各层块时长。
func CompactionLadder(retentionS float64) []float64 {
	cap := MaxCompactedSpanOf(retentionS)
	out := []float64{}
	for d := BlockDuration; d <= cap+1e-9; d *= 2 {
		out = append(out, d)
	}
	return out
}

// NeededDiskSpace 是官方容量公式。
func NeededDiskSpace(retentionS, samplesPerS, bytesPerSample float64) float64 {
	return retentionS * samplesPerS * bytesPerSample
}

// WalSegmentsFor 返回覆盖 seconds 秒所需 WAL 段数（至少 3 段）。
func WalSegmentsFor(seconds, samplesPerS, walBytesPerSample float64) int {
	raw := seconds * samplesPerS * walBytesPerSample
	n := int(math.Ceil(raw / float64(WalSegmentSize)))
	if n < MinWalSegments {
		return MinWalSegments
	}
	return n
}

// WalShards 按 ref % workers 分片（WAL 重放并行化的分片规则）。
func WalShards(refs []int, workers int) [][]int {
	out := make([][]int, workers)
	for _, r := range refs {
		out[r%workers] = append(out[r%workers], r)
	}
	return out
}

// InvertedIndex 是 label_name=label_value → 升序 series ref 列表。
type InvertedIndex struct {
	postings map[[2]string][]int
	all      []int
}

// NewInvertedIndex 构造空索引。
func NewInvertedIndex() *InvertedIndex {
	return &InvertedIndex{postings: map[[2]string][]int{}}
}

// Add 写入一条序列的标签。
func (ix *InvertedIndex) Add(ref int, labels map[string]string) {
	ix.all = append(ix.all, ref)
	for k, v := range labels {
		key := [2]string{k, v}
		ix.postings[key] = append(ix.postings[key], ref)
		sort.Ints(ix.postings[key])
	}
}

// Postings 返回某个 label=value 的 posting list。
func (ix *InvertedIndex) Postings(label, value string) []int {
	return ix.postings[[2]string{label, value}]
}

// Intersect 返回 (命中 ref 升序列表, 比较次数)。
func (ix *InvertedIndex) Intersect(matchers [][2]string) ([]int, int) {
	if len(matchers) == 0 {
		out := append([]int{}, ix.all...)
		sort.Ints(out)
		return out, 0
	}
	lists := make([][]int, 0, len(matchers))
	for _, m := range matchers {
		lists = append(lists, ix.postings[m])
	}
	sort.Slice(lists, func(a, b int) bool { return len(lists[a]) < len(lists[b]) })
	cur := append([]int{}, lists[0]...)
	comparisons := 0
	for _, other := range lists[1:] {
		merged := []int{}
		i, j := 0, 0
		for i < len(cur) && j < len(other) {
			comparisons++
			switch {
			case cur[i] == other[j]:
				merged = append(merged, cur[i])
				i++
				j++
			case cur[i] < other[j]:
				i++
			default:
				j++
			}
		}
		cur = merged
		if len(cur) == 0 {
			break
		}
	}
	return cur, comparisons
}
