// CSS Flexbox 主轴布局原理 demo —— Go 版。
//
// 依据 W3C CSS Flexible Box Layout Module Level 1：
//   §9.3 Collect flex items into flex lines（换行收集，用 outer hypothetical main size）
//   §9.7 Resolving Flexible Lengths（弹性长度解析：冻结循环 + min/max 违约处理）
//
// 运行：go run flex_layout.go
package main

import (
	"fmt"
	"math"
)

const EPS = 1e-9

// Item 是一个 flex item 的主轴尺寸参数。
type Item struct {
	Name                      string
	FlexBase                  float64 // flex-basis（内容盒）
	Grow, Shrink              float64
	MinSize, MaxSize, Margin  float64
	Target                    float64 // §9.7 运行期状态
	Frozen                    bool
}

// NewItem 构造一个 item（max 缺省 +inf）。
func NewItem(name string, base, grow, shrink float64) *Item {
	return &Item{Name: name, FlexBase: base, Grow: grow, Shrink: shrink,
		MinSize: 0, MaxSize: math.Inf(1)}
}

func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// Hypothetical 返回 hypothetical main size（flex base 经 min/max 钳制）。
func (it *Item) Hypothetical() float64 {
	return clamp(it.FlexBase, it.MinSize, it.MaxSize)
}

func (it *Item) outerHypothetical() float64 { return it.Hypothetical() + it.Margin }
func (it *Item) outerBase() float64         { return it.FlexBase + it.Margin }
func (it *Item) outerTarget() float64       { return it.Target + it.Margin }

func (it *Item) outer() float64 {
	if it.Frozen {
		return it.outerTarget()
	}
	return it.outerBase()
}

// CollectLines 按 §9.3 把 items 收集进若干 flex line。
// 逐个收集连续项，直到下一项放不下；首项就放不下则单独成行；
// 零尺寸项会被收进上一行末尾。
func CollectLines(items []*Item, containerInner float64) [][]*Item {
	lines := [][]*Item{}
	cur := []*Item{}
	sum := 0.0
	for _, it := range items {
		h := it.outerHypothetical()
		if len(cur) == 0 {
			cur, sum = []*Item{it}, h
			continue
		}
		if sum+h <= containerInner+EPS {
			cur = append(cur, it)
			sum += h
		} else {
			lines = append(lines, cur)
			cur, sum = []*Item{it}, h
		}
	}
	if len(cur) > 0 {
		lines = append(lines, cur)
	}
	return lines
}

