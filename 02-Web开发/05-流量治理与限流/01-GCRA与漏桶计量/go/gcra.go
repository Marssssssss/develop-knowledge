// Package gcra 实现 GCRA 与连续状态漏桶的等价对照。
//
// 依据 ITU-T I.371 的两种等价描述（经 Wikipedia《Generic cell rate algorithm》转述）：
//   - 虚拟调度：维护理论到达时间 TAT，一致判据 ta >= TAT - tau；
//     一致时 TAT = max(TAT, ta) + T，非一致时 TAT 不变。
//   - 连续状态漏桶：桶内以 1 单位/单位时间漏水，一致信元加 T，判据 X' <= tau。
package gcra

// FixedWindow 时间桶：按窗口网格对齐，计数在窗口边界清零。
type FixedWindow struct {
	Limit       int
	Window      float64
	WindowStart float64
	Started     bool
	Count       int
}

// Arrive 返回本次是否放行。
func (f *FixedWindow) Arrive(ta float64) bool {
	if !f.Started || ta-f.WindowStart >= f.Window {
		f.WindowStart = float64(int(ta/f.Window)) * f.Window
		f.Started = true
		f.Count = 0
	}
	if f.Count+1 <= f.Limit {
		f.Count++
		return true
	}
	return false
}

// Gcra 虚拟调度形态。BufferMode 为 "itu"（扣 tau）或 "brandur"（扣 tau+T）。
type Gcra struct {
	T          float64
	Tau        float64
	Strict     bool
	BufferMode string
	Tat        float64
	HasTat     bool
}

// NewGcra 由速率与容限构造。
func NewGcra(rate, tau float64) *Gcra {
	if rate <= 0 {
		panic("rate must be positive")
	}
	return &Gcra{T: 1.0 / rate, Tau: tau, BufferMode: "itu"}
}

func (g *Gcra) buffer() float64 {
	if g.BufferMode == "brandur" {
		return g.Tau + g.T
	}
	return g.Tau
}

// NextAllowed 返回下一次允许的时刻；尚无状态时返回 ok=false。
func (g *Gcra) NextAllowed() (float64, bool) {
	if !g.HasTat {
		return 0, false
	}
	return g.Tat - g.buffer(), true
}

// Arrive 处理一次到达，返回 (是否一致, 还需等待秒数)。
func (g *Gcra) Arrive(ta float64, cost int) (bool, float64) {
	if !g.HasTat {
		g.Tat = ta + float64(cost)*g.T
		g.HasTat = true
		return true, 0
	}
	na, _ := g.NextAllowed()
	ok := ta >= na
	if g.Strict {
		ok = ta > na
	}
	if !ok {
		return false, na - ta
	}
	g.Tat = max(g.Tat, ta) + float64(cost)*g.T
	return true, 0
}

// BurstCapacity 同一瞬间最多连续通过多少次。
func (g *Gcra) BurstCapacity() int {
	probe := NewGcra(1.0/g.T, g.Tau)
	probe.Strict = g.Strict
	probe.BufferMode = g.BufferMode
	n := 0
	for {
		ok, _ := probe.Arrive(0.0, 1)
		if !ok {
			return n
		}
		n++
	}
}

// ContinuousLeakyBucket 连续状态漏桶，桶容量上界为 T + tau。
type ContinuousLeakyBucket struct {
	T    float64
	Tau  float64
	X    float64
	Lct  float64
	Init bool
}

// NewContinuousLeakyBucket 由速率与容限构造。
func NewContinuousLeakyBucket(rate, tau float64) *ContinuousLeakyBucket {
	return &ContinuousLeakyBucket{T: 1.0 / rate, Tau: tau}
}

// Arrive 处理一次到达，返回 (是否一致, 还需等待秒数)。
func (b *ContinuousLeakyBucket) Arrive(ta float64, cost int) (bool, float64) {
	if !b.Init {
		b.Init = true
		b.Lct = ta
		b.X = float64(cost) * b.T
		return true, 0
	}
	xp := b.X - (ta - b.Lct)
	if xp < 0 {
		xp = 0
	}
	if xp > b.Tau {
		return false, xp - b.Tau
	}
	b.Lct = ta
	b.X = xp + float64(cost)*b.T
	return true, 0
}

func max(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}
