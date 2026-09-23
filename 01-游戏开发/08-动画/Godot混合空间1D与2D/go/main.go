// Godot 4 BlendSpace1D / BlendSpace2D 权重计算的 Go 侧转写，
// 与 python/blendspace.py 同协议。
package main

import (
	"fmt"
	"math"
)

// CMPEpsilon 取自 core/math/math_defs.h。
const CMPEpsilon = 0.00001

// BlendMode 对应 AnimationNodeBlendSpace1D::BlendMode。
type BlendMode int

// 两种主要混合模式。
const (
	Interpolated BlendMode = iota
	Discrete
)

// SyncMode 对应源码的 sync_mode。
type SyncMode int

// 三种同步模式。
const (
	SyncNone SyncMode = iota
	SyncCyclicMutable
	SyncFixed
)

// Result 为一次混合的结果。
type Result struct {
	Weights    []float64
	Closest    int
	DeltaScale float64 // sync 的时间缩放，0 表示不同步
	HasScale   bool
	Triangle   int
}

// BlendSpace1D 为一维混合空间。
type BlendSpace1D struct {
	Positions     []float64
	Lengths       []float64
	Mode          BlendMode
	Sync          SyncMode
	CyclicLength  float64
}

// Process 复刻 _process 的权重计算。
func (b *BlendSpace1D) Process(blendPos float64) Result {
	n := len(b.Positions)
	weights := make([]float64, n)
	if n == 0 {
		return Result{Weights: weights, Closest: -1}
	}
	if n == 1 {
		weights[0] = 1
		return Result{Weights: weights, Closest: 0, DeltaScale: b.deltaScale(weights), HasScale: b.Sync != SyncNone}
	}
	closest := -1
	if b.Mode == Interpolated {
		pLower, posLower := -1, 0.0
		pHigher, posHigher := -1, 0.0
		for i, pos := range b.Positions {
			if pos <= blendPos {
				if pLower == -1 || pos > posLower {
					pLower, posLower = i, pos
				}
			} else if pHigher == -1 || pos < posHigher {
				pHigher, posHigher = i, pos
			}
		}
		switch {
		case pLower == -1 && pHigher != -1:
			weights[pHigher] = 1
		case pHigher == -1:
			weights[pLower] = 1
		default:
			d := posHigher - posLower
			p := (blendPos - posLower) / d
			weights[pLower] = 1 - p
			weights[pHigher] = p
		}
		maxW := 0.0
		for i, w := range weights { // >= 让下标大者在平局时胜出
			if w >= maxW {
				maxW, closest = w, i
			}
		}
	} else {
		best := 1e20
		for i, pos := range b.Positions { // < 让下标小者在平局时胜出
			if d := math.Abs(pos - blendPos); d < best {
				best, closest = d, i
			}
		}
		weights[closest] = 1
	}
	return Result{Weights: weights, Closest: closest,
		DeltaScale: b.deltaScale(weights), HasScale: b.Sync != SyncNone}
}

func (b *BlendSpace1D) deltaScale(weights []float64) float64 {
	if b.Sync == SyncNone {
		return 0
	}
	if b.Sync == SyncFixed {
		if b.CyclicLength > CMPEpsilon {
			return 1 / b.CyclicLength
		}
		return 0
	}
	target, total := 0.0, 0.0
	for i, w := range weights {
		if w > 0 && b.Lengths[i] > CMPEpsilon {
			target += w * b.Lengths[i]
			total += w
		}
	}
	if total > CMPEpsilon {
		target /= total
	}
	if target > CMPEpsilon {
		return 1 / target
	}
	return 0
}

// BlendSpace2D 为二维混合空间。
type BlendSpace2D struct {
	Positions [][2]float64
	Triangles [][3]int
	Mode      BlendMode
}

// BlendTriangle 复刻 _blend_triangle 的重心坐标求解。
func BlendTriangle(pos [2]float64, pts [3][2]float64) [3]float64 {
	var w [3]float64
	for i := 0; i < 3; i++ {
		if math.Abs(pos[0]-pts[i][0]) < 1e-6 && math.Abs(pos[1]-pts[i][1]) < 1e-6 {
			w[i] = 1
			return w
		}
	}
	v0 := [2]float64{pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]}
	v1 := [2]float64{pts[2][0] - pts[0][0], pts[2][1] - pts[0][1]}
	v2 := [2]float64{pos[0] - pts[0][0], pos[1] - pts[0][1]}
	d00 := v0[0]*v0[0] + v0[1]*v0[1]
	d01 := v0[0]*v1[0] + v0[1]*v1[1]
	d11 := v1[0]*v1[0] + v1[1]*v1[1]
	d20 := v2[0]*v0[0] + v2[1]*v0[1]
	d21 := v2[0]*v1[0] + v2[1]*v1[1]
	denom := d00*d11 - d01*d01
	if denom == 0 {
		return [3]float64{1, 0, 0}
	}
	vv := (d11*d20 - d01*d21) / denom
	ww := (d00*d21 - d01*d20) / denom
	return [3]float64{1 - vv - ww, vv, ww}
}

