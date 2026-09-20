// Go 1.24 的 testing.B.Loop 复刻：单次执行基准函数 + 计时器自动收放 + predictN 标定。
//
// 口径（src/testing/benchmark.go 与 go.dev/doc/go1.24，本轮实读）：
//   - Go 1.24 起可以写 for b.Loop() { ... }，两个显著优势：
//     ① 每个 -count 只**执行一次**基准函数 ⇒ 昂贵的 setup/cleanup 只做一次
//     ② 循环体内函数调用的**实参和结果保持存活** ⇒ 编译器无法把循环体整体优化掉
//   - 首次调用 Loop 会 ResetTimer ⇒ setup 不计入；返回 false 时 StopTimer ⇒ cleanup 不计入
//   - 循环内 **b.N 被置 0**（源码注释：避免混淆）；返回 false 后 b.N = 总迭代次数
//   - 计时器被 StopTimer 关掉后再调 Loop ⇒ b.Fatal("B.Loop called with timer stopped")
//   - loopPoisonTimer = 1<<63：用最高位把 i 打毒，逼它走慢路径做一致性检查
//   - -benchtime=Nx（固定次数）时 loop.n 直接取 N，跑够就停，不再按时间标定
//   - predictN：先乘后除（避免快速基准丢掉数量级）→ ×1.2 → 夹到 100*last →
//     至少 last+1 → 上限 1e9；prevns==0 时按 1 处理（issue 70709）
package main

import "fmt"

const maxBenchPredictIters = 1_000_000_000

// predictN 对应 benchmark.go 的同名函数。
func predictN(goalns, prevIters, prevns, last int64) int64 {
	if prevns == 0 {
		prevns = 1
	}
	n := goalns * prevIters / prevns
	n += n / 5
	if n > 100*last {
		n = 100 * last
	}
	if n < last+1 {
		n = last + 1
	}
	if n > maxBenchPredictIters {
		n = maxBenchPredictIters
	}
	return n
}

// B 是 testing.B 与 Loop 相关状态的最小复刻（时钟用「每次迭代固定耗时」模拟）。
type B struct {
	benchNS    int64
	benchCount int64
	perIterNS  int64

	N       int
	timerOn bool
	elapsed int64
	i, n    int64
	done    bool

	bodyCalls int
	fnCalls   int
	fatal     string
}

func NewB(benchNS, benchCount, perIterNS int64) *B {
	// testing 在进入基准函数之前就把 timerOn 置位，
	// 否则 Loop 的首次慢路径会立刻撞上 "B.Loop called with timer stopped"。
	return &B{benchNS: benchNS, benchCount: benchCount, perIterNS: perIterNS, timerOn: true}
}

func (b *B) resetTimer() { b.elapsed = 0; b.timerOn = true }
func (b *B) stopTimer()  { b.timerOn = false }

func (b *B) tick() {
	b.bodyCalls++
	if b.timerOn {
		b.elapsed += b.perIterNS
	}
}

// Loop 对应 testing.B.Loop：快路径只做一次比较。
func (b *B) Loop() bool {
	if b.i < b.n {
		b.i++
		return true
	}
	return b.loopSlowPath()
}

func (b *B) loopSlowPath() bool {
	if !b.timerOn {
		b.fatal = "B.Loop called with timer stopped"
		return false
	}
	if uint64(b.i)&loopPoisonMask != 0 {
		panic(fmt.Sprintf("unknown loop stop condition: %#x", b.i))
	}
	if b.n == 0 {
		if b.benchCount > 0 {
			b.n = b.benchCount
		} else {
			b.n = 1
		}
		b.N = 0 // 循环内不使用 b.N
		b.resetTimer()
		b.i++
		return true
	}
	var more bool
	if b.benchCount > 0 {
		if b.i != b.benchCount {
			panic(fmt.Sprintf("iteration count %d < fixed target %d", b.i, b.benchCount))
		}
		more = false
	} else {
		more = b.stopOrScaleBLoop()
	}
	if !more {
		b.stopTimer()
		b.N = int(b.n)
		b.done = true
		return false
	}
	b.i++
	return true
}

func (b *B) stopOrScaleBLoop() bool {
	t := b.elapsed
	if t >= b.benchNS {
		return false
	}
	prevIters := b.n
	b.n = predictN(b.benchNS, prevIters, t, prevIters)
	return prevIters < b.n
}

// loopPoisonTimer = uint64(1 << (63 - iota))；loopPoisonMask = ^uint64((1<<(63-(iota-1)))-1)
// 用 uint64 参与常量运算，避免 1<<63 在 int64 上溢出；iota=1 时掩码正好只剩最高位。
const (
	loopPoisonTimer = uint64(1) << 63
	loopPoisonMask  = ^((uint64(1) << 63) - 1)
)

func main() {
	// 1) predictN 的四条夹紧规则
	fmt.Println("predictN(1s,1,1us,1)          =", predictN(1_000_000_000, 1, 1_000, 1))
	fmt.Println("predictN(1s,100,1ms,100)      =", predictN(1_000_000_000, 100, 1_000_000, 100))
	fmt.Println("predictN(1s,1000,100ms,1000)  =", predictN(1_000_000_000, 1_000, 100_000_000, 1_000))
	fmt.Println("predictN(1s,1e8,1ms,1e8)      =", predictN(1_000_000_000, 100_000_000, 1_000_000, 100_000_000))
	fmt.Println("predictN(1s,1,0,1)            =", predictN(1_000_000_000, 1, 0, 1))

	// 2) Loop 风格：基准函数只跑一次
	b := NewB(1_000_000, 0, 1_000)
	b.fnCalls++
	for b.Loop() {
		b.tick()
	}
	fmt.Printf("loop style: fnCalls=%d bodyCalls=%d N=%d elapsed=%d done=%v\n",
		b.fnCalls, b.bodyCalls, b.N, b.elapsed, b.done)

	// 3) 计时器被关掉后调用 Loop
	stopped := NewB(1_000_000, 0, 1_000)
	stopped.stopTimer()
	stopped.Loop()
	fmt.Println("after StopTimer:", stopped.fatal)

	// 4) 固定次数：-benchtime=5x
	fixed := NewB(1_000_000_000, 5, 1_000)
	calls := 0
	for fixed.Loop() {
		calls++
		fixed.tick()
	}
	fmt.Printf("fixed count: calls=%d N=%d done=%v timerOn=%v\n",
		calls, fixed.N, fixed.done, fixed.timerOn)
}
