// MSDF（Multi-channel Signed Distance Field）—— Go 版。
//
// 依据 Viktor Chlumský 硕士论文《Shape Decomposition for Multi-channel Distance
// Fields》（CVUT Prague, 2015）：
//   §1.1.3 单通道 SDF 依赖插值，只能近似"变化率恒定"处 → 尖角被抹圆
//   §1.1.4 把形状分解成若干**平滑**子形状，各建距离场塞进 RGB 三通道
//   §2.5   伪距离：沿边所在直线量取、叉积定符号，抹掉最近点距离的脊线
//   §3.2.2 median-of-three：‖A‖ := median(a1,a2,a3)，穷举得最小维度 n = 3
//   §3.2   Figure 3.7 凸角/凹角的四象限编码
//   §5.1.4 GLSL：median = max(min(a,b), min(max(a,b),c))，d = median(s.rgb) − 0.5
//
// 运行：go run msdf.go
package main

import (
	"fmt"
	"math"
)

// Median3 论文 §5.1.4 给的 min/max 写法。
func Median3(a, b, c float64) float64 {
	return math.Max(math.Min(a, b), math.Min(math.Max(a, b), c))
}

// Figure 3.7：凸角 / 凹角四个象限的三通道二进制向量。
var (
	ConvexQuadrants  = [][3]float64{{1, 1, 0}, {1, 0, 0}, {0, 1, 0}, {0, 0, 0}}
	ConcaveQuadrants = [][3]float64{{1, 1, 1}, {1, 0, 1}, {1, 1, 0}, {1, 0, 0}}
)

// QuadrantInside 按 median-of-three 判定四个象限的内/外。
func QuadrantInside(q [][3]float64) []int {
	out := make([]int, 0, len(q))
	for _, v := range q {
		if Median3(v[0], v[1], v[2]) >= 1 {
			out = append(out, 1)
		} else {
			out = append(out, 0)
		}
	}
	return out
}

// SmoothPatterns 平滑通道在四象限上可能的 0/1 模式（全0/全1 + 4 个半平面）。
func SmoothPatterns() []int {
	pats := []int{0b0000, 0b1111}
	for i := 0; i < 4; i++ {
		pats = append(pats, (1<<i)|(1<<((i+1)%4)))
	}
	return pats
}

func majority(bits []int) int {
	sum := 0
	for _, b := range bits {
		sum += b
	}
	if sum*2 > len(bits) {
		return 1
	}
	return 0
}

// CanExpress n 个平滑通道的多数表决能否恰好表达 target 这个象限模式。
func CanExpress(target, n int, pats []int) bool {
	var walk func(start int, chosen []int) bool
	walk = func(start int, chosen []int) bool {
		if len(chosen) == n {
			for q := 0; q < 4; q++ {
				bits := []int{}
				for _, p := range chosen {
					bits = append(bits, (p>>q)&1)
				}
				if majority(bits) != (target>>q)&1 {
					return false
				}
			}
			return true
		}
		for _, p := range pats {
			if walk(0, append(chosen, p)) {
				return true
			}
		}
		return false
	}
	return walk(0, nil)
}

// MinDimensionForCorner 表达该角所需的最小通道数。
func MinDimensionForCorner(target int) int {
	pats := SmoothPatterns()
	for n := 1; n <= 4; n++ {
		if CanExpress(target, n, pats) {
			return n
		}
	}
	return -1
}

// PseudoDistance 点到边 AB 所在直线的有符号伪距离（叉积定符号）。
func PseudoDistance(px, py, ax, ay, bx, by float64) float64 {
	ex, ey := bx-ax, by-ay
	l := math.Hypot(ex, ey)
	if l == 0 {
		return math.Hypot(px-ax, py-ay)
	}
	return (ex*(py-ay) - ey*(px-ax)) / l
}

// TrueDistanceToSegment 点到线段的最短距离。
func TrueDistanceToSegment(px, py, ax, ay, bx, by float64) float64 {
	ex, ey := bx-ax, by-ay
	denom := ex*ex + ey*ey
	if denom == 0 {
		return math.Hypot(px-ax, py-ay)
	}
	t := math.Max(0, math.Min(1, ((px-ax)*ex+(py-ay)*ey)/denom))
	return math.Hypot(px-(ax+t*ex), py-(ay+t*ey))
}

// 被测形状：凸直角 {x > cx, y > cy}，角点刻意不对齐网格。
const cornerX, cornerY = 0.37, -0.21

// CornerSDF 真实有符号距离（内部为正）。
func CornerSDF(x, y float64) float64 {
	dx, dy := x-cornerX, y-cornerY
	switch {
	case dx > 0 && dy > 0:
		return math.Min(dx, dy)
	case dy > 0:
		return dx
	case dx > 0:
		return dy
	}
	return -math.Hypot(dx, dy)
}

// CornerMSDF 三通道：R = y−cy、G = x−cx、B 恒为外。
func CornerMSDF(x, y float64) [3]float64 {
	return [3]float64{y - cornerY, x - cornerX, -8}
}

