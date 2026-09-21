// Package yoga 复刻 facebook/yoga 的三个核心算法段：
//   PixelGrid.cpp        -> roundValueToPixelGrid
//   FlexLine.cpp         -> calculateFlexLine
//   CalculateLayout.cpp  -> distributeFreeSpaceFirstPass / SecondPass
// 无本机 Go 工具链，仅人工审查 + 括号配平校验；数值语义与 C++ 版一致。
package yoga

import "math"

// YGUndefined 是 Yoga 表示「未定义」的哨兵值（NaN）。
func YGUndefined() float64 { return math.NaN() }

func isDefined(v float64) bool { return !math.IsNaN(v) }

// inexactEquals 对应 numeric/Comparison.h：硬编码 epsilon，两个 NaN 视为相等。
func inexactEquals(a, b float64) bool {
	if isDefined(a) && isDefined(b) {
		return math.Abs(a-b) < 0.0001
	}
	return !isDefined(a) && !isDefined(b)
}

// RoundValueToPixelGrid 是 PixelGrid.cpp:15 的逐行转写。
func RoundValueToPixelGrid(value, pointScaleFactor float64, forceCeil, forceFloor bool) float64 {
	scaledValue := value * pointScaleFactor
	fractial := math.Mod(scaledValue, 1.0) // 与 C 的 fmod 同号语义
	if fractial < 0 {
		fractial++
	}
	switch {
	case inexactEquals(fractial, 0):
		scaledValue = scaledValue - fractial
	case inexactEquals(fractial, 1.0):
		scaledValue = scaledValue - fractial + 1.0
	case forceCeil:
		scaledValue = scaledValue - fractial + 1.0
	case forceFloor:
		scaledValue = scaledValue - fractial
	default:
		up := !math.IsNaN(fractial) && (fractial > 0.5 || inexactEquals(fractial, 0.5))
		if up {
			scaledValue = scaledValue - fractial + 1.0
		} else {
			scaledValue = scaledValue - fractial
		}
	}
	if math.IsNaN(scaledValue) || math.IsNaN(pointScaleFactor) {
		return YGUndefined()
	}
	if pointScaleFactor == 0 {
		return YGUndefined() // IEEE 0/0
	}
	return scaledValue / pointScaleFactor
}

// Errata 取值见 yoga/YGEnums.h。
const (
	ErrataNone                                        = 0
	ErrataStretchFlexBasis                            = 1
	ErrataAbsolutePositionWithoutInsetsExcludesPadding = 2
	ErrataAbsolutePercentAgainstInnerSize             = 4
	ErrataMinSizeUndefinedInsteadOfAuto               = 8
	ErrataFlexFirstPassUsesRunningTotals              = 16
	ErrataAll                                         = 2147483647
	ErrataClassic                                     = 2147483646
	// ErrataDefault 是 Config.h 里 errata_ 的初值。
	ErrataDefault = ErrataMinSizeUndefinedInsteadOfAuto | ErrataFlexFirstPassUsesRunningTotals
)

// HasErrata 按位判断。
func HasErrata(errata, bit int) bool { return errata&bit == bit }

// FlexItem 是一行里的弹性子项（basis 已由 computeFlexBasisForChildren 算好）。
type FlexItem struct {
	Basis   float64
	Grow    float64
	Shrink  float64
	MinMain float64 // NaN 表示未设置
	MaxMain float64
	Size    float64
}

// FlexLine 对应 algorithm/FlexLine.h 的 FlexLine + FlexLineRunningLayout。
type FlexLine struct {
	Items             []*FlexItem
	SizeConsumed      float64
	TotalGrow         float64
	TotalShrinkScaled float64
}

func boundAxis(it *FlexItem, size float64) float64 {
	if !math.IsNaN(it.MinMain) && size < it.MinMain {
		return it.MinMain
	}
	if !math.IsNaN(it.MaxMain) && size > it.MaxMain {
		return it.MaxMain
	}
	return size
}

