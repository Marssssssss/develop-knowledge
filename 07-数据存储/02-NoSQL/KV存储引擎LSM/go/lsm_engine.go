// lsm_engine.go — LSM-Tree 存储引擎核心:WAL + memtable + 不可变 memtable + 分层 SSTable
//
// 权威来源:
//   google/leveldb  doc/impl.md
//     * log 文件约 4MB → 转成 sorted table;内存里保留 memtable 副本供读
//     * young(level-0)文件超过阈值(当前 4 个)→ 与 L1 重叠文件一起合并
//     * level-L 文件总大小超过 10^L MB 时与 L+1 重叠文件合并
//     * 每个新 L1 文件 2MB;输出 key range 覆盖超过 10 个 L+2 文件时另开新文件
//     * 压缩丢弃被覆盖的值;**只有更高编号的层没有任何文件覆盖该 key 时才丢删除标记**
//   facebook/rocksdb  wiki/Write-Stalls.md
//     * 不可变 memtable 数 >= max_write_buffer_number → 完全停止写
//     * max_write_buffer_number > 3 时提前一个 stall
//     * L0 文件数 >= level0_slowdown_writes_trigger → stall;>= level0_stop_writes_trigger → 停止
//     * 待压缩字节 >= soft/hard_pending_compaction_bytes → stall / 停止
package main

import (
	"fmt"
	"sort"
	"strings"
)

const (
	KB = 1024
	MB = 1024 * 1024

	PutKind    = 0
	DeleteKind = 1
)

// WriteStoppedError 写被完全停止:等待 flush/compaction 追上。
type WriteStoppedError struct{ Reason string }

// Error 实现 error 接口。
func (e WriteStoppedError) Error() string { return "write stopped because of " + e.Reason }

// LSMConfig 全部可调参数集中一处,避免长位置参数写错类型。
type LSMConfig struct {
	WriteBufferSize                int
	MaxWriteBufferNumber           int
	NumLevels                      int
	Level0FileNumCompactionTrigger int
	TargetFileSizeBase             int
	MaxBytesForLevelBase           int
	MaxBytesForLevelMultiplier     int
	Level0SlowdownWritesTrigger    int
	Level0StopWritesTrigger        int
	SoftPendingCompactionBytes     int
	HardPendingCompactionBytes     int
	DynamicLevelBytes              bool
}

// DefaultLSMConfig 取 LevelDB / RocksDB 的常见默认值。
func DefaultLSMConfig() LSMConfig {
	return LSMConfig{
		WriteBufferSize:                4 * MB,
		MaxWriteBufferNumber:           2,
		NumLevels:                      7,
		Level0FileNumCompactionTrigger: 4,
		TargetFileSizeBase:             2 * MB,
		MaxBytesForLevelBase:           10 * MB,
		MaxBytesForLevelMultiplier:     10,
		Level0SlowdownWritesTrigger:    20,
		Level0StopWritesTrigger:        36,
		SoftPendingCompactionBytes:     128 * MB,
		HardPendingCompactionBytes:     256 * MB,
		DynamicLevelBytes:              false,
	}
}

// LSMEngine 单机 LSM 引擎:一条写路径 + 一条后台 flush/compaction 路径。
type LSMEngine struct {
	Cfg         LSMConfig
	Mem         *MemTable
	Immutables  []*MemTable
	Levels      map[int][]*SSTable
	WalBatches  [][]Entry
	Seq         int
	NextFileNum int
	PendingCompactionBytes int
	UserBytesWritten       int
	DiskBytesWritten       int
}

// NewLSMEngine 建空引擎。
func NewLSMEngine(cfg LSMConfig) *LSMEngine {
	levels := map[int][]*SSTable{}
	for l := 0; l < cfg.NumLevels; l++ {
		levels[l] = []*SSTable{}
	}
	return &LSMEngine{
		Cfg:         cfg,
		Mem:         &MemTable{WriteBufferSize: cfg.WriteBufferSize},
		Levels:      levels,
		WalBatches:  [][]Entry{{}},
		NextFileNum: 1,
	}
}

// Put 写入一个键值。
func (e *LSMEngine) Put(key, value string) error {
	return e.write(Entry{Key: key, Kind: PutKind, Value: value})
}

