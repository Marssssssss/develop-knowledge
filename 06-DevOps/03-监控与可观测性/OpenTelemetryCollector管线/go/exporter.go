package main

import "math"

// ---------------------------------------------------------------- retry

// RetryPolicy exporter helper 的重试退避。
// 默认 enabled=true initial_interval=5s max_interval=30s max_elapsed_time=300s
// multiplier=1.5。max_elapsed_time=0 时官方语义为「永不停止」。
//
// 注意口径分歧:上游 exporterhelper README 现为 300s,较老的 fork(Shopify
// 快照)为 120s —— 同一份文档的不同版本差异很大,跨版本比参数前先确认版本。
type RetryPolicy struct {
	Enabled        bool
	InitialInterval float64
	MaxInterval    float64
	MaxElapsedTime float64
	Multiplier     float64
}

// NewRetryPolicy 返回官方默认值。
func NewRetryPolicy() *RetryPolicy {
	return &RetryPolicy{
		Enabled:         true,
		InitialInterval: 5,
		MaxInterval:     30,
		MaxElapsedTime:  300,
		Multiplier:      1.5,
	}
}

// Backoff 第 attempt 次重试前的等待秒数(attempt 从 1 起)。
func (r *RetryPolicy) Backoff(attempt int) float64 {
	if !r.Enabled {
		return math.Inf(1)
	}
	v := r.InitialInterval * math.Pow(r.Multiplier, float64(attempt-1))
	if v > r.MaxInterval {
		return r.MaxInterval
	}
	return v
}

// Schedule 前 n 次重试的等待序列。
func (r *RetryPolicy) Schedule(n int) []float64 {
	out := make([]float64, 0, n)
	for k := 1; k <= n; k++ {
		out = append(out, r.Backoff(k))
	}
	return out
}

// TotalWait 前 n 次重试的累计等待。
func (r *RetryPolicy) TotalWait(n int) float64 {
	sum := 0.0
	for k := 1; k <= n; k++ {
		sum += r.Backoff(k)
	}
	return sum
}

// AttemptsWithinBudget 预算内最多能重试几次;-1 表示无上限(0 = 不重试)。
func (r *RetryPolicy) AttemptsWithinBudget() int {
	if !r.Enabled {
		return 0
	}
	if r.MaxElapsedTime <= 0 {
		return -1
	}
	total, k := 0.0, 0
	for {
		next := total + r.Backoff(k+1)
		if next > r.MaxElapsedTime {
			return k
		}
		total, k = next, k+1
	}
}

// WindowCovers 给定后端故障时长,重试窗口能否覆盖到它恢复。
func (r *RetryPolicy) WindowCovers(outageS float64) bool {
	budget := r.AttemptsWithinBudget()
	if budget < 0 {
		return true
	}
	return r.TotalWait(budget) >= outageS
}

// ---------------------------------------------------------------- queue

// SendingQueue 内存发送队列。队列满且未开 block_on_overflow 时新数据被直接
// 丢弃;该丢弃发生在进入重试逻辑之前,由 otelcol_exporter_enqueue_failed_*
// 指标暴露。
//
// queue_size 默认值存在文档口径分歧:上游 README 写 5000,官网 resiliency 页
// 写「often 1000」;新增的 sizer 字段还会改变计量单位。
type SendingQueue struct {
	QueueSize       int
	NumConsumers    int
	BlockOnOverflow bool
	items           []string
	Enqueued        int
	EnqueueFailed   int
}

// NewSendingQueue 按官方默认 num_consumers=10、queue_size=5000 建队列。
func NewSendingQueue(queueSize, numConsumers int, blockOnOverflow bool) *SendingQueue {
	return &SendingQueue{QueueSize: queueSize, NumConsumers: numConsumers, BlockOnOverflow: blockOnOverflow}
}

// Len 当前队列深度。
func (q *SendingQueue) Len() int { return len(q.items) }

// CapacityLeft 剩余容量。
func (q *SendingQueue) CapacityLeft() int { return q.QueueSize - len(q.items) }

// Enqueue 返回 "ok" / "drop" / "block" 三态。
func (q *SendingQueue) Enqueue(batch string) string {
	if len(q.items) >= q.QueueSize {
		if q.BlockOnOverflow {
			return "block"
		}
		q.EnqueueFailed++
		return "drop"
	}
	q.items = append(q.items, batch)
	q.Enqueued++
	return "ok"
}

// Drain 按 num_consumers 并发消费,返回清空队列所需秒数。
func (q *SendingQueue) Drain(perBatchS float64) float64 {
	if len(q.items) == 0 {
		return 0
	}
	rounds := (len(q.items) + q.NumConsumers - 1) / q.NumConsumers
	q.items = nil
	return float64(rounds) * perBatchS
}

// SuggestedQueueSize 官方给出的估算公式:缓冲秒数 × RPS ÷ 每批请求数。
func SuggestedQueueSize(bufferSeconds, rps, perBatch float64) (int, error) {
	if perBatch <= 0 {
		return 0, cfgErr("per_batch 必须为正")
	}
	return int(bufferSeconds * rps / perBatch), nil
}

// MemoryQueueCrashLoss 纯内存队列崩溃时已入队批次的损失量(全丢)。
func MemoryQueueCrashLoss(queued int) int { return queued }

// PersistentQueue file_storage 支撑的持久化队列:进程崩溃后重启继续投递。
type PersistentQueue struct {
	*SendingQueue
	OnDisk        []string
	LoadedBatches int
}

// NewPersistentQueue 建一个带持久层的队列。
func NewPersistentQueue(queueSize int) *PersistentQueue {
	return &PersistentQueue{SendingQueue: NewSendingQueue(queueSize, 10, false)}
}

// Enqueue 入队成功时同步写一份到持久层。
func (p *PersistentQueue) Enqueue(batch string) string {
	state := p.SendingQueue.Enqueue(batch)
	if state == "ok" {
		p.OnDisk = append(p.OnDisk, batch)
	}
	return state
}

// ReloadAfterCrash 重启:把盘上批次按原序读回内存队列。
func (p *PersistentQueue) ReloadAfterCrash() int {
	p.LoadedBatches = len(p.OnDisk)
	for _, b := range p.OnDisk {
		p.SendingQueue.items = append(p.SendingQueue.items, b)
	}
	p.OnDisk = nil
	return p.LoadedBatches
}

// CrashAndRestart 崩溃后重启:内存队列全丢,再从盘上恢复。
func (p *PersistentQueue) CrashAndRestart() int {
	p.SendingQueue.items = nil
	return p.ReloadAfterCrash()
}
