package main

// demo 520 Go 侧:四种退避策略,与 python/backoff.py 同构。
// 转写对象是 aws-samples/aws-arch-backoff-simulator 的 src/backoff_simulator.py。
// 数值权威来自 python 侧实跑;Go 侧经人工审查 + bracket/sanity/crossref。

import (
	"fmt"
	"math"
	"math/rand"
)

// Backoff 基类:expo(n) = min(cap, 2^n * base)。
type Backoff struct {
	Base float64
	Cap  float64
	Rng  *rand.Rand
}

// Expo 带封顶的指数退避。
func (b Backoff) Expo(n int) float64 {
	return math.Min(b.Cap, math.Pow(2, float64(n))*b.Base)
}

// NoBackoff 不退避。
type NoBackoff struct{ Backoff }

// Next 恒为 0。
func (b NoBackoff) Next(n int) float64 { return 0 }

// ExpoBackoff 纯指数退避(无 jitter)。
type ExpoBackoff struct{ Backoff }

// Next 等于 expo(n),是确定值。
func (b ExpoBackoff) Next(n int) float64 { return b.Expo(n) }

// ExpoBackoffEqualJitter v/2 + uniform(0, v/2)。
type ExpoBackoffEqualJitter struct{ Backoff }

// Next 至少保留一半退避。
func (b ExpoBackoffEqualJitter) Next(n int) float64 {
	v := b.Expo(n)
	return v/2 + b.Rng.Float64()*(v/2)
}

// ExpoBackoffFullJitter uniform(0, v),可以睡到接近 0。
type ExpoBackoffFullJitter struct{ Backoff }

// Next 在 [0, v) 上均匀取值。
func (b ExpoBackoffFullJitter) Next(n int) float64 {
	v := b.Expo(n)
	return b.Rng.Float64() * v
}

// ExpoBackoffDecorr 有状态:sleep = min(cap, uniform(base, sleep*3))。
// 注意 n 被完全忽略 —— 这是四种策略里唯一与尝试次数无关的一个。
type ExpoBackoffDecorr struct {
	Backoff
	Sleep float64
}

// Next 推进内部状态并返回新的 sleep。
func (b *ExpoBackoffDecorr) Next(n int) float64 {
	b.Sleep = math.Min(b.Cap, b.Rng.Float64()*(b.Sleep*3-b.Base)+b.Base)
	return b.Sleep
}

// Make 按名字构造策略。base=5、cap=2000 是 AWS 模拟器里的实际取值。
func Make(name string, base, cap float64, rng *rand.Rand) (interface {
	Next(int) float64
}, error) {
	bo := Backoff{Base: base, Cap: cap, Rng: rng}
	switch name {
	case "none":
		return NoBackoff{bo}, nil
	case "expo":
		return ExpoBackoff{bo}, nil
	case "equal":
		return ExpoBackoffEqualJitter{bo}, nil
	case "full":
		return ExpoBackoffFullJitter{bo}, nil
	case "decorr":
		return &ExpoBackoffDecorr{Backoff: bo, Sleep: base}, nil
	}
	return nil, fmt.Errorf("unknown backoff %q", name)
}
