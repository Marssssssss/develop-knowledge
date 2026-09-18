package main

import (
	"strconv"
	"strings"
)

// ---------------------------------------------------------------- memory_limiter

// MemoryLimiter 周期检查进程内存:超软限拒绝入站数据并回压,超硬限强制 GC。
// 默认 check_interval=0s、limit_mib=0(视为不设限)、spike_limit_mib=limit*20%。
type MemoryLimiter struct {
	LimitMib      int
	SpikeLimitMib int
	CheckInterval float64
}

// NewMemoryLimiter spike<0 表示按默认取 limit 的 20%。
func NewMemoryLimiter(limitMib, spike int, interval float64) *MemoryLimiter {
	if spike < 0 {
		spike = int(float64(limitMib)*0.2 + 0.5)
	}
	return &MemoryLimiter{LimitMib: limitMib, SpikeLimitMib: spike, CheckInterval: interval}
}

// SoftLimitMib 软限 = 硬限 - 尖峰预留,超过即开始拒绝数据。
func (m *MemoryLimiter) SoftLimitMib() int { return m.LimitMib - m.SpikeLimitMib }

// Check 返回 (verdict, headroom);verdict 取值 "ok" / "refused" / "gc"。
// 比较为严格大于,等号边界属建模选择。
func (m *MemoryLimiter) Check(rssMib int) (string, int) {
	if m.LimitMib <= 0 {
		return "ok", 1 << 30
	}
	headroom := m.SoftLimitMib() - rssMib
	if rssMib > m.LimitMib {
		return "gc", headroom
	}
	if rssMib > m.SoftLimitMib() {
		return "refused", headroom
	}
	return "ok", headroom
}

// ---------------------------------------------------------------- batch

// BatchProcessor 把记录聚成批以减少出站调用。
// 默认 timeout=200ms、send_batch_size=8192、send_batch_max_size=0(不切分)。
type BatchProcessor struct {
	SendBatchSize int
	TimeoutMs     int
	SendBatchMax  int
	buf           []Record
	firstAddMs    int
	hasFirst      bool
	Emitted       []string
}

// NewBatchProcessor max=0 表示不切分;非 0 时必须 >= size。
func NewBatchProcessor(size, timeoutMs, max int) (*BatchProcessor, error) {
	if max != 0 && max < size {
		return nil, cfgErr("send_batch_max_size(%d) 必须 >= send_batch_size(%d)", max, size)
	}
	return &BatchProcessor{SendBatchSize: size, TimeoutMs: timeoutMs, SendBatchMax: max}, nil
}

// BufLen 当前缓冲条数。
func (b *BatchProcessor) BufLen() int { return len(b.buf) }

// Flush 排空缓冲并按 max 切分;尾批可以小于 send_batch_size。
func (b *BatchProcessor) Flush(nowMs int, reason string) [][]Record {
	var out [][]Record
	for len(b.buf) > 0 {
		n := len(b.buf)
		if b.SendBatchMax > 0 && b.SendBatchMax < n {
			n = b.SendBatchMax
		}
		out = append(out, append([]Record(nil), b.buf[:n]...))
		b.buf = b.buf[n:]
	}
	b.hasFirst = false
	if len(out) > 0 {
		parts := make([]string, 0, len(out))
		for _, batch := range out {
			parts = append(parts, strconv.Itoa(len(batch)))
		}
		b.Emitted = append(b.Emitted, reason+"["+strings.Join(parts, ",")+"]")
	}
	return out
}

// Add 逐条到达:每条都检查阈值。
func (b *BatchProcessor) Add(rec Record, nowMs int) [][]Record {
	b.buf = append(b.buf, rec)
	if !b.hasFirst {
		b.firstAddMs, b.hasFirst = nowMs, true
	}
	if len(b.buf) >= b.SendBatchSize {
		return b.Flush(nowMs, "size")
	}
	return nil
}

// Extend 逐条到达的批量形式。
func (b *BatchProcessor) Extend(recs []Record, nowMs int) [][]Record {
	var out [][]Record
	for _, r := range recs {
		out = append(out, b.Add(r, nowMs)...)
	}
	return out
}

// Push 一次投递 N 条后检查阈值。真实 Collector 的输入单元是一个 request,
// send_batch_max_size 的切分只在 Push 语义下才会发生。
func (b *BatchProcessor) Push(recs []Record, nowMs int) [][]Record {
	b.buf = append(b.buf, recs...)
	if len(recs) > 0 && !b.hasFirst {
		b.firstAddMs, b.hasFirst = nowMs, true
	}
	if len(b.buf) >= b.SendBatchSize {
		return b.Flush(nowMs, "size")
	}
	return nil
}

// Tick timeout 兜底:到点把不足一批的数据也发出去。
func (b *BatchProcessor) Tick(nowMs int) [][]Record {
	if len(b.buf) > 0 && b.hasFirst && nowMs-b.firstAddMs >= b.TimeoutMs {
		return b.Flush(nowMs, "timeout")
	}
	return nil
}
