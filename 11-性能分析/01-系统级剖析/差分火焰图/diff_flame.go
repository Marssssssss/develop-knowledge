// 差分火焰图（red/blue differential flame graph）—— Go 侧实现
// （无工具链，人工审查 + 机械核查）。
//
// 口径同 Python 版，来自 Brendan Gregg《Differential Flame Graphs》(2014-11-09)：
//   用 profile 2 定宽度、用 "2 − 1" 定颜色；-n 归一化、-x 剥地址、--negate 反转。
package main

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
)

type Profile map[string]float64

// Pair 是 difffolded.pl 三列输出里的 (v1, v2)。
type Pair struct{ V1, V2 float64 }

func DiffFolded(p1, p2 Profile) map[string]Pair {
	out := map[string]Pair{}
	for k, v := range p1 {
		out[k] = Pair{v, 0}
	}
	for k, v := range p2 {
		e := out[k]
		e.V2 = v
		out[k] = e
	}
	return out
}

func Sum(p Profile) float64 {
	s := 0.0
	for _, v := range p {
		s += v
	}
	return s
}

// NormalizeScale 是 `-n` 的缩放因子：把第一份总量拉平到第二份。
func NormalizeScale(p1, p2 Profile) float64 {
	s1 := Sum(p1)
	if s1 <= 0 {
		return 1
	}
	return Sum(p2) / s1
}

func Normalize(p1, p2 Profile) Profile {
	k := NormalizeScale(p1, p2)
	out := Profile{}
	for s, v := range p1 {
		out[s] = v * k
	}
	return out
}

var hexRe = regexp.MustCompile(`0x[0-9a-fA-F]+`)

// StripHex 是 `-x`：剥掉十六进制地址，避免同一函数被当成两条栈。
func StripHex(stack string) string { return hexRe.ReplaceAllString(stack, "") }

func ApplyStripHex(p Profile) Profile {
	out := Profile{}
	for s, v := range p {
		k := StripHex(s)
		out[k] += v
	}
	return out
}

// Frame 是差分火焰图的一帧。
type Frame struct {
	Stack        string
	Before, After float64
}

func (f Frame) Delta() float64 { return f.After - f.Before } // 颜色取 "2 − 1"
func (f Frame) Width() float64 { return f.After }            // 宽度取第二份

func (f Frame) Hue(maxDelta float64) string {
	if maxDelta <= 0 {
		return "white"
	}
	r := f.Delta() / maxDelta
	if r > -1e-12 && r < 1e-12 {
		return "white"
	}
	sat := r
	if sat < 0 {
		sat = -sat
	}
	if sat > 1 {
		sat = 1
	}
	if r > 0 {
		return fmt.Sprintf("red@%.3f", sat)
	}
	return fmt.Sprintf("blue@%.3f", sat)
}

func BuildFrames(d map[string]Pair, negate bool) []Frame {
	var out []Frame
	for s, pr := range d {
		if negate {
			out = append(out, Frame{s, pr.V2, pr.V1})
		} else {
			out = append(out, Frame{s, pr.V1, pr.V2})
		}
	}
	return out
}

func MaxAbsDelta(fs []Frame) float64 {
	m := 0.0
	for _, f := range fs {
		if a := f.Delta(); a < 0 {
			a = -a
		}
		if a > m {
			m = a
		}
	}
	return m
}

func TotalWidth(fs []Frame) float64 {
	s := 0.0
	for _, f := range fs {
		s += f.Width()
	}
	return s
}

// Elided 返回在 profile 1 里存在、profile 2 里彻底消失的栈占比（"X% elided"）。
func Elided(p1, p2 Profile) (count int, samples, pct float64) {
	total := Sum(p1)
	for s, v := range p1 {
		if _, ok := p2[s]; !ok {
			count++
			samples += v
		}
	}
	if total > 0 {
		pct = samples * 100 / total
	}
	return
}

func SelfDelta(stack string, d map[string]Pair) float64 {
	pr, ok := d[stack]
	if !ok {
		return 0
	}
	return pr.V2 - pr.V1 // 只看这一行，不含孩子
}

func SubtreeDelta(prefix string, d map[string]Pair) float64 {
	s := 0.0
	for k, pr := range d {
		if k == prefix || strings.HasPrefix(k, prefix+";") {
			s += pr.V2 - pr.V1
		}
	}
	return s
}

func main() {
	p1 := Profile{"func_a;func_b;func_c": 31, "func_a": 4, "func_z": 10}
	p2 := Profile{"func_a;func_b;func_c": 33, "func_a": 9, "func_new": 7}
	d := DiffFolded(p1, p2)
	fs := BuildFrames(d, false)
	md := MaxAbsDelta(fs)
	sort.Slice(fs, func(i, j int) bool { return fs[i].Stack < fs[j].Stack })
	for _, f := range fs {
		fmt.Printf("%-24s v1=%-4g v2=%-4g delta=%-5g %s\n",
			f.Stack, f.Before, f.After, f.Delta(), f.Hue(md))
	}
	fmt.Printf("总宽度=%.0f（= 第二份样本总量）\n", TotalWidth(fs))

	c, s, pct := Elided(p1, p2)
	fmt.Printf("elided: %d 条 / %.0f 样本 / %.2f%%\n", c, s, pct)

	d2 := DiffFolded(Profile{"a": 10, "a;b": 10, "a;c": 10},
		Profile{"a": 15, "a;b": 30, "a;c": 30})
	fmt.Printf("帧 a 自身 delta=%.0f，子树 delta=%.0f（颜色不含孩子）\n",
		SelfDelta("a", d2), SubtreeDelta("a", d2))

	l1, l2 := Profile{"a": 100, "b": 100}, Profile{"a": 200, "b": 200}
	fmt.Printf("-n 因子=%.1f，归一化后 a: %.0f vs %.0f\n",
		NormalizeScale(l1, l2), Normalize(l1, l2)["a"], l2["a"])
	fmt.Printf("-x: %q -> %q\n", "app;foo+0x1a2b", StripHex("app;foo+0x1a2b"))
}
