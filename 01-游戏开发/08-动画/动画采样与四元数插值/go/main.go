// glTF 2.0 动画采样器三种插值模式（Appendix C）的 Go 侧实现，
// 与 python/sampler.py 同协议：STEP / LINEAR（含 rotation 的 SLERP）/ CUBICSPLINE。
package main

import (
	"errors"
	"fmt"
	"math"
)

// Interpolation 为采样器插值模式。
type Interpolation string

// 三种合法取值。
const (
	Step         Interpolation = "STEP"
	Linear       Interpolation = "LINEAR"
	CubicSpline  Interpolation = "CUBICSPLINE"
)

// Path 为动画通道的目标属性。
type Path string

// 四种合法取值。
const (
	Translation Path = "translation"
	Rotation    Path = "rotation"
	Scale       Path = "scale"
	Weights     Path = "weights"
)

// pathDim 给出各 path 对应的输出访问器分量数。
func pathDim(p Path) int {
	switch p {
	case Translation, Scale:
		return 3
	case Rotation:
		return 4
	case Weights:
		return 1
	}
	return 0
}

// Lerp 为规范 LINEAR（非 rotation）：v_t = (1-t)·v_k + t·v_{k+1}。
func Lerp(a, b []float64, t float64) []float64 {
	out := make([]float64, len(a))
	for i := range a {
		out[i] = (1-t)*a[i] + t*b[i]
	}
	return out
}

func dot(a, b []float64) float64 {
	s := 0.0
	for i := range a {
		s += a[i] * b[i]
	}
	return s
}

// Normalize 归一化四元数。
func Normalize(q []float64) ([]float64, error) {
	n := 0.0
	for _, c := range q {
		n += c * c
	}
	n = math.Sqrt(n)
	if n == 0 {
		return nil, errors.New("零四元数无法归一化")
	}
	out := make([]float64, len(q))
	for i, c := range q {
		out[i] = c / n
	}
	return out, nil
}

// Slerp 为规范 SLERP：a = arccos(|dot|)，s = sign(dot)。
// v_t = sin(a(1-t))/sin(a)·v_k + s·sin(at)/sin(a)·v_{k+1}
func Slerp(q0, q1 []float64, t float64) ([]float64, error) {
	d := dot(q0, q1)
	a := math.Acos(math.Min(1, math.Max(-1, math.Abs(d))))
	s := 1.0
	if d < 0 {
		s = -1
	}
	if math.Sin(a) == 0 { // a → 0 时退化为线性插值
		return Normalize(Lerp(q0, q1, t))
	}
	c0 := math.Sin(a*(1-t)) / math.Sin(a)
	c1 := s * math.Sin(a*t) / math.Sin(a)
	out := make([]float64, len(q0))
	for i := range q0 {
		out[i] = c0*q0[i] + c1*q1[i]
	}
	return out, nil
}

// Cubic 为规范 CUBICSPLINE：
// v_t = (2t³-3t²+1)·v_k + t_d(t³-2t²+t)·b_k + (-2t³+3t²)·v_{k+1} + t_d(t³-t²)·a_{k+1}
func Cubic(vk, bk, vk1, ak1 []float64, td, t float64) []float64 {
	h00 := 2*t*t*t - 3*t*t + 1
	h10 := t*t*t - 2*t*t + t
	h01 := -2*t*t*t + 3*t*t
	h11 := t*t*t - t*t
	out := make([]float64, len(vk))
	for i := range vk {
		out[i] = h00*vk[i] + td*h10*bk[i] + h01*vk1[i] + td*h11*ak1[i]
	}
	return out
}

// Key 为 CUBICSPLINE 的一帧：(in-tangent, value, out-tangent)。
type Key struct {
	A []float64
	V []float64
	B []float64
}

// Sampler 为 animation.sampler。
type Sampler struct {
	Times         []float64
	Values        [][]float64 // STEP / LINEAR 用
	Keys          []Key       // CUBICSPLINE 用
	Interpolation Interpolation
	Path          Path
	HasMinMax     bool
}

