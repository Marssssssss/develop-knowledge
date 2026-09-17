// cassandra_model.go — Cassandra 查询驱动建模 + 墓碑/压缩策略(Go 实现)
//
// 权威来源(apache/cassandra trunk 的文档源文件,与官网最新版同源):
//   - developing/data-modeling/intro.adoc
//     * "In Cassandra, data modeling is query-driven."
//     * "the first field or component of a primary key is hashed to generate the
//        partition key and the remaining fields or components are the clustering keys"
//     * 分区规模指引:值个数 below 100,000、磁盘 below 100MB
//     * "LWT transactions ... should be kept to the minimum."
//   - managing/operating/compaction/tombstones.adoc
//     * "Cassandra treats a deletion as an insertion, and inserts a time-stamped
//        deletion marker called a tombstone."
//     * gc_grace_seconds 默认 864000 秒(十天);宽限期内墓碑不会被压缩清掉
//     * 清除条件:墓碑老于 gc_grace **且** 所有含该分区更旧数据的 SSTable
//       都在同一次压缩里
//     * 三节点僵尸(resurrection)示例
//   - managing/operating/compaction/{overview,stcs,lcs,twcs}.adoc
//     * STCS:攒够 4 个大小相近的 SSTable 触发,分桶区间 [avg×0.5, avg×1.5]
//     * LCS:每层是上一层 10 倍;L0 超过 32 个 SSTable 则先在 L0 走 STCS
//     * TWCS:按时间窗口分桶,窗口结束后不再压缩该窗口
//
// 运行: go run cassandra_model.go cassandra_model_check.go
package main

import (
	"fmt"
	"strings"
)

const (
	DefaultGCGraceSeconds = 864000 // 官方默认宽限期:10 天
	MaxPartitionValues    = 100000
	MaxPartitionBytes     = 100 * 1024 * 1024
	StcsMinThreshold      = 4
	LcsL0StcsTrigger      = 32
	LevelMultiplier       = 10
)

// ---------------------------------------------------------------- 主键与建模

// ParsePrimaryKey 解析 `PRIMARY KEY (...)` 的内容,返回(分区键字段, 聚簇键字段)。
// 官方:第一个分量(可含括号合成复合分区键)生成分区键,其余是分区内排序用的聚簇键。
func ParsePrimaryKey(expr string) ([]string, []string) {
	text := strings.TrimSpace(expr)
	pk := []string{}
	rest := text
	if strings.HasPrefix(text, "(") {
		depth := 0
		for i := 0; i < len(text); i++ {
			if text[i] == '(' {
				depth++
			} else if text[i] == ')' {
				depth--
				if depth == 0 {
					pk = splitFields(text[1:i])
					rest = strings.TrimPrefix(text[i+1:], ",")
					break
				}
			}
		}
	} else {
		parts := strings.SplitN(text, ",", 2)
		pk = append(pk, strings.TrimSpace(parts[0]))
		if len(parts) == 2 {
			rest = parts[1]
		}
	}
	return pk, splitFields(rest)
}

func splitFields(s string) []string {
	out := []string{}
	for _, f := range strings.Split(s, ",") {
		f = strings.TrimSpace(f)
		if f != "" {
			out = append(out, f)
		}
	}
	return out
}

// Schema 描述一张表的主键结构。
type Schema struct {
	Name         string
	PartitionKey []string
	Clustering   []string
	LWTOps       int
}

// NeedsFiltering 分区键未全部等值绑定时为 true —— 无法定位到单个分区,
// 官方要求"把查询涉及的分区数压到最小"。
func (s Schema) NeedsFiltering(eq []string) bool {
	for _, k := range s.PartitionKey {
		found := false
		for _, e := range eq {
			if e == k {
				found = true
			}
		}
		if !found {
			return true
		}
	}
	return false
}

// PartitionWarnings 官方两把尺子:分区内值个数与分区磁盘大小。
func PartitionWarnings(values, diskBytes int) []string {
	out := []string{}
	if values > MaxPartitionValues {
		out = append(out, "partition_values_over_100k")
	}
	if diskBytes > MaxPartitionBytes {
		out = append(out, "partition_disk_over_100mb")
	}
	return out
}

// ---------------------------------------------------------------- 墓碑与读路径

// Cell 是一个带时间戳的单元格;Tombstone 为 true 表示它是删除标记。
type Cell struct {
	Ts        int
	Value     string
	Tombstone bool
	TTL       int
	DeletedAt int
}

