package main

// 限额与分发器限流。
//
// 最反直觉的一条在 DistributorRateMB：**全局策略下配置的 ingestion_rate_mb
// 会被均摊到每个 distributor 实例上**，而 ingestion_burst_size_mb 不会。
// 官方原文：
//   - global: 「configuring a per-distributor local rate limiter as
//     ingestion_rate / N, where N is the number of distributor replicas」
//   - burst: 「The burst size refers to the per-distributor local rate limiter
//     even in the case of the "global" strategy」
// 所以扩容 distributor 会**降低**每实例阈值，滚动重启导致实例数下降会
// **抬高**每实例阈值 —— 这是「429 风暴常与滚动重启同时发生」的成因。

import (
	"fmt"
	"strings"
)

const MiB = 1024 * 1024

// Limits limits_config 的默认值。来源：官方配置参考手册逐项标注的 default。
type Limits struct {
	IngestionRateStrategy   string
	IngestionRateMB         float64
	IngestionBurstSizeMB    float64
	MaxLineSize             string
	MaxLineSizeTruncate     bool
	MaxLabelNameLength      int
	MaxLabelValueLength     int
	MaxLabelNamesPerSeries  int
	MaxStreamsPerUser       int
	MaxGlobalStreamsPerUser int
	ChunkIdlePeriod         string
}

// DefaultLimits 与 Python 版 DEFAULTS 保持一致，便于跨语言对照。
func DefaultLimits() Limits {
	return Limits{
		IngestionRateStrategy:   "global",
		IngestionRateMB:         4,
		IngestionBurstSizeMB:    6,
		MaxLineSize:             "256KB",
		MaxLineSizeTruncate:     false,
		MaxLabelNameLength:      1024,
		MaxLabelValueLength:     2048,
		MaxLabelNamesPerSeries:  30,
		MaxStreamsPerUser:       0,
		MaxGlobalStreamsPerUser: 5000,
		ChunkIdlePeriod:         "30m",
	}
}

// DistributorRateMB 单个 distributor 实例实际执行的本地阈值（MB/s）。
func DistributorRateMB(limitMB float64, nDistributors int, strategy string) (float64, error) {
	if nDistributors < 1 {
		return 0, fmt.Errorf("distributor 数量必须 >= 1")
	}
	switch strategy {
	case "local":
		return limitMB, nil
	case "global":
		return limitMB / float64(nDistributors), nil
	}
	return 0, fmt.Errorf("未知的 ingestion_rate_strategy: %q", strategy)
}

// DistributorBurstMB burst 不随 distributor 数量变化（官方原文明确说明）。
// 参数保留是为了让调用点显式写出「这里做过均摊判断」，避免读者误以为忘了除。
func DistributorBurstMB(burstMB float64, nDistributors int, strategy string) float64 {
	_, _ = nDistributors, strategy
	return burstMB
}

// ClusterEffectiveRateMB 集群口径的实际总限额。local 策略下是配置值的 N 倍。
func ClusterEffectiveRateMB(limitMB float64, nDistributors int, strategy string) (float64, error) {
	per, err := DistributorRateMB(limitMB, nDistributors, strategy)
	if err != nil {
		return 0, err
	}
	return per * float64(nDistributors), nil
}

// CheckLine 行长校验：ok | truncated | rejected。
func CheckLine(l Limits, lineBytes int) string {
	capacity, err := ParseBytes(l.MaxLineSize)
	if err != nil || capacity <= 0 || float64(lineBytes) <= capacity {
		return "ok"
	}
	if l.MaxLineSizeTruncate {
		return "truncated"
	}
	return "rejected"
}

// CheckLabels 标签数量与名/值长度校验。标签数量是防标签爆炸的第一道闸。
func CheckLabels(l Limits, labels map[string]string) string {
	if len(labels) > l.MaxLabelNamesPerSeries {
		return "too_many_labels"
	}
	for name, value := range labels {
		if len(name) > l.MaxLabelNameLength {
			return "label_name_too_long"
		}
		if len(value) > l.MaxLabelValueLength {
			return "label_value_too_long"
		}
	}
	return "ok"
}

// ---------------------------------------------------------------- 活跃流

type Decision struct {
	Status int
	Reason string
}

func (d Decision) Accepted() bool { return d.Status < 400 }

// StreamRegistry 按租户跟踪活跃流，执行 max_global_streams_per_user。
//
// 「活跃」的判定窗口就是 chunk_idle_period（默认 30 分钟）。这个耦合容易被
// 忽略：调大 chunk_idle_period 会同时拉长流的活跃期，从而提前撞上流数量上限。
type StreamRegistry struct {
	IdlePeriodS float64
	lastSeen    map[string]int64
}

func NewStreamRegistry(idlePeriodS float64) *StreamRegistry {
	return &StreamRegistry{IdlePeriodS: idlePeriodS, lastSeen: map[string]int64{}}
}

// RegistryKey 租户 + 序列键，拼成活跃流表的索引。
func RegistryKey(tenant, key string) string { return tenant + "|" + key }

func (r *StreamRegistry) Touch(tenant, key string, tsNs int64) {
	r.lastSeen[RegistryKey(tenant, key)] = tsNs
}

func (r *StreamRegistry) ActiveCount(tenant string, nowNs int64) int {
	window := int64(r.IdlePeriodS * 1e9)
	count := 0
	for k, ts := range r.lastSeen {
		if !strings.HasPrefix(k, tenant+"|") {
			continue
		}
		if nowNs-ts <= window {
			count++
		}
	}
	return count
}

func (r *StreamRegistry) Admit(tenant, key string, l Limits, nowNs int64) Decision {
	if _, ok := r.lastSeen[RegistryKey(tenant, key)]; ok {
		return Decision{Status: 200, Reason: "existing_stream"}
	}
	if l.MaxGlobalStreamsPerUser > 0 && r.ActiveCount(tenant, nowNs) >= l.MaxGlobalStreamsPerUser {
		return Decision{Status: 429, Reason: "stream_limit"}
	}
	return Decision{Status: 200, Reason: "new_stream"}
}
