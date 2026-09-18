package main

// WAL 与 ingester。
//
// 三条写入路径硬规则（官方 architecture overview）：
//  1. 同一 stream 的时间戳必须严格递增，否则该行被拒并回错给客户端。
//  2. 完全重复的行（时间戳与内容都相同）被静默忽略，不算错误。
//  3. 时间戳相同但内容不同的行会被接受 —— 同一条流里可以存在两条同 ts 的日志。
//
// WAL 的作用：崩溃后重放未刷写记录；优雅关闭则先刷写再截断 WAL。
// checkpoint_duration 默认 5m，避免重放时间随运行时长线性增长。

import "fmt"

type WALRecord struct {
	Tenant string
	Labels map[string]string
	TsNs   int64
	Line   string
}

type WAL struct {
	CheckpointDurationS float64
	Records             []WALRecord
	ReplayedFrom        int
	lastCheckpointNs    int64
	hasCheckpoint       bool
	Checkpoints         int
}

func NewWAL(checkpointDurationS float64) *WAL {
	return &WAL{CheckpointDurationS: checkpointDurationS}
}

func (w *WAL) Append(tenant string, labels map[string]string, tsNs int64, line string) {
	w.Records = append(w.Records, WALRecord{Tenant: tenant, Labels: labels, TsNs: tsNs, Line: line})
	if !w.hasCheckpoint {
		w.lastCheckpointNs = tsNs
		w.hasCheckpoint = true
	}
}

// Checkpoint 到周期则折叠一次，返回是否真的折叠了。
func (w *WAL) Checkpoint(nowNs int64) bool {
	if !w.hasCheckpoint {
		return false
	}
	if nowNs-w.lastCheckpointNs < int64(w.CheckpointDurationS*1e9) {
		return false
	}
	w.ReplayedFrom = len(w.Records)
	w.lastCheckpointNs = nowNs
	w.Checkpoints++
	return true
}

func (w *WAL) Pending() []WALRecord { return w.Records[w.ReplayedFrom:] }

// MarkFlushed 数据已确认落到对象存储（优雅关闭时全量刷写），WAL 可整体截断。
func (w *WAL) MarkFlushed() { w.ReplayedFrom = len(w.Records) }

// Ingester 单实例。内存里按 (tenant, 序列键) 维护 Stream。
type Ingester struct {
	Encoding    string
	TargetSize  int
	IdlePeriodS float64
	MaxAgeS     float64
	WAL         *WAL
	Streams     map[string]*Stream
}

func NewIngester(
	encoding string,
	targetSize int,
	idlePeriodS, maxAgeS float64,
	walEnabled bool,
) (*Ingester, error) {
	if !containsString(encodings, encoding) {
		return nil, fmt.Errorf("未知的 chunk_encoding: %q", encoding)
	}
	ing := &Ingester{
		Encoding:    encoding,
		TargetSize:  targetSize,
		IdlePeriodS: idlePeriodS,
		MaxAgeS:     maxAgeS,
		Streams:     map[string]*Stream{},
	}
	if walEnabled {
		ing.WAL = NewWAL(300)
	}
	return ing, nil
}

func containsString(haystack []string, needle string) bool {
	for _, item := range haystack {
		if item == needle {
			return true
		}
	}
	return false
}

func (ing *Ingester) Stream(tenant string, labels map[string]string) *Stream {
	key := RegistryKey(tenant, SeriesKey(labels))
	if existing, ok := ing.Streams[key]; ok {
		return existing
	}
	stream := &Stream{
		Tenant:      tenant,
		Encoding:    ing.Encoding,
		TargetSize:  ing.TargetSize,
		IdlePeriodS: ing.IdlePeriodS,
		MaxAgeS:     ing.MaxAgeS,
	}
	ing.Streams[key] = stream
	return stream
}

func (ing *Ingester) Push(tenant string, labels map[string]string, tsNs int64, line string) string {
	outcome := ing.Stream(tenant, labels).Push(tsNs, line)
	if outcome == "appended" && ing.WAL != nil {
		ing.WAL.Append(tenant, labels, tsNs, line)
	}
	return outcome
}

// Shutdown 优雅关闭：flush_on_shutdown=true 时把内存里的流全部刷写并截断 WAL。
func (ing *Ingester) Shutdown() []*Chunk {
	flushed := []*Chunk{}
	for _, stream := range ing.Streams {
		if chunk := stream.Flush("shutdown"); chunk != nil {
			flushed = append(flushed, chunk)
		}
	}
	if ing.WAL != nil {
		ing.WAL.MarkFlushed()
	}
	return flushed
}

// Crash 进程异常退出：内存里的流全部丢失，只有已落盘的 WAL 还在。
// chunk_retain_period 默认 0s，刷写后不留内存副本，因此「未刷写的 chunk」
// 在崩溃时就是真丢 —— 这就是默认 replication_factor=3 存在的理由。
func (ing *Ingester) Crash() { ing.Streams = map[string]*Stream{} }

// Recover 崩溃恢复：重放 WAL 里尚未被 flush 的记录，返回每条流恢复的条数。
func (ing *Ingester) Recover() map[string]int {
	restored := map[string]int{}
	if ing.WAL == nil {
		return restored
	}
	for _, rec := range ing.WAL.Pending() {
		ing.Stream(rec.Tenant, rec.Labels).Push(rec.TsNs, rec.Line)
		restored[RegistryKey(rec.Tenant, SeriesKey(rec.Labels))]++
	}
	return restored
}