func dist(a, b [2]float64) float64 {
	return math.Hypot(a[0]-b[0], a[1]-b[1])
}

func sign(a, b, c [2]float64) float64 {
	return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
}

func pointInTriangle(p [2]float64, t [3][2]float64) bool {
	d1, d2, d3 := sign(p, t[0], t[1]), sign(p, t[1], t[2]), sign(p, t[2], t[0])
	neg := d1 < 0 || d2 < 0 || d3 < 0
	pos := d1 > 0 || d2 > 0 || d3 > 0
	return !(neg && pos)
}

// Process 计算二维混合权重：内部点走重心坐标，外部点回退到最近边的投影。
func (b *BlendSpace2D) Process(pos [2]float64) Result {
	n := len(b.Positions)
	weights := make([]float64, n)
	if n == 0 || (b.Mode == Interpolated && len(b.Triangles) == 0) {
		return Result{Weights: weights, Closest: -1, Triangle: -1}
	}
	if b.Mode == Discrete {
		best, closest := 1e20, -1
		for i, p := range b.Positions {
			d := (p[0]-pos[0])*(p[0]-pos[0]) + (p[1]-pos[1])*(p[1]-pos[1])
			if d < best {
				best, closest = d, i
			}
		}
		weights[closest] = 1
		return Result{Weights: weights, Closest: closest, Triangle: -1}
	}
	var triW [3]float64
	triIdx := -1
	for i, tri := range b.Triangles {
		var pts [3][2]float64
		for j := 0; j < 3; j++ {
			pts[j] = b.Positions[tri[j]]
		}
		if pointInTriangle(pos, pts) {
			triIdx, triW = i, BlendTriangle(pos, pts)
			break
		}
	}
	if triIdx == -1 {
		best := math.Inf(1)
		for i, tri := range b.Triangles {
			var pts [3][2]float64
			for j := 0; j < 3; j++ {
				pts[j] = b.Positions[tri[j]]
			}
			for j := 0; j < 3; j++ {
				a, bb := pts[j], pts[(j+1)%3]
				c2 := closestOnSegment(pos, a, bb)
				if d := dist(c2, pos); d < best {
					best = d
					triIdx = i
					triW = [3]float64{0, 0, 0}
					if segLen := dist(a, bb); segLen == 0 {
						triW[j] = 1
					} else {
						c := dist(a, c2) / segLen
						triW[j] = 1 - c
						triW[(j+1)%3] = c
					}
				}
			}
		}
	}
	closest, maxW := -1, 0.0
	for j := 0; j < 3; j++ {
		pi := b.Triangles[triIdx][j]
		weights[pi] = triW[j]
		if triW[j] >= maxW {
			maxW, closest = triW[j], pi
		}
	}
	return Result{Weights: weights, Closest: closest, Triangle: triIdx}
}

func closestOnSegment(p, a, b [2]float64) [2]float64 {
	dx, dy := b[0]-a[0], b[1]-a[1]
	denom := dx*dx + dy*dy
	if denom == 0 {
		return a
	}
	t := ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / denom
	if t < 0 {
		t = 0
	} else if t > 1 {
		t = 1
	}
	return [2]float64{a[0] + dx*t, a[1] + dy*t}
}

func main() {
	bs := &BlendSpace1D{Positions: []float64{0, 1, 2}}
	for _, p := range []float64{0, 0.5, 1, 1.5, 3} {
		r := bs.Process(p)
		fmt.Println("[1D]", p, r.Weights, "closest=", r.Closest)
	}
	bsd := &BlendSpace1D{Positions: []float64{0, 1, 2}, Mode: Discrete}
	r := bsd.Process(0.6)
	fmt.Println("[1D 离散] 0.6 ->", r.Weights, "closest=", r.Closest)

	bs2 := &BlendSpace2D{Positions: [][2]float64{{0, 0}, {1, 0}, {0, 1}},
		Triangles: [][3]int{{0, 1, 2}}}
	r2 := bs2.Process([2]float64{0.25, 0.25})
	fmt.Println("[2D] (0.25,0.25) ->", r2.Weights, "closest=", r2.Closest)
	r3 := bs2.Process([2]float64{2, 0})
	fmt.Println("[2D 外部] (2,0) ->", r3.Weights, "closest=", r3.Closest)
}