func insideTruth(x, y float64) bool { return x > cornerX && y > cornerY }

// Field 把连续场离散采样到 N×N 网格。
type Field struct {
	N    int
	Lo   float64
	Step float64
	Data [][3]float64 // 单通道场只用 [0]
}

// NewSDFField 采样单通道 SDF。
func NewSDFField(n int, lo, hi float64) *Field {
	step := (hi - lo) / float64(n-1)
	f := &Field{N: n, Lo: lo, Step: step}
	for r := 0; r < n; r++ {
		row := make([][3]float64, n)
		for c := 0; c < n; c++ {
			row[c] = [3]float64{CornerSDF(lo + float64(c)*step), 0, 0}
		}
		f.Data = append(f.Data, row)
	}
	return f
}

// NewMSDFField 采样三通道 MSDF。
func NewMSDFField(n int, lo, hi float64) *Field {
	step := (hi - lo) / float64(n-1)
	f := &Field{N: n, Lo: lo, Step: step}
	for r := 0; r < n; r++ {
		row := make([][3]float64, n)
		for c := 0; c < n; c++ {
			row[c] = CornerMSDF(lo+float64(c)*step, lo+float64(r)*step)
		}
		f.Data = append(f.Data, row)
	}
	return f
}

// Bilinear 双线性采样（等价 GL_LINEAR）。
func (f *Field) Bilinear(x, y float64) [3]float64 {
	fx, fy := (x-f.Lo)/f.Step, (y-f.Lo)/f.Step
	c0 := int(math.Floor(fx))
	r0 := int(math.Floor(fy))
	if c0 < 0 {
		c0 = 0
	}
	if r0 < 0 {
		r0 = 0
	}
	if c0 > f.N-2 {
		c0 = f.N - 2
	}
	if r0 > f.N-2 {
		r0 = f.N - 2
	}
	tx := math.Max(0, math.Min(1, fx-float64(c0)))
	ty := math.Max(0, math.Min(1, fy-float64(r0)))
	v := [3]float64{}
	for k := 0; k < 3; k++ {
		top := f.Data[r0][c0][k]*(1-tx) + f.Data[r0][c0+1][k]*tx
		bot := f.Data[r0+1][c0][k]*(1-tx) + f.Data[r0+1][c0+1][k]*tx
		v[k] = top*(1-ty) + bot*ty
	}
	return v
}

// ReconstructErrors 统计重建结果与真值不一致的像素数。
func ReconstructErrors(f *Field, lo, hi float64, outN int, msdf bool) (int, float64) {
	step := (hi - lo) / float64(outN-1)
	wrong := 0
	maxDev := 0.0
	for r := 0; r < outN; r++ {
		y := lo + float64(r)*step
		for c := 0; c < outN; c++ {
			x := lo + float64(c)*step
			s := f.Bilinear(x, y)
			got := s[0] > 0
			if msdf {
				got = Median3(s[0], s[1], s[2]) > 0
			}
			if got != insideTruth(x, y) {
				wrong++
				dx, dy := x-cornerX, y-cornerY
				dev := math.Hypot(dx, dy)
				if (dx > 0) != (dy > 0) {
					dev = math.Min(math.Abs(dx), math.Abs(dy))
				}
				if dev > maxDev {
					maxDev = dev
				}
			}
		}
	}
	return wrong, maxDev
}

func main() {
	fmt.Println("[1] median 的 min/max 写法")
	fmt.Printf("  median3(0.2,0.9,0.5) = %.2f\n", Median3(0.2, 0.9, 0.5))

	fmt.Println("[2] Figure 3.7 象限判定")
	fmt.Printf("  凸角 median → %v（1 个内）\n", QuadrantInside(ConvexQuadrants))
	fmt.Printf("  凹角 median → %v（3 个内）\n", QuadrantInside(ConcaveQuadrants))

	fmt.Println("[3] 最小维度 n")
	fmt.Printf("  凸角 min n = %d；凹角 min n = %d → 两者都要 → 3\n",
		MinDimensionForCorner(0b0001), MinDimensionForCorner(0b1110))

	fmt.Println("[4] 伪距离 vs 真实距离（边 (0,0)→(1,0)，点 (5,3)）")
	fmt.Printf("  pseudo=%.3f true=%.3f\n",
		PseudoDistance(5, 3, 0, 0, 1, 0), TrueDistanceToSegment(5, 3, 0, 0, 1, 0))

	fmt.Println("[6] 重建：域 [−4,4]²，输出 65²")
	for _, n := range []int{5, 9, 17, 33} {
		se, sd := ReconstructErrors(NewSDFField(n, -4, 4), -4, 4, 65, false)
		me, md := ReconstructErrors(NewMSDFField(n, -4, 4), -4, 4, 65, true)
		fmt.Printf("  网格 %2d²  SDF 错 %3d（偏差 %.3f） | MSDF 错 %d（%.3f）\n",
			n, se, sd, me, md)
	}
}
