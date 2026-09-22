// Package tailsampling 转写 OpenTelemetry Collector Contrib 尾采样处理器的核心状态机。
//
// 逐条对应实读源码：
//   processor/tailsamplingprocessor/internal/idbatcher/id_batcher.go
//   processor/tailsamplingprocessor/processor.go
//   processor/tailsamplingprocessor/README.md
//
// 与 Python 版的差异按 Go 语义落地：
//   - 批号用 uint64（Go 版即如此），Stop 前 lastBatchID = math.MaxUint64；
//   - 环形槽位用 []map[string]struct{} 而不是 []set，下标取模必须在读前算好，
//     否则 proposeBatch % numBatches 在负数或溢出下会静默指错槽位。
package tailsampling

import (
	"container/list"
	"math"
)

const (
	// Sampled 表示最终采样。
	Sampled = "sampled"
	// NotSampled 表示最终不采样。
	NotSampled = "not_sampled"
	// Unspecified 表示尚未决策。
	Unspecified = "unspecified"
	// InvertedSampled 是已废弃的"反向采样"结论。
	InvertedSampled = "inverted_sample"
	// InvertedNotSampled 是已废弃的"反向不采样"结论。
	InvertedNotSampled = "inverted_not_sample"
	// Drop 是 drop 策略的结论。
	Drop = "drop"
)

// Batcher 是定长批管道：管道里压着 numBatches 个批，另有 1 个正在建的批。
type Batcher struct {
	batches     []map[string]struct{}
	current     map[string]struct{}
	takeID      uint64
	lastBatchID uint64
	stopped     bool
	capacity    int
}

// NewBatcher 建一个 numBatches 长的管道。numBatches < 1 时返回 nil（对应 ErrInvalidNumBatches）。
func NewBatcher(numBatches uint64, newBatchesInitialCapacity int) *Batcher {
	if numBatches < 1 {
		return nil
	}
	if newBatchesInitialCapacity == 0 {
		newBatchesInitialCapacity = 10
	}
	b := &Batcher{
		batches:     make([]map[string]struct{}, numBatches),
		current:     make(map[string]struct{}, newBatchesInitialCapacity),
		lastBatchID: math.MaxUint64,
		capacity:    newBatchesInitialCapacity,
	}
	for i := range b.batches {
		b.batches[i] = make(map[string]struct{})
	}
	return b
}

// NumBatches 返回管道长度。
func (b *Batcher) NumBatches() uint64 { return uint64(len(b.batches)) }

// AddToCurrentBatch 把 id 放进正在建的批，返回该批的绝对编号。
func (b *Batcher) AddToCurrentBatch(id string) uint64 {
	b.current[id] = struct{}{}
	return b.takeID + uint64(len(b.batches))
}

// MoveToEarlierBatch 把 id 往前搬 batchesFromNow 批；只会往前搬。
func (b *Batcher) MoveToEarlierBatch(id string, traceCurrentBatch, batchesFromNow uint64) uint64 {
	proposed := b.takeID + batchesFromNow
	if proposed >= traceCurrentBatch {
		return traceCurrentBatch
	}
	if traceCurrentBatch == b.takeID+uint64(len(b.batches)) {
		delete(b.current, id)
	} else {
		delete(b.batches[traceCurrentBatch%uint64(len(b.batches))], id)
	}
	b.batches[proposed%uint64(len(b.batches))][id] = struct{}{}
	return proposed
}

// RemoveFromBatch 从指定批里摘掉 id；越界是 noop。
func (b *Batcher) RemoveFromBatch(id string, batch uint64) {
	currentBatchID := b.takeID + uint64(len(b.batches))
	switch {
	case batch == currentBatchID:
		delete(b.current, id)
	case batch >= b.takeID && batch < currentBatchID:
		delete(b.batches[batch%uint64(len(b.batches))], id)
	}
}

