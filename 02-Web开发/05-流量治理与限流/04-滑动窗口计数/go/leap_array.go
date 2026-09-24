// Package leaparray 转写 alibaba/Sentinel 的 LeapArray 滑动窗口计数。
//
// 依据 alibaba/Sentinel@master：
//   sentinel-core/src/main/java/com/alibaba/csp/sentinel/slots/statistic/base/LeapArray.java
//   sentinel-core/src/main/java/com/alibaba/csp/sentinel/slots/statistic/base/WindowWrap.java
//
// Java 侧用 AtomicReferenceArray + CAS + updateLock，Go 侧直接用切片加单个互斥锁，
// 分支结构与判据保持逐条对应。
package leaparray

import (
	"errors"
	"sync"
)

// Bucket 是简化版 MetricBucket，只保留一个计数。
type Bucket struct {
	Value int
}

// Add 累加。
func (b *Bucket) Add(n int) { b.Value += n }

// Reset 清零。
func (b *Bucket) Reset() { b.Value = 0 }

// WindowWrap 记录窗口长度、窗口起点与桶内容。
type WindowWrap struct {
	WindowLength int
	WindowStart  int64
	Bucket       *Bucket
}

// ResetTo 复用对象并重置到新的窗口起点。
func (w *WindowWrap) ResetTo(start int64) {
	w.WindowStart = start
	w.Bucket.Reset()
}

// LeapArray 滑动窗口环形数组。
type LeapArray struct {
	SampleCount        int
	IntervalMs         int64
	WindowLength       int64
	Array              []*WindowWrap
	ResetCount         int64
	mu                 sync.Mutex
}

// New 校验 sampleCount > 0、intervalMs > 0 且能整除。
func New(sampleCount int, intervalMs int64) (*LeapArray, error) {
	if sampleCount <= 0 {
		return nil, errors.New("bucket count is invalid")
	}
	if intervalMs <= 0 {
		return nil, errors.New("total time interval of the sliding window should be positive")
	}
	if intervalMs%int64(sampleCount) != 0 {
		return nil, errors.New("time span needs to be evenly divided")
	}
	return &LeapArray{
		SampleCount:  sampleCount,
		IntervalMs:   intervalMs,
		WindowLength: intervalMs / int64(sampleCount),
		Array:        make([]*WindowWrap, sampleCount),
	}, nil
}

// CalculateTimeIdx 时间戳 → 数组下标。
func (l *LeapArray) CalculateTimeIdx(timeMs int64) int {
	return int(timeMs/l.WindowLength) % len(l.Array)
}

// CalculateWindowStart 时间戳 → 所在窗口起点。
func (l *LeapArray) CalculateWindowStart(timeMs int64) int64 {
	return timeMs - timeMs%l.WindowLength
}

// CurrentWindow 对应 Java 侧的四分支 while 循环。时间回拨时返回临时桶，不写回数组。
func (l *LeapArray) CurrentWindow(timeMs int64) *WindowWrap {
	if timeMs < 0 {
		return nil
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	idx := l.CalculateTimeIdx(timeMs)
	start := l.CalculateWindowStart(timeMs)
	old := l.Array[idx]
	if old == nil {
		w := &WindowWrap{WindowLength: int(l.WindowLength), WindowStart: start, Bucket: &Bucket{}}
		l.Array[idx] = w
		return w
	}
	if start == old.WindowStart {
		return old
	}
	if start > old.WindowStart {
		old.ResetTo(start)
		l.ResetCount++
		return old
	}
	return &WindowWrap{WindowLength: int(l.WindowLength), WindowStart: start, Bucket: &Bucket{}}
}

// IsWindowDeprecated 判据是严格大于：time - start > intervalMs 才过期。
func (l *LeapArray) IsWindowDeprecated(timeMs int64, w *WindowWrap) bool {
	return timeMs-w.WindowStart > l.IntervalMs
}

// ListValid 返回全部有效桶。
func (l *LeapArray) ListValid(timeMs int64) []*WindowWrap {
	l.mu.Lock()
	defer l.mu.Unlock()
	out := make([]*WindowWrap, 0, len(l.Array))
	for _, w := range l.Array {
		if w == nil || l.IsWindowDeprecated(timeMs, w) {
			continue
		}
		out = append(out, w)
	}
	return out
}

// Values 汇总有效桶计数。
func (l *LeapArray) Values(timeMs int64) int {
	total := 0
	for _, w := range l.ListValid(timeMs) {
		total += w.Bucket.Value
	}
	return total
}

// Add 往当前窗口累加。
func (l *LeapArray) Add(n int, timeMs int64) {
	l.CurrentWindow(timeMs).Bucket.Add(n)
}

// Limiter 基于 LeapArray 的 QPS 限流器。
type Limiter struct {
	Limit int
	Array *LeapArray
}

// NewLimiter 构造。
func NewLimiter(limit, sampleCount int, intervalMs int64) (*Limiter, error) {
	la, err := New(sampleCount, intervalMs)
	if err != nil {
		return nil, err
	}
	return &Limiter{Limit: limit, Array: la}, nil
}

// TryPass 未达阈值即放行并计数。
func (l *Limiter) TryPass(timeMs int64, n int) bool {
	if l.Array.Values(timeMs)+n <= l.Limit {
		l.Array.Add(n, timeMs)
		return true
	}
	return false
}

// FixedWindow 对照用的固定窗口计数器。
type FixedWindow struct {
	IntervalMs int64
	Start      int64
	Started    bool
	Value      int
}

// Add 累加；跨窗口时清零。
func (f *FixedWindow) Add(n int, timeMs int64) {
	if !f.Started || timeMs-f.Start >= f.IntervalMs {
		f.Start = (timeMs / f.IntervalMs) * f.IntervalMs
		f.Started = true
		f.Value = 0
	}
	f.Value += n
}

// Values 读取当前窗口计数。
func (f *FixedWindow) Values(timeMs int64) int {
	if !f.Started || timeMs-f.Start >= f.IntervalMs {
		return 0
	}
	return f.Value
}
