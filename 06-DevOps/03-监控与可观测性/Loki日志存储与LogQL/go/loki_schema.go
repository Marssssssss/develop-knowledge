package main

// 存储 schema 与索引约束。
//
// 官方口径（Storage schema 文档）：
//   - 新装实例推荐且**事实上强制**的组合是 store: tsdb + schema: v13；
//   - 理由是 structured metadata 与原生 OTLP 摄入默认开启，两者要求当前
//     schema 段使用 tsdb 索引与 v13 及以上；不满足时 Loki **拒绝启动**并抛
//     `CONFIG ERROR: schema v13 is required ...` / `tsdb index type is required`；
//   - boltdb-shipper 已弃用，将在 4.0 移除，且不支持 structured metadata；
//   - tsdb 的 index.period 必须是 24h；row_shards 自 v10 起默认 16。

import (
	"fmt"
	"strconv"
	"strings"
)

const (
	SchemaStoreTSDB  = "tsdb"
	SchemaV13        = "v13"
	TSDBIndexPeriod  = "24h"
	DefaultRowShards = 16
	removedInV4Store = "boltdb-shipper"

	structuredErrFull = "CONFIG ERROR: `tsdb` index type is required to store " +
		"Structured Metadata and use native OTLP ingestion..."
	schemaV13ErrFull = "CONFIG ERROR: schema v13 is required to store " +
		"Structured Metadata and use native OTLP ingestion..."
)

// SchemaVersionNumber 把 v13 转成 13。字符串比较会把 v9 判成大于 v13。
func SchemaVersionNumber(schema string) (int, error) {
	text := strings.TrimPrefix(strings.ToLower(schema), "v")
	version, err := strconv.Atoi(text)
	if err != nil {
		return 0, fmt.Errorf("非法的 schema 版本: %q", schema)
	}
	return version, nil
}

// SchemaCheck 模拟启动期校验，返回错误串；空串表示可启动。
// 这就是「老教程的 boltdb-shipper 配置升级后起不来」的机器可读版本。
func SchemaCheck(store, schema string, allowStructuredMetadata, nativeOTLP bool) string {
	needsModern := allowStructuredMetadata || nativeOTLP
	if store == removedInV4Store {
		return "CONFIG ERROR: boltdb-shipper 已在 4.0 中移除，请迁移到 tsdb"
	}
	if needsModern && store != SchemaStoreTSDB {
		return structuredErrFull
	}
	if needsModern {
		version, err := SchemaVersionNumber(schema)
		if err != nil || version < 13 {
			return schemaV13ErrFull
		}
	}
	return ""
}

// ValidateIndexPeriod tsdb 的索引 period 必须恰好是 24h。
func ValidateIndexPeriod(store, period string) bool {
	if store != SchemaStoreTSDB {
		return true
	}
	return period == TSDBIndexPeriod
}

// EqualStrings 逐项比较两个字符串切片，顺带把 nil 与空切片视作相等
// （FlushReasons 返回空切片而非 nil，直接 reflect.DeepEqual 会误判）。
func EqualStrings(got, want []string) bool {
	if len(got) != len(want) {
		return false
	}
	for i := range got {
		if got[i] != want[i] {
			return false
		}
	}
	return true
}
