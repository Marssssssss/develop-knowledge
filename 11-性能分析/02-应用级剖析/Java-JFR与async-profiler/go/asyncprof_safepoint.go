// JFR 与 async-profiler 复刻：safepoint 偏差、三种 CPU 采样引擎、JFR 的 base-128 编码。
//
// 口径（本轮实读的官方资料）：
//   - async-profiler README：这是一个 **不存在 safepoint 偏差** 的低开销 Java 采样剖析器
//   - docs/CpuSamplingEngines.md：cpu(perf_events) / itimer / ctimer 三引擎对照；
//     「1 个样本 = 1 个核持续跑了 N 纳秒」；itimer 一次只投递一个信号且不按线程均分；
//     ctimer 分辨率受 jiffy(HZ) 限制；cpu 每线程一个 perf 描述符，容器里常被 paranoid/seccomp 拦
//   - docs/StackWalkingModes.md：FP / DWARF / VM Structs；4.2 起默认 vm；
//     vm 模式被 setjmp/longjmp 崩溃保护包住，能显示 Java+native+JVM stub 全栈
//   - JEP 328：≤1% 开销（SPECjbb2015）、未启用时无可测开销、
//     线程本地无锁缓冲 → 全局环形缓冲、自描述二进制用 little endian base 128
//   - JEP 349：线程本地缓冲每秒刷一次到磁盘仓库；只读取被订阅的事件
package main

import (
	"fmt"
	"sort"
)

// Line 是方法里的一行：cost 为耗时，poll 表示这里是不是 safepoint 轮询点。
type Line struct {
	No   int
	Poll bool
	Cost int
}

type Method struct{ Lines []Line }

// bounds 返回每行的 [起, 止) 区间与总时长。
func (m Method) bounds() ([][3]int, int) {
	out := make([][3]int, 0, len(m.Lines))
	t := 0
	for _, l := range m.Lines {
		out = append(out, [3]int{t, t + l.Cost, l.No})
		t += l.Cost
	}
	return out, t
}

func (m Method) lineAt(t int) (int, bool, bool) {
	bs, _ := m.bounds()
	for _, b := range bs {
		if t >= b[0] && t < b[1] {
			for _, l := range m.Lines {
				if l.No == b[2] {
					return l.No, l.Poll, true
				}
			}
		}
	}
	return 0, false, false
}

func (m Method) nextPollAfter(t int) (int, bool) {
	bs, _ := m.bounds()
	for _, b := range bs {
		if b[0] >= t {
			for _, l := range m.Lines {
				if l.No == b[2] && l.Poll {
					return l.No, true
				}
			}
		}
	}
	return 0, false
}

// SampleAsync 信号来了就地取栈 ⇒ 样本落在真正执行的那一行。
func SampleAsync(m Method, interval, total int) map[int]int {
	hist := map[int]int{}
	for t := interval / 2; t < total; t += interval {
		if no, _, ok := m.lineAt(t); ok {
			hist[no]++
		}
	}
	return hist
}

// SampleAtSafepoints 只能在 safepoint 取栈：采样点不在轮询点上就推迟到下一个轮询点，
// 后面再也没有轮询点 ⇒ 这个样本彻底丢失。
func SampleAtSafepoints(m Method, interval, total int) map[int]int {
	hist := map[int]int{}
	for t := interval / 2; t < total; t += interval {
		no, poll, ok := m.lineAt(t)
		if !ok {
			break
		}
		if poll {
			hist[no]++
			continue
		}
		if target, found := m.nextPollAfter(t); found {
			hist[target]++
		}
	}
	return hist
}

// CpuEngineSamples 复刻官方算例：2 核 × 30% × (1s/10ms) = 60 样本/秒。
func CpuEngineSamples(cores int, util float64, intervalNs int64) int {
	perCore := int64(1_000_000_000) / intervalNs
	return int(float64(cores) * util * float64(perCore))
}

// MinIntervalMs ctimer 的分辨率受 jiffy 限制：HZ=100 ⇒ 10ms，HZ=250 ⇒ 4ms。
func MinIntervalMs(hz int) int { return 1000 / hz }

// Leb128 对应 JEP 328 的 little endian base 128。
func Leb128(buf []byte, i int) (int, int) {
	val, shift, n := 0, 0, 0
	for {
		b := buf[i+n]
		val |= int(b&0x7F) << shift
		n++
		if b&0x80 == 0 {
			break
		}
		shift += 7
	}
	return val, i + n
}

func keys(m map[int]int) []int {
	out := make([]int, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Ints(out)
	return out
}

func dump(name string, m map[int]int) {
	fmt.Printf("%s:", name)
	for _, k := range keys(m) {
		fmt.Printf(" 行%d=%d", k, m[k])
	}
	fmt.Println()
}

func main() {
	hot := Method{[]Line{{10, false, 30}, {11, true, 2}, {12, false, 28}, {13, true, 2}}}
	_, total := hot.bounds()
	a := SampleAsync(hot, 10, total)
	s := SampleAtSafepoints(hot, 10, total)
	dump("async    ", a)
	dump("safepoint", s)

	native := Method{[]Line{{1, false, 100}}}
	_, nTotal := native.bounds()
	dump("async(native)    ", SampleAsync(native, 10, nTotal))
	dump("safepoint(native)", SampleAtSafepoints(native, 10, nTotal))

	fmt.Println("cpu samples 2x30% @10ms:", CpuEngineSamples(2, 0.30, 10_000_000))
	fmt.Println("min interval HZ=100/250:", MinIntervalMs(100), MinIntervalMs(250))

	raw := []byte{0x98, 0x80, 0x80, 0x00, 0x87, 0x02,
		0x95, 0xAE, 0xE4, 0xB2, 0x92, 0x03,
		0xA2, 0xF7, 0xAE, 0x9A, 0x94, 0x02,
		0x02, 0x01, 0x8D, 0x11, 0x00, 0x00}
	size, i := Leb128(raw, 0)
	id, i := Leb128(raw, i)
	ts, i := Leb128(raw, i)
	_, i = Leb128(raw, i)
	tid, i := Leb128(raw, i)
	sid, _ := Leb128(raw, i)
	fmt.Printf("JFR event: bytes=%d size=%d id=%d ts=%d tid=%d stack=%d\n",
		len(raw), size, id, ts, tid, sid)
}