// Row 是某分区键/聚簇键下的一个版本。
type Row struct {
	PK, CK string
	Cell   Cell
}

// Read 返回可见值;墓碑会遮住时间戳更早的值(官方原文)。
func Read(rows []Row, pk, ck string) (string, bool) {
	best := -1
	for i, r := range rows {
		if r.PK != pk || r.CK != ck {
			continue
		}
		if best < 0 || r.Cell.Ts > rows[best].Cell.Ts {
			best = i
		}
	}
	if best < 0 || rows[best].Cell.Tombstone {
		return "", false
	}
	return rows[best].Cell.Value, true
}

// Sstable 是不可变文件。
type Sstable struct {
	Name string
	Rows []Row
}

func (s Sstable) Keys() map[string]bool {
	out := map[string]bool{}
	for _, r := range s.Rows {
		out[r.PK] = true
	}
	return out
}

// HasDataOlderThan 本文件是否含该分区、且比给定时间戳更旧的数据。
func (s Sstable) HasDataOlderThan(pk string, ts int) bool {
	for _, r := range s.Rows {
		if r.PK == pk && r.Cell.Ts < ts {
			return true
		}
	}
	return false
}

// ExpiredOnly 只剩墓碑 / 过期 TTL 数据(官方 fully expired SSTable 口径)。
func (s Sstable) ExpiredOnly(now int) bool {
	for _, r := range s.Rows {
		if r.Cell.Tombstone {
			continue
		}
		if r.Cell.TTL == 0 || r.Cell.Ts+r.Cell.TTL > now {
			return false
		}
	}
	return true
}

// Purgeable 判断墓碑能否在本次压缩后被删除。官方三个条件必须同时满足。
func Purgeable(tomb Cell, now int, partition string, compacting, all []Sstable,
	repaired, onlyPurgeRepaired bool, gcGrace int) (bool, string) {
	base := tomb.DeletedAt
	if base == 0 {
		base = tomb.Ts
	}
	if now-base <= gcGrace {
		return false, "within_grace_period"
	}
	if onlyPurgeRepaired && !repaired {
		return false, "only_purge_repaired_tombstones"
	}
	inCompaction := map[string]bool{}
	for _, t := range compacting {
		inCompaction[t.Name] = true
	}
	for _, t := range all {
		if inCompaction[t.Name] {
			continue
		}
		if t.HasDataOlderThan(partition, tomb.Ts) {
			return false, "shadowing_sstable_outside_compaction: " + t.Name
		}
	}
	return true, "purgeable"
}

// ---------------------------------------------------------------- 压缩策略

// StcsTrigger 返回可合并的下标;攒够 min 个"大小相近"的 SSTable 才触发。
// 官方分桶口径:大小落在 [平均×bucket_low, 平均×bucket_high]。
func StcsTrigger(sizes []int, min int) []int {
	if len(sizes) < min {
		return nil
	}
	sum := 0
	for _, s := range sizes {
		sum += s
	}
	avg := float64(sum) / float64(len(sizes))
	out := []int{}
	for i, s := range sizes {
		if float64(s) >= avg*0.5 && float64(s) <= avg*1.5 {
			out = append(out, i)
		}
	}
	if len(out) < min {
		return nil
	}
	return out
}

// LcsTargets 每层目标是上一层的 10 倍(官方原文)。
func LcsTargets(levels, base int) []int {
	out := make([]int, levels)
	v := base
	for i := 0; i < levels; i++ {
		out[i] = v
		v *= LevelMultiplier
	}
	return out
}

// L0Failsafe 官方:L0 超过 32 个 SSTable 时先在 L0 走一次 STCS。
func L0Failsafe(l0Count int) string {
	if l0Count > LcsL0StcsTrigger {
		return "stcs_in_l0"
	}
	return "lcs"
}

// TwcsWindows 按时间窗口分桶。
func TwcsWindows(timestamps []int, windowSeconds int) map[int][]int {
	out := map[int][]int{}
	for _, ts := range timestamps {
		b := ts / windowSeconds
		out[b] = append(out[b], ts)
	}
	return out
}

// ---------------------------------------------------------------- 自检

var pass, fail int

func check(label string, cond bool, detail ...string) {
	if cond {
		pass++
		fmt.Println("  PASS ", label)
		return
	}
	fail++
	extra := ""
	if len(detail) > 0 {
		extra = detail[0]
	}
	fmt.Println("  FAIL ", label, extra)
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
