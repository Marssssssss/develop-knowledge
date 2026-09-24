// Package warmingup 转写 Guava RateLimiter 的 SmoothBursty 与 SmoothWarmingUp。
//
// 依据 google/guava@master：
//   - guava/src/com/google/common/util/concurrent/RateLimiter.java
//   - guava/src/com/google/common/util/concurrent/SmoothRateLimiter.java
//
// 时间一律用微秒整数，permits 用 float64，与 Java 侧口径一致。
// Go 没有虚派发，故把两个抽象钩子（coolDownIntervalMicros /
// storedPermitsToWaitTime）做成 Limiter 上的函数字段，由构造函数注入。
package warmingup

// MicrosPerSecond 每秒微秒数。
const MicrosPerSecond = 1_000_000

// LongMax 对应 Java long 的上界，用于 LongMath.saturatedAdd 的钳位。
const LongMax = int64(1<<63 - 1)

// SaturatedAdd 溢出时钳到 LongMax 而不是回绕。
func SaturatedAdd(a, b int64) int64 {
	s := a + b
	if s < a { // 正溢出
		return LongMax
	}
	return s
}

// Limiter 是两种实现共享的状态机（resync + reserveEarliestAvailable）。
type Limiter struct {
	StoredPermits        float64
	MaxPermits           float64
	StableIntervalMicros float64
	NextFreeTicketMicros int64

	coolDownFn func() float64
	waitFn     func(stored, take float64) int64
}

// StoredPermitsToWaitTime 暴露给外部的代价函数（对应同名的抽象方法）。
func (l *Limiter) StoredPermitsToWaitTime(stored, take float64) int64 {
	return l.waitFn(stored, take)
}

// CoolDownIntervalMicros 暴露给外部的冷却间隔（对应同名的抽象方法）。
func (l *Limiter) CoolDownIntervalMicros() float64 { return l.coolDownFn() }

// Resync 把「闲置期间新产生的令牌」补进桶，并受 MaxPermits 钳制。
func (l *Limiter) Resync(nowMicros int64) {
	if nowMicros > l.NextFreeTicketMicros {
		gain := float64(nowMicros-l.NextFreeTicketMicros) / l.coolDownFn()
		if l.StoredPermits+gain > l.MaxPermits {
			l.StoredPermits = l.MaxPermits
		} else {
			l.StoredPermits += gain
		}
		l.NextFreeTicketMicros = nowMicros
	}
}

// Reserve 预约 required 张令牌，返回「本次可用的时刻」（即旧的 NextFreeTicketMicros）。
func (l *Limiter) Reserve(required int, nowMicros int64) int64 {
	l.Resync(nowMicros)
	moment := l.NextFreeTicketMicros
	storedToSpend := float64(required)
	if l.StoredPermits < storedToSpend {
		storedToSpend = l.StoredPermits
	}
	fresh := float64(required) - storedToSpend
	wait := l.waitFn(l.StoredPermits, storedToSpend) + int64(fresh*l.StableIntervalMicros)
	l.NextFreeTicketMicros = SaturatedAdd(l.NextFreeTicketMicros, wait)
	l.StoredPermits -= storedToSpend
	return moment
}

// Acquire 返回调用方需要睡眠的秒数（永不为负）。
func (l *Limiter) Acquire(permits int, nowMicros int64) float64 {
	moment := l.Reserve(permits, nowMicros)
	if moment-nowMicros <= 0 {
		return 0
	}
	return float64(moment-nowMicros) / MicrosPerSecond
}

// CanAcquire 对应 Guava 的 canAcquire：queryEarliestAvailable **不触发 resync**。
func (l *Limiter) CanAcquire(nowMicros, timeoutMicros int64) bool {
	return l.NextFreeTicketMicros-timeoutMicros <= nowMicros
}

// SetRate 只更新稳定间隔，子类派生量由各自的 SetRate 完成。
func (l *Limiter) setStable(qps float64, nowMicros int64) {
	l.Resync(nowMicros)
	l.StableIntervalMicros = MicrosPerSecond / qps
}

// Bursty 突发型：存储令牌免费，冷却间隔等于稳定间隔。
type Bursty struct {
	Limiter
	MaxBurstSeconds float64
}

// NewBursty 构造，默认保存 1 秒的突发额度。
func NewBursty() *Bursty {
	b := &Bursty{MaxBurstSeconds: 1.0}
	b.coolDownFn = func() float64 { return b.StableIntervalMicros }
	b.waitFn = func(stored, take float64) int64 { return 0 }
	return b
}

// SetRate 对应 SmoothBursty.doSetRate：初态 storedPermits = 0（没有突发额度）。
func (b *Bursty) SetRate(qps float64, nowMicros int64) {
	b.setStable(qps, nowMicros)
	oldMax := b.MaxPermits
	b.MaxPermits = b.MaxBurstSeconds * qps
	if oldMax == 0.0 { // initial state
		b.StoredPermits = 0.0
	} else {
		b.StoredPermits = b.StoredPermits * b.MaxPermits / oldMax
	}
}

// WarmingUp 预热型：右半段梯形积分，左半段按稳定间隔计价。
type WarmingUp struct {
	Limiter
	WarmupMicros int64
	ColdFactor   float64
	Slope        float64
	Threshold    float64
}

// NewWarmingUp 构造；RateLimiter.create 传入的 coldFactor 默认为 3.0。
func NewWarmingUp(warmupMicros int64, coldFactor float64) *WarmingUp {
	w := &WarmingUp{WarmupMicros: warmupMicros, ColdFactor: coldFactor}
	w.coolDownFn = func() float64 { return float64(w.WarmupMicros) / w.MaxPermits }
	w.waitFn = w.storedPermitsToWaitTime
	return w
}

// ColdInterval 冷启动时的单张令牌间隔 = coldFactor * stableInterval。
func (w *WarmingUp) ColdInterval() float64 {
	return w.StableIntervalMicros * w.ColdFactor
}

func (w *WarmingUp) permitsToTime(permits float64) float64 {
	return w.StableIntervalMicros + permits*w.Slope
}

func (w *WarmingUp) storedPermitsToWaitTime(stored, take float64) int64 {
	above := stored - w.Threshold
	var micros int64
	if above > 0 {
		takeAbove := above
		if take < takeAbove {
			takeAbove = take
		}
		length := w.permitsToTime(above) + w.permitsToTime(above-takeAbove)
		micros = int64(takeAbove * length / 2.0)
		take -= takeAbove
	}
	return micros + int64(w.StableIntervalMicros*take)
}

// SetRate 计算 thresholdPermits / maxPermits / slope 三个派生量；
// 初态 storedPermits = maxPermits（源码注释即 initial state is cold）。
func (w *WarmingUp) SetRate(qps float64, nowMicros int64) {
	w.setStable(qps, nowMicros)
	oldMax := w.MaxPermits
	cold := w.StableIntervalMicros * w.ColdFactor
	w.Threshold = 0.5 * float64(w.WarmupMicros) / w.StableIntervalMicros
	w.MaxPermits = w.Threshold + 2.0*float64(w.WarmupMicros)/(w.StableIntervalMicros+cold)
	w.Slope = (cold - w.StableIntervalMicros) / (w.MaxPermits - w.Threshold)
	if oldMax == 0.0 {
		w.StoredPermits = w.MaxPermits
	} else {
		w.StoredPermits = w.StoredPermits * w.MaxPermits / oldMax
	}
}
