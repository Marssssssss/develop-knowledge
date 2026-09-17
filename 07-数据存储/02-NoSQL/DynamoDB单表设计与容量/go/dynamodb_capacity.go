// dynamodb_capacity.go — DynamoDB 容量换算、单分区上限、LSI/GSI 语义(Go 实现)
//
// 权威来源:
//   - Best practices for designing and using partition keys effectively
//     https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-design.html
//     * "Every partition ... a maximum capacity of 3,000 read units per second and
//        1,000 write units per second."
//     * "One read unit represents one strongly consistent read operation per second,
//        or two eventually consistent read operations per second, for an item up to
//        4 KB in size. One write unit represents one write operation per second for
//        an item up to 1 KB in size."
//     * 20 KB 条目:一次强一致读消耗 5 个读单位,单分区可支撑 600 次/秒。
//   - Using Global Secondary Indexes / Local Secondary Indexes
//     * GSI 键值不必唯一;缺 GSI 排序键则不写索引条目;GSI 吞吐独立于主表;
//       GSI 容量不足会让主表写被限流;GSI 只支持最终一致读,且不能回主表取属性。
//     * LSI:每个分区键值最多 10 GB(含主表条目与索引条目);支持强一致读;
//       回表取非投影属性要按**整条**条目计费。
//   - Using write sharding to distribute workloads evenly(随机后缀 1..200 的官方示例)
//
// 运行: go run dynamodb_capacity.go
package main

import (
	"errors"
	"fmt"
	"os"
)

const (
	kb = 1024
	mb = 1024 * kb

	readUnitBytes  = 4 * kb
	writeUnitBytes = 1 * kb

	perPartitionRCU = 3000
	perPartitionWCU = 1000

	maxItemBytes = 400 * kb
	maxPageBytes = 1 * mb

	maxLSI = 5
	maxGSI = 20

	lsiCollectionLimit = 10 * 1024 * mb
)

func ceilDiv(n, d int) int { return (n + d - 1) / d }

// ReadUnits 返回一次读请求消耗的读单位。一致性倍数:strong 1 / eventual 0.5 /
// transactional 2(官方定价页三种口径)。
func ReadUnits(itemBytes int, consistency string) (float64, error) {
	var f float64
	switch consistency {
	case "strong":
		f = 1
	case "eventual":
		f = 0.5
	case "transactional":
		f = 2
	default:
		return 0, errors.New("unknown consistency: " + consistency)
	}
	return float64(ceilDiv(itemBytes, readUnitBytes)) * f, nil
}

// WriteUnits 返回一次写请求消耗的写单位(1 KB 粒度;事务写 ×2)。
func WriteUnits(itemBytes int, transactional bool) int {
	u := ceilDiv(itemBytes, writeUnitBytes)
	if transactional {
		return u * 2
	}
	return u
}

// BatchGetUnits —— BatchGetItem 对**每个条目**各自向上取整到 4 KB 后再相加
// (官方示例:1.5 KB + 6.5 KB 算成 12 KB,而不是 8 KB)。
func BatchGetUnits(sizes []int, consistency string) float64 {
	total := 0
	for _, s := range sizes {
		total += ceilDiv(s, readUnitBytes) * readUnitBytes
	}
	u, _ := ReadUnits(total, consistency)
	return u
}

// QueryUnits —— Query/Scan 把返回集合的**总大小**一次性向上取整(官方示例:
// 合计 40.8 KB 向上取整到 44 KB)。
func QueryUnits(totalBytes int, consistency string) float64 {
	u, _ := ReadUnits(totalBytes, consistency)
	return u
}

// ShardCount 把一个逻辑分区键的写负载摊到 N 个分区所需的分片数(推导:
// N = ceil(target / 每分区写上限);与官方随机后缀示例的 1..200 一致)。
func ShardCount(targetWCU int) int {
	if targetWCU <= perPartitionWCU {
		return 1
	}
	return ceilDiv(targetWCU, perPartitionWCU)
}

// ValidateItemSize 官方条目上限 400 KB(含属性名)。
func ValidateItemSize(n int) string {
	if n <= 0 {
		return "EmptyItem"
	}
	if n > maxItemBytes {
		return "ItemSizeTooLarge"
	}
	return ""
}

// ValidateKeys 官方:分区键 1~2048 字节;排序键 1~1024 字节。
func ValidateKeys(pkBytes, skBytes int) []string {
	out := []string{}
	if pkBytes < 1 || pkBytes > 2048 {
		out = append(out, "partition_key_length")
	}
	if skBytes != 0 && (skBytes < 1 || skBytes > 1024) {
		out = append(out, "sort_key_length")
	}
	return out
}

// HotPartition 单分区键值上的吞吐是否越过分区硬顶(自适应容量抬不动它)。
func HotPartition(opsPerSecond, limit int) bool { return opsPerSecond > limit }

// ------------------------------------------------------------------ 索引