// CloseCurrentAndTakeFirstBatch 取出管道最前的批，把正在建的批补进该槽位。
func (b *Batcher) CloseCurrentAndTakeFirstBatch() ([]string, bool) {
	if b.takeID >= b.lastBatchID {
		read := keys(b.current)
		b.current = nil
		return read, false
	}
	idx := b.takeID % uint64(len(b.batches))
	read := keys(b.batches[idx])
	if !b.stopped {
		b.batches[idx] = b.current
		b.current = make(map[string]struct{}, b.capacity)
	}
	b.takeID++
	return read, true
}

// Stop 之后不再补充新批，管道读空即止。
func (b *Batcher) Stop() {
	b.stopped = true
	b.lastBatchID = b.takeID + uint64(len(b.batches))
}

func keys(m map[string]struct{}) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

// NumDecisionBatches 就是 processor.go 里的 math.Max(1, DecisionWait.Seconds())。
func NumDecisionBatches(decisionWaitSeconds float64) uint64 {
	n := uint64(decisionWaitSeconds)
	if n < 1 {
		return 1
	}
	return n
}

// Combine 实现 Policy Decision Flow 的有序组合律。
func Combine(decisions []string) string {
	var hasDrop, hasInvNot, hasSample, hasInvSample, hasNot bool
	for _, d := range decisions {
		switch d {
		case Drop:
			hasDrop = true
		case InvertedNotSampled:
			hasInvNot = true
		case Sampled:
			hasSample = true
		case InvertedSampled:
			hasInvSample = true
		case NotSampled:
			hasNot = true
		}
	}
	switch {
	case hasDrop:
		return NotSampled
	case hasInvNot:
		return NotSampled
	case hasSample:
		return Sampled
	case hasInvSample && !hasNot:
		return Sampled
	}
	return NotSampled
}

// NumDropPolicies 只数策略列表**前缀**里的 drop 策略。
func NumDropPolicies(isDrop []bool) int {
	n := 0
	for _, d := range isDrop {
		if !d {
			break
		}
		n++
	}
	return n
}

// lru 是一套独立的 LRU。sampled 与 non_sampled **必须各用一套**——
// 共用一条淘汰链会让一边的写入把另一边的条目挤掉。
type lru struct {
	limit    int
	items    map[string]*list.Element
	order    *list.List
	decision string
}

func newLRU(limit int, decision string) *lru {
	return &lru{limit: limit, items: map[string]*list.Element{}, order: list.New(), decision: decision}
}

func (l *lru) put(id string) {
	if l.limit <= 0 {
		return
	}
	if el, ok := l.items[id]; ok {
		l.order.MoveToFront(el)
		return
	}
	l.items[id] = l.order.PushFront(id)
	for len(l.items) > l.limit {
		el := l.order.Back()
		if el == nil {
			return
		}
		l.order.Remove(el)
		delete(l.items, el.Value.(string))
	}
}

func (l *lru) get(id string) bool {
	el, ok := l.items[id]
	if !ok {
		return false
	}
	l.order.MoveToFront(el)
	return true
}

// DecisionCache 是 sampled / non_sampled 两套独立 LRU。
type DecisionCache struct {
	sampled    *lru
	nonSampled *lru
}

// NewDecisionCache 建缓存；容量为 0 表示不启用（与默认配置一致）。
func NewDecisionCache(sampledCap, nonSampledCap int) *DecisionCache {
	return &DecisionCache{
		sampled:    newLRU(sampledCap, Sampled),
		nonSampled: newLRU(nonSampledCap, NotSampled),
	}
}

// Put 写入一条决策。
func (c *DecisionCache) Put(id, decision string) {
	if decision == Sampled {
		c.sampled.put(id)
		return
	}
	c.nonSampled.put(id)
}

// Get 返回缓存中的决策，未命中返回 Unspecified。
func (c *DecisionCache) Get(id string) string {
	if c.sampled.get(id) {
		return Sampled
	}
	if c.nonSampled.get(id) {
		return NotSampled
	}
	return Unspecified
}
