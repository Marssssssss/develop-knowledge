// Package main：DynamoDB 容量单位、分区上限与自适应容量模型。
// 官方口径见 README；adaptive 的调度算法官方未公布，这里选一种读法实现。
package main

import "math"

const (
	rcuPerPartition = 3000.0
	wcuPerPartition = 1000.0
	readUnitBytes   = 4096
	writeUnitBytes  = 1024
)

func wcuFor(itemBytes int) float64 {
	if itemBytes <= 0 {
		return 1
	}
	return math.Ceil(float64(itemBytes) / float64(writeUnitBytes))
}

func rcuFor(itemBytes int, consistent bool) float64 {
	if itemBytes <= 0 {
		if consistent {
			return 1
		}
		return 0.5
	}
	units := math.Ceil(float64(itemBytes) / float64(readUnitBytes))
	if consistent {
		return units
	}
	return units / 2.0
}

func maxOpsPerPartition(itemBytes int, consistent bool) float64 {
	perOp := rcuFor(itemBytes, consistent)
	if perOp <= 0 {
		return math.Inf(1)
	}
	return rcuPerPartition / perOp
}

// writeShardSuffix 官方给出的计算后缀：码点乘积 mod shards 再 +1。
func writeShardSuffix(orderID string, shards int) int {
	product := 1
	for _, r := range orderID {
		product *= int(r)
	}
	return product%shards + 1
}

func randomSuffix(day string, n int) string {
	return day + "." + itoa(n)
}

func readFanout(shards int) int { return shards }

// Table 表级容量与分区配额模型。
type Table struct {
	Partitions  int
	Provisioned float64
	Adaptive    bool
}

func (t Table) perPartitionQuota() float64 {
	return t.Provisioned / float64(t.Partitions)
}

// Serve 返回每分区实际服务量与被限流的量。
func (t Table) Serve(loads []float64) ([]float64, float64) {
	quota := t.perPartitionQuota()
	served := make([]float64, len(loads))
	total := 0.0
	for i, q := range loads {
		total += q
		served[i] = math.Min(q, math.Min(quota, rcuPerPartition))
	}
	if !t.Adaptive {
		return served, total - sum(served)
	}
	pool := 0.0
	for _, s := range served {
		pool += quota - s
	}
	for i, q := range loads {
		if served[i] < q && served[i] < rcuPerPartition {
			want := math.Min(q-served[i], rcuPerPartition-served[i])
			give := math.Min(want, pool)
			served[i] += give
			pool -= give
		}
	}
	return served, total - sum(served)
}

func sum(xs []float64) float64 {
	s := 0.0
	for _, x := range xs {
		s += x
	}
	return s
}

// isHot 热分区判据：超过均匀分布值的 (1+threshold) 倍。
func isHot(loads []float64, threshold float64) []int {
	if len(loads) == 0 {
		return nil
	}
	fair := sum(loads) / float64(len(loads))
	var hot []int
	for i, q := range loads {
		if q > fair*(1+threshold) {
			hot = append(hot, i)
		}
	}
	return hot
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf []byte
	for n > 0 {
		buf = append([]byte{byte('0' + n%10)}, buf...)
		n /= 10
	}
	if neg {
		return "-" + string(buf)
	}
	return string(buf)
}
