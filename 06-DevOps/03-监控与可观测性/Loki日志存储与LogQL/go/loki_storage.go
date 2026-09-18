package main

// Chunk 生命周期：体积与压缩、滚动条件、对象键、复制仲裁。
//
// chunk 滚动的三个条件相互独立，任一满足即刷写：空闲超时
// （chunk_idle_period 30m）、压缩后体积达标（chunk_target_size 1.5 MiB）、
// 存活超时（max_chunk_age 2h）。

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
)

var chunkDefaults = map[string]float64{
	"chunk_idle_period":  1800,
	"chunk_block_size":   262144,  // 256 KiB 未压缩上限
	"chunk_target_size":  1572864, // 1.5 MiB 压缩后目标
	"max_chunk_age":      7200,
	"concurrent_flushes": 32,
	"flush_op_timeout":   600,
}

// DefaultChunkEncoding 默认是 gzip；官方最佳实践**推荐** snappy。
// 推荐值 ≠ 默认值 —— 配置文件里不写就仍然是 gzip。
const DefaultChunkEncoding = "gzip"

var encodings = []string{
	"none", "gzip", "lz4-64k", "snappy", "lz4-256k", "lz4-1M", "lz4", "flate", "zstd",
}

// illustrativeRatio 说明性压缩比，**不是实测值**，只用来演示
// 「压缩后体积达标就滚动」这条机制。真实比值强烈依赖日志内容：官方没给数字，
// 业界公开经验是高度重复的结构化 JSON 大致 10~15 倍、自由文本大致 3~6 倍。
var illustrativeRatio = map[string]float64{
	"none": 1, "snappy": 2, "lz4": 2.2, "lz4-64k": 2, "lz4-256k": 2.4,
	"lz4-1M": 2.6, "flate": 5.5, "gzip": 6, "zstd": 6.5,
}

type streamEntry struct {
	TsNs int64
	Line string
}

type Chunk struct {
	Tenant            string
	FromNs            int64
	ToNs              int64
	Entries           int
	UncompressedBytes int
	Encoding          string
	Reason            string
}

type Stream struct {
	Tenant             string
	Encoding           string
	TargetSize         int
	IdlePeriodS        float64
	MaxAgeS            float64
	Entries            []streamEntry
	chunkStartNs       int64
	hasChunk           bool
	lastAppendNs       int64
	hasAppend          bool
	Flushed            []Chunk
	RejectedOutOfOrder int
	IgnoredDuplicates  int
}

// Push 返回 appended | duplicate | out_of_order。
// 去重只认「紧邻的上一行」，与官方描述一致（比较对象是 previous line）。
func (s *Stream) Push(tsNs int64, line string) string {
	if len(s.Entries) > 0 {
		last := s.Entries[len(s.Entries)-1]
		if tsNs < last.TsNs {
			s.RejectedOutOfOrder++
			return "out_of_order"
		}
		if tsNs == last.TsNs && line == last.Line {
			s.IgnoredDuplicates++
			return "duplicate"
		}
		// 同 ts 不同内容：合法，继续走追加
	} else {
		s.chunkStartNs = tsNs
		s.hasChunk = true
	}
	s.Entries = append(s.Entries, streamEntry{TsNs: tsNs, Line: line})
	s.lastAppendNs = tsNs
	s.hasAppend = true
	return "appended"
}

func (s *Stream) UncompressedBytes() int {
	total := 0
	for _, e := range s.Entries {
		total += len(e.Line) + 16 // 近似 per-entry 头部开销
	}
	return total
}

func (s *Stream) CompressedBytes() int {
	ratio, ok := illustrativeRatio[s.Encoding]
	if !ok || ratio <= 0 {
		ratio = 1
	}
	return int(float64(s.UncompressedBytes()) / ratio)
}

// FlushReasons 返回所有成立的条件，顺序固定为 idle → size → age。
func (s *Stream) FlushReasons(nowNs int64) []string {
	reasons := []string{}
	if len(s.Entries) == 0 {
		return reasons
	}
	if s.hasAppend && nowNs-s.lastAppendNs >= int64(s.IdlePeriodS*1e9) {
		reasons = append(reasons, "idle")
	}
	if s.CompressedBytes() >= s.TargetSize {
		reasons = append(reasons, "size")
	}
	if s.hasChunk && nowNs-s.chunkStartNs >= int64(s.MaxAgeS*1e9) {
		reasons = append(reasons, "age")
	}
	return reasons
}

func (s *Stream) Flush(reason string) *Chunk {
	if len(s.Entries) == 0 {
		return nil
	}
	chunk := Chunk{
		Tenant:            s.Tenant,
		FromNs:            s.Entries[0].TsNs,
		ToNs:              s.Entries[len(s.Entries)-1].TsNs,
		Entries:           len(s.Entries),
		UncompressedBytes: s.UncompressedBytes(),
		Encoding:          s.Encoding,
		Reason:            reason,
	}
	// chunk_retain_period 默认 0s：刷写后不再驻留内存
	s.Flushed = append(s.Flushed, chunk)
	s.Entries = nil
	s.hasChunk = false
	s.hasAppend = false
	return &chunk
}

// Fingerprint 流的指纹。Loki 用 xxhash64 并以十进制字符串呈现；这里用
// SHA-256 前 8 字节代替，只为在无第三方依赖下取得稳定短标识，不声称同值。
func Fingerprint(labels map[string]string) string {
	keys := make([]string, 0, len(labels))
	for k := range labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	for _, k := range keys {
		b.WriteString(k)
		b.WriteByte(0x01)
		b.WriteString(labels[k])
		b.WriteByte(0x00)
	}
	sum := sha256.Sum256([]byte(b.String()))
	return hex.EncodeToString(sum[:8])
}

// ChunkObjectKey 对象存储里的 chunk 键。租户前缀在最外层——存储层隔离的物理依据。
func ChunkObjectKey(tenant string, labels map[string]string, fromNs, toNs int64, checksum string) string {
	return fmt.Sprintf("%s/%s/%d:%d:%s", tenant, Fingerprint(labels), fromNs, toNs, checksum)
}

// Quorum Dynamo 风格仲裁数：floor(rf/2) + 1。rf=3 → 需要 2 个成功。
func Quorum(replicationFactor int) (int, error) {
	if replicationFactor < 1 {
		return 0, fmt.Errorf("replication_factor 必须 >= 1")
	}
	return replicationFactor/2 + 1, nil
}