// Sample 在 t_c 处求值：命中关键帧直出，区间外 clamp。
func (s *Sampler) Sample(tc float64) ([]float64, error) {
	if len(s.Times) == 0 {
		return nil, errors.New("空采样器")
	}
	for i, tk := range s.Times {
		if tc == tk {
			return s.valueAt(i)
		}
	}
	if tc < s.Times[0] {
		return s.valueAt(0)
	}
	if tc > s.Times[len(s.Times)-1] {
		return s.valueAt(len(s.Times) - 1)
	}
	for i := 0; i < len(s.Times)-1; i++ {
		if tc > s.Times[i] && tc < s.Times[i+1] {
			td := s.Times[i+1] - s.Times[i]
			t := (tc - s.Times[i]) / td
			switch s.Interpolation {
			case Step:
				return s.valueAt(i)
			case Linear:
				a, _ := s.valueAt(i)
				b, _ := s.valueAt(i + 1)
				if s.Path == Rotation {
					return Slerp(a, b, t)
				}
				return Lerp(a, b, t), nil
			case CubicSpline:
				out := Cubic(s.Keys[i].V, s.Keys[i].B, s.Keys[i+1].V, s.Keys[i+1].A, td, t)
				if s.Path == Rotation { // rotation 的结果 MUST 归一化
					return Normalize(out)
				}
				return out, nil
			}
		}
	}
	return nil, errors.New("不在任何区间内")
}

func (s *Sampler) valueAt(i int) ([]float64, error) {
	if s.Interpolation == CubicSpline {
		out := make([]float64, len(s.Keys[i].V))
		copy(out, s.Keys[i].V)
		return out, nil
	}
	out := make([]float64, len(s.Values[i]))
	copy(out, s.Values[i])
	return out, nil
}

// Validate 校验插值模式/path/分量数/min-max。
func (s *Sampler) Validate() error {
	switch s.Interpolation {
	case Step, Linear, CubicSpline:
	default:
		return errors.New("interpolation 只能取 LINEAR / STEP / CUBICSPLINE")
	}
	if !s.HasMinMax {
		return errors.New("input 访问器 MUST 定义 min/max")
	}
	want := pathDim(s.Path)
	if want == 0 {
		return errors.New("非法 path")
	}
	payload := func(i int) []float64 {
		if s.Interpolation == CubicSpline {
			return s.Keys[i].V
		}
		return s.Values[i]
	}
	n := len(s.Times)
	if s.Interpolation == CubicSpline {
		if len(s.Keys) != n {
			return errors.New("input 与 output 元素数不一致")
		}
		if n < 2 {
			return errors.New("CUBICSPLINE 采样器 MUST 至少有 2 个关键帧")
		}
	} else if len(s.Values) != n {
		return errors.New("input 与 output 元素数不一致")
	}
	for i := 0; i < n; i++ {
		if len(payload(i)) != want {
			return errors.New("输出分量数与 path 不匹配")
		}
	}
	for i := 0; i+1 < n; i++ {
		if s.Times[i+1] < s.Times[i] {
			return errors.New("input 时间必须单调不减")
		}
	}
	return nil
}

func main() {
	q0 := []float64{0, 0, 0, 1}
	q90 := []float64{0, math.Sin(math.Pi / 4), 0, math.Cos(math.Pi / 4)}

	st := &Sampler{Times: []float64{0, 1, 2}, Values: [][]float64{{0}, {10}, {20}},
		Interpolation: Step, Path: Weights, HasMinMax: true}
	v, _ := st.Sample(1.5)
	fmt.Println("[STEP ] t=1.5 ->", v)
	v, _ = st.Sample(5)
	fmt.Println("[STEP ] t=5.0 ->", v)

	rot := &Sampler{Times: []float64{0, 1}, Values: [][]float64{q0, q90},
		Interpolation: Linear, Path: Rotation, HasMinMax: true}
	r, _ := rot.Sample(0.5)
	fmt.Println("[SLERP] t=0.5 ->", r)

	neg := []float64{-q90[0], -q90[1], -q90[2], -q90[3]}
	rotNeg := &Sampler{Times: []float64{0, 1}, Values: [][]float64{q0, neg},
		Interpolation: Linear, Path: Rotation, HasMinMax: true}
	r2, _ := rotNeg.Sample(0.5)
	fmt.Println("[SLERP] q1 取反后 t=0.5 ->", r2, "（最短路修正 ⇒ 与上一条一致）")

	cs := &Sampler{Times: []float64{0, 2},
		Keys:          []Key{{[]float64{0}, []float64{0}, []float64{0}}, {[]float64{0}, []float64{10}, []float64{0}}},
		Interpolation: CubicSpline, Path: Weights, HasMinMax: true}
	c, _ := cs.Sample(0.5)
	fmt.Println("[CUBIC] 零切线 t=0.5 ->", c, "（smoothstep，非 LINEAR 的 2.5）")
}