// ResolveFlexibleLengths 照 §9.7 的九步解析一行内各 item 的 used main size。
func ResolveFlexibleLengths(items []*Item, containerInner float64) []float64 {
	for _, it := range items {
		it.Target = it.FlexBase
		it.Frozen = false
	}

	// 1. 决定用 grow 还是 shrink
	sumHyp := 0.0
	for _, it := range items {
		sumHyp += it.outerHypothetical()
	}
	usingGrow := sumHyp < containerInner-EPS

	// 3. Size inflexible items
	for _, it := range items {
		factor := it.Shrink
		if usingGrow {
			factor = it.Grow
		}
		hypo := it.Hypothetical()
		if factor == 0 {
			it.Target, it.Frozen = hypo, true
		} else if usingGrow && it.FlexBase > hypo {
			it.Target, it.Frozen = hypo, true
		} else if !usingGrow && it.FlexBase < hypo {
			it.Target, it.Frozen = hypo, true
		}
	}

	outerSum := func() float64 {
		s := 0.0
		for _, it := range items {
			s += it.outer()
		}
		return s
	}
	// 4. initial free space
	initialFree := containerInner - outerSum()

	for {
		unfrozen := []*Item{}
		for _, it := range items {
			if !it.Frozen {
				unfrozen = append(unfrozen, it)
			}
		}
		if len(unfrozen) == 0 {
			break
		}
		remaining := containerInner - outerSum()
		sumFactors := 0.0
		for _, it := range unfrozen {
			if usingGrow {
				sumFactors += it.Grow
			} else {
				sumFactors += it.Shrink
			}
		}
		if sumFactors < 1 { // 因子之和 < 1：只用 initial free space 的相应比例
			scaled := initialFree * sumFactors
			if math.Abs(scaled) < math.Abs(remaining) {
				remaining = scaled
			}
		}
		if math.Abs(remaining) > EPS {
			if usingGrow {
				total := 0.0
				for _, it := range unfrozen {
					total += it.Grow
				}
				for _, it := range unfrozen {
					ratio := 0.0
					if total > EPS {
						ratio = it.Grow / total
					}
					it.Target = it.FlexBase + remaining*ratio
				}
			} else {
				// 收缩按 shrink × inner flex base size 加权
				scaled := map[*Item]float64{}
				total := 0.0
				for _, it := range unfrozen {
					scaled[it] = it.Shrink * it.FlexBase
					total += scaled[it]
				}
				for _, it := range unfrozen {
					ratio := 0.0
					if total > EPS {
						ratio = scaled[it] / total
					}
					it.Target = it.FlexBase - math.Abs(remaining)*ratio
				}
			}
		}
		// 5d/5e. Fix min/max violations + Freeze over-flexed items
		totalViolation := 0.0
		var minViol, maxViol []*Item
		for _, it := range unfrozen {
			clamped := clamp(it.Target, math.Max(0, it.MinSize), it.MaxSize)
			delta := clamped - it.Target
			if math.Abs(delta) > EPS {
				totalViolation += delta
				it.Target = clamped
				if delta > 0 {
					minViol = append(minViol, it)
				} else {
					maxViol = append(maxViol, it)
				}
			}
		}
		switch {
		case math.Abs(totalViolation) <= EPS:
			for _, it := range items {
				it.Frozen = true
			}
		case totalViolation > 0:
			for _, it := range minViol {
				it.Frozen = true
			}
		default:
			for _, it := range maxViol {
				it.Frozen = true
			}
		}
	}

	out := make([]float64, 0, len(items))
	for _, it := range items {
		out = append(out, it.Target)
	}
	return out
}

func show(label string, items []*Item, container float64) {
	lines := CollectLines(items, container)
	fmt.Printf("%s  容器=%.0f\n", label, container)
	for i, line := range lines {
		sizes := ResolveFlexibleLengths(line, container)
		fmt.Printf("  line%d: ", i)
		for j, it := range line {
			fmt.Printf("%s=%.2f ", it.Name, sizes[j])
		}
		fmt.Println()
	}
}

func main() {
	show("[1] grow 均分", []*Item{NewItem("a", 100, 1, 1), NewItem("b", 100, 1, 1),
		NewItem("c", 100, 1, 1)}, 600)
	show("[2] grow 1/2/1", []*Item{NewItem("a", 100, 1, 1), NewItem("b", 100, 2, 1),
		NewItem("c", 100, 1, 1)}, 600)
	show("[3] 因子和 <1", []*Item{NewItem("a", 100, 0.25, 1),
		NewItem("b", 100, 0.25, 1)}, 600)

	shrink := []*Item{NewItem("small", 100, 0, 1), NewItem("big", 300, 0, 1)}
	fmt.Println("[5] scaled shrink（收缩按 shrink×base 加权）")
	fmt.Printf("  %v\n", ResolveFlexibleLengths(shrink, 200))

	a := NewItem("A", 200, 0, 1)
	a.MinSize = 150
	fmt.Println("[6] min 违约触发冻结循环")
	fmt.Printf("  %v\n", ResolveFlexibleLengths([]*Item{a, NewItem("B", 200, 0, 1)}, 100))

	fmt.Println("[9] 换行：容器 300，四项各 120")
	wrap := []*Item{NewItem("i0", 120, 1, 1), NewItem("i1", 120, 1, 1),
		NewItem("i2", 120, 1, 1), NewItem("i3", 120, 1, 1)}
	for i, line := range CollectLines(wrap, 300) {
		names := []string{}
		for _, it := range line {
			names = append(names, it.Name)
		}
		fmt.Printf("  line%d: %v\n", i, names)
	}
}