// Index 是一个二级索引。kind 为 "LSI" 或 "GSI"。
type Index struct {
	Name       string
	Kind       string
	SkAttr     string
	Projection string // KEYS_ONLY | INCLUDE | ALL
	Included   []string
	RCU, WCU   int // GSI 自有吞吐;LSI 必须为 0(与主表共用)
}

// EntrySize 索引条目大小:ALL 与主表条目等大;KEYS_ONLY 只有键;INCLUDE 加投影属性。
func (ix Index) EntrySize(pk, sk string, attrs map[string]string, itemBytes int) int {
	if ix.Projection == "ALL" {
		return itemBytes
	}
	e := len(pk) + len(sk) + 32
	if ix.Projection == "KEYS_ONLY" {
		return e
	}
	for _, a := range ix.Included {
		e += len(attrs[a])
	}
	return e
}

// Indexed 索引键属性缺失 → 不写索引条目(官方 GSI 文档原文)。
// 口径说明:本模型对每个索引只建模一个索引键属性 SkAttr,LSI 的分区键沿用主表。
func (ix Index) Indexed(attrs map[string]string) bool { return attrs[ix.SkAttr] != "" }

// ProjectedAttrCount 官方:一个表所有二级索引的用户指定投影属性合计 ≤ 100
// (同名属性进两个索引计 2 次;KEYS_ONLY / ALL 不计入)。
func ProjectedAttrCount(ixs []Index) int {
	total := 0
	for _, ix := range ixs {
		if ix.Projection == "ALL" {
			continue
		}
		total += len(ix.Included)
	}
	return total
}

var (
	errValidation = errors.New("ValidationException")
	errThrottle   = errors.New("ProvisionedThroughputExceededException")
	errCollection = errors.New("ItemCollectionSizeLimitExceededException")
)

// ------------------------------------------------------------------ 表

// Table 同时服务"单表设计"的键空间与容量校验。
type Table struct {
	items           map[string]int    // "pk|sk" → 条目字节数
	attrs           map[string]map[string]string
	lsis            []Index
	gsis            []Index
	CollectionLimit int
}

func NewTable() *Table {
	return &Table{items: map[string]int{}, attrs: map[string]map[string]string{},
		CollectionLimit: lsiCollectionLimit}
}

// AddLSI 官方:LSI 必须建表时一起定义,且与主表共用吞吐。
func (t *Table) AddLSI(ix Index, atCreation bool) error {
	if !atCreation || len(t.lsis) >= maxLSI {
		return errValidation
	}
	if ix.RCU != 0 || ix.WCU != 0 {
		return errValidation
	}
	t.lsis = append(t.lsis, ix)
	return nil
}

// AddGSI 官方:GSI 有独立的吞吐配置(必须显式给出)。
func (t *Table) AddGSI(ix Index) error {
	if len(t.gsis) >= maxGSI {
		return errValidation
	}
	if ix.WCU == 0 {
		return errValidation
	}
	t.gsis = append(t.gsis, ix)
	return nil
}

func pkOf(key string) string {
	for i := 0; i < len(key); i++ {
		if key[i] == '|' {
			return key[:i]
		}
	}
	return key
}

func skOf(key string) string {
	for i := 0; i < len(key); i++ {
		if key[i] == '|' {
			return key[i+1:]
		}
	}
	return ""
}

// CollectionBytes 官方:同一分区键值的项目集合 = 主表条目 + 各 LSI 条目。
func (t *Table) CollectionBytes(pk string) int {
	total := 0
	for k, size := range t.items {
		if pkOf(k) != pk {
			continue
		}
		total += size
		for _, ix := range t.lsis {
			if ix.Indexed(t.attrs[k]) {
				total += ix.EntrySize(pk, skOf(k), t.attrs[k], size)
			}
		}
	}
	return total
}

// Put 写入一个条目,返回(主表写单位, 各 GSI 写单位)。
func (t *Table) Put(pk, sk string, attrs map[string]string, size int) (int, map[string]int, error) {
	if ValidateItemSize(size) != "" {
		return 0, nil, errValidation
	}
	key := pk + "|" + sk
	if len(t.lsis) > 0 {
		delta := size - t.items[key] // 不存在时为 size
		if delta > 0 && t.CollectionBytes(pk)+delta > t.CollectionLimit {
			return 0, nil, errCollection
		}
	}
	t.items[key] = size
	t.attrs[key] = attrs
	ixUnits := map[string]int{}
	for _, ix := range t.gsis {
		if !ix.Indexed(attrs) {
			continue
		}
		need := WriteUnits(ix.EntrySize(pk, sk, attrs, size), false)
		ixUnits[ix.Name] = need
		if need > ix.WCU { // 官方:GSI 容量不足 → 主表写被限流
			return 0, nil, errThrottle
		}
	}
	return WriteUnits(size, false), ixUnits, nil
}

// CheckRead 官方:GSI 只支持最终一致读;LSI 强一致读合法。
func (t *Table) CheckRead(kind string, stronglyConsistent bool) error {
	if kind == "GSI" && stronglyConsistent {
		return errValidation
	}
	return nil
}

