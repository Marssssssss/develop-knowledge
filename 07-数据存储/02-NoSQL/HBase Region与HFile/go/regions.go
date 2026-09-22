// Package main：HBase region 分裂策略与 HFile 版本（口径见 README）。
package main

const (
	defaultMaxFileSize  = int64(10 * 1024 * 1024 * 1024) // hbase.hregion.max.filesize
	defaultMemstoreFlush = int64(128 * 1024 * 1024)
	defaultJitter       = 0.25
	regionSplitLimit    = 1000
	mb                  = int64(1024 * 1024)
	maxInt64            = int64(1<<63 - 1)
)

// jitterRate 源码：jitterRate = (random.nextFloat() - 0.5) * jitter。
func jitterRate(randFloat, jitter float64) float64 {
	return (randFloat - 0.5) * jitter
}

// constantSizeThreshold 配上抖动后的实际阈值，含源码的溢出保护。
func constantSizeThreshold(maxFileSize int64, randFloat, jitter float64) int64 {
	rate := jitterRate(randFloat, jitter)
	value := int64(float64(maxFileSize) * rate)
	if rate > 0 && value > maxInt64-maxFileSize {
		return maxInt64
	}
	return maxFileSize + value
}

// IncreasingToUpperBoundSplitPolicy 默认分裂策略（0.94 起）。
type IncreasingToUpperBoundSplitPolicy struct {
	MaxFileSize int64
	InitialSize int64
}

func NewIncreasingPolicy(memstoreFlush, maxFileSize int64, override int64) *IncreasingToUpperBoundSplitPolicy {
	init := 2 * memstoreFlush
	if override > 0 {
		init = override
	}
	return &IncreasingToUpperBoundSplitPolicy{MaxFileSize: maxFileSize, InitialSize: init}
}

// SizeToCheck region 数为 0 或超过 100 时直接用最大文件大小（防数值溢出）。
func (p *IncreasingToUpperBoundSplitPolicy) SizeToCheck(count int) int64 {
	if count == 0 || count > 100 {
		return p.MaxFileSize
	}
	want := p.InitialSize * int64(count) * int64(count) * int64(count)
	if want > p.MaxFileSize {
		return p.MaxFileSize
	}
	return want
}

func (p *IncreasingToUpperBoundSplitPolicy) ShouldSplit(regionSize int64, count int) bool {
	return p.CanSplit(count) && regionSize > p.SizeToCheck(count)
}

func (p *IncreasingToUpperBoundSplitPolicy) CanSplit(count int) bool {
	return count < regionSplitLimit
}

// ConstantSizeSplitPolicy 只看最大文件大小（带抖动）。
type ConstantSizeSplitPolicy struct {
	Threshold int64
}

func NewConstantPolicy(maxFileSize int64, randFloat, jitter float64) *ConstantSizeSplitPolicy {
	return &ConstantSizeSplitPolicy{Threshold: constantSizeThreshold(maxFileSize, randFloat, jitter)}
}

func (p *ConstantSizeSplitPolicy) ShouldSplit(regionSize int64) bool {
	return regionSize > p.Threshold
}

// SplitSequence region 数逐轮 +1，返回每轮的阈值。
func SplitSequence(p *IncreasingToUpperBoundSplitPolicy, start, rounds int) [][2]int64 {
	seq := [][2]int64{}
	count := start
	for i := 0; i < rounds; i++ {
		if !p.CanSplit(count) {
			break
		}
		seq = append(seq, [2]int64{int64(count), p.SizeToCheck(count)})
		count++
	}
	return seq
}

func hfileDefaultVersion() int { return 3 }

// canWriteHFile HBase 已不能写早于默认版本（v3）的 HFile，但仍能读 v2。
func canWriteHFile(v int) bool { return v >= 3 }