// Delete 删除 = 插入一条删除标记(与 Cassandra 墓碑同构)。
func (e *LSMEngine) Delete(key string) error {
	return e.write(Entry{Key: key, Kind: DeleteKind})
}

func (e *LSMEngine) write(entry Entry) error {
	state, why := e.WriteStallState()
	if state == "stop" {
		return WriteStoppedError{Reason: why}
	}
	e.Seq++
	entry.Seq = e.Seq
	e.Mem.Add(entry)
	last := len(e.WalBatches) - 1
	e.WalBatches[last] = append(e.WalBatches[last], entry)
	e.UserBytesWritten += entry.SizeBytes()
	if e.Mem.IsFull() {
		e.RotateMemtable()
		e.BackgroundWork()
	}
	return nil
}

// WriteStallState 三层写停顿判定,顺序与官方 LOG 文案一致。
func (e *LSMEngine) WriteStallState() (string, string) {
	n := len(e.Immutables)
	if n >= e.Cfg.MaxWriteBufferNumber {
		return "stop", "too_many_immutable_memtables"
	}
	if e.Cfg.MaxWriteBufferNumber > 3 && n >= e.Cfg.MaxWriteBufferNumber-1 {
		return "stall", "too_many_immutable_memtables"
	}
	l0 := len(e.Levels[0])
	if l0 >= e.Cfg.Level0StopWritesTrigger {
		return "stop", "too_many_level0_files"
	}
	if l0 >= e.Cfg.Level0SlowdownWritesTrigger {
		return "stall", "too_many_level0_files"
	}
	if e.PendingCompactionBytes >= e.Cfg.HardPendingCompactionBytes {
		return "stop", "pending_compaction_bytes"
	}
	if e.PendingCompactionBytes >= e.Cfg.SoftPendingCompactionBytes {
		return "stall", "pending_compaction_bytes"
	}
	return "ok", ""
}

// RotateMemtable 冻结当前 memtable,新开 memtable + 新 WAL 文件。
func (e *LSMEngine) RotateMemtable() {
	e.Immutables = append(e.Immutables, e.Mem)
	e.Mem = &MemTable{WriteBufferSize: e.Cfg.WriteBufferSize}
	e.WalBatches = append(e.WalBatches, []Entry{})
}

// Get 读路径:memtable → 不可变(新→旧) → L0(新→旧) → L1..Ln。
func (e *LSMEngine) Get(key string) (string, bool) {
	if ent, ok := e.Mem.Get(key); ok {
		return valueOf(ent)
	}
	for i := len(e.Immutables) - 1; i >= 0; i-- {
		if ent, ok := e.Immutables[i].Get(key); ok {
			return valueOf(ent)
		}
	}
	for l := 0; l < e.Cfg.NumLevels; l++ {
		tables := make([]*SSTable, len(e.Levels[l]))
		copy(tables, e.Levels[l])
		sort.SliceStable(tables, func(i, j int) bool { return tables[i].Number > tables[j].Number })
		for _, t := range tables {
			if ent, ok := t.Get(key); ok {
				return valueOf(ent)
			}
		}
	}
	return "", false
}

func valueOf(ent Entry) (string, bool) {
	if ent.IsDelete() {
		return "", false
	}
	return ent.Value, true
}

// LevelSizeBytes 某层总字节数。
func (e *LSMEngine) LevelSizeBytes(level int) int {
	t := 0
	for _, s := range e.Levels[level] {
		t += s.SizeBytes()
	}
	return t
}

// TotalSizeBytes 全库总字节数。
func (e *LSMEngine) TotalSizeBytes() int {
	t := 0
	for l := 0; l < e.Cfg.NumLevels; l++ {
		t += e.LevelSizeBytes(l)
	}
	return t
}

// LevelSummary 形如 "L0=4 L1=2" 的紧凑摘要。
func (e *LSMEngine) LevelSummary() string {
	parts := []string{}
	for l := 0; l < e.Cfg.NumLevels; l++ {
		if len(e.Levels[l]) > 0 {
			parts = append(parts, fmt.Sprintf("L%d=%d", l, len(e.Levels[l])))
		}
	}
	return strings.Join(parts, " ")
}