// CalculateFlexLine 是 FlexLine.cpp:16 的简化版（不含 display/position/auto margin）。
func CalculateFlexLine(items []*FlexItem, availableMain, gap float64, wrap bool) *FlexLine {
	line := &FlexLine{}
	var sizeConsumedIncludingMin float64
	for i, child := range items {
		_ = i
		leadingGap := 0.0
		if len(line.Items) > 0 {
			leadingGap = gap
		}
		bounded := boundAxis(child, child.Basis)
		if sizeConsumedIncludingMin+bounded+leadingGap > availableMain && wrap && len(line.Items) > 0 {
			break
		}
		sizeConsumedIncludingMin += bounded + leadingGap
		line.SizeConsumed += bounded + leadingGap
		line.TotalGrow += child.Grow
		line.TotalShrinkScaled += -child.Shrink * child.Basis
		line.Items = append(line.Items, child)
	}
	// 源码只对正值抬到 1：shrink 合计恒为负，永远不会被抬起。
	if line.TotalGrow > 0 && line.TotalGrow < 1 {
		line.TotalGrow = 1
	}
	if line.TotalShrinkScaled > 0 && line.TotalShrinkScaled < 1 {
		line.TotalShrinkScaled = 1
	}
	return line
}

// DistributeFreeSpace 串起第一遍（冻结被 min/max 夹住的项）与第二遍（定尺寸）。
// 返回各子项的最终主尺寸与未被分配的自由空间。
func DistributeFreeSpace(line *FlexLine, availableMain float64, errata int) ([]float64, float64) {
	remaining := availableMain - line.SizeConsumed
	original := remaining
	useRunning := HasErrata(errata, ErrataFlexFirstPassUsesRunningTotals)

	origGrow, origShrink := line.TotalGrow, line.TotalShrinkScaled
	runGrow, runShrink := line.TotalGrow, line.TotalShrinkScaled

	frozen := make(map[*FlexItem]float64)
	delta := 0.0
	for _, child := range line.Items {
		basis := boundAxis(child, child.Basis)
		if remaining < 0 {
			scaled := -child.Shrink * basis
			if isDefined(scaled) && scaled != 0 {
				total := origShrink
				if useRunning {
					total = runShrink
				}
				base := basis + remaining/total*scaled
				bounded := boundAxis(child, base)
				if isDefined(base) && isDefined(bounded) && base != bounded {
					delta += bounded - basis
					runShrink -= -child.Shrink * basis
					frozen[child] = bounded
				}
			}
		} else if isDefined(remaining) && remaining > 0 && child.Grow != 0 {
			total := origGrow
			if useRunning {
				total = runGrow
			}
			base := basis + remaining/total*child.Grow
			bounded := boundAxis(child, base)
			if isDefined(base) && isDefined(bounded) && base != bounded {
				delta += bounded - basis
				runGrow -= child.Grow
				frozen[child] = bounded
			}
		}
	}
	remaining -= delta

	sizes := make([]float64, 0, len(line.Items))
	distributed := 0.0
	for _, child := range line.Items {
		basis := boundAxis(child, child.Basis)
		size := basis
		if v, ok := frozen[child]; ok {
			size = v
		} else if remaining < 0 {
			scaled := -child.Shrink * basis
			if scaled != 0 {
				if isDefined(runShrink) && math.Abs(runShrink) < 1e-6 {
					size = boundAxis(child, basis+scaled) // 相对 epsilon 保护
				} else {
					size = boundAxis(child, basis+remaining/runShrink*scaled)
				}
			}
		} else if isDefined(remaining) && remaining > 0 && child.Grow != 0 {
			size = boundAxis(child, basis+remaining/runGrow*child.Grow)
		}
		child.Size = size
		sizes = append(sizes, size)
		distributed += size - basis
	}
	return sizes, original - distributed
}
