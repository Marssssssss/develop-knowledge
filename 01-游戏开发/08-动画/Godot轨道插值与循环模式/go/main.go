// Godot 4 Animation 轨道求值的 Go 侧转写，与 python/track.py 同协议：
// _find 二分查找 + 三种 LoopMode 的下标选择 + NEAREST / LINEAR / LINEAR_ANGLE。
package main

import (
	"fmt"
	"math"
)

// CMPEpsilon 取自 core/math/math_defs.h。
const CMPEpsilon = 0.00001

// Key 为一个关键帧。
type Key struct {
	Time       float64
	Value      float64
	Transition float64
}

// LoopMode 对应 Animation::LoopMode。
type LoopMode int

// 三种循环模式。
const (
	LoopNone LoopMode = iota
	LoopLinear
	LoopPingPong
)

// Interpolation 对应 Animation::InterpolationType 的前三种。
type Interpolation int

// 三种插值方式。
const (
	Nearest Interpolation = iota
	Linear
	LinearAngle
)

// Track 为一条轨道。
type Track struct {
	Keys          []Key
	Length        float64
	Loop          LoopMode
	Interp        Interpolation
	LoopWrap      bool
	UpdateDiscete bool
}

func isEqualApprox(a, b float64) bool { return math.Abs(a-b) < CMPEpsilon }
func isZeroApprox(v float64) bool     { return math.Abs(v) < CMPEpsilon }

// posmod 对应 Math::posmod。
func posmod(x, m float64) float64 { return x - math.Floor(x/m)*m }

// pingpong 对应 Math::pingpong。
func pingpong(value, length float64) float64 {
	if length == 0 {
		return 0
	}
	f := value - length
	f = f - math.Floor(f)
	return math.Abs(f/(length*2)*length*2 - length)
}

// pingpongIndex 复刻源码的 round(pingpong(i+0.5, len)-0.5)。
func pingpongIndex(i, length int) int {
	return int(math.Round(pingpong(float64(i)+0.5, float64(length)) - 0.5))
}

// find 直译 Animation::_find，空轨道返回 -2。
func (t *Track) find(time float64, backward, limit bool) int {
	n := len(t.Keys)
	if n == 0 {
		return -2
	}
	low, high, middle := 0, n-1, 0
	for low <= high {
		middle = (low + high) / 2
		if isEqualApprox(time, t.Keys[middle].Time) {
			return middle
		} else if time < t.Keys[middle].Time {
			high = middle - 1
		} else {
			low = middle + 1
		}
	}
	if !backward {
		if t.Keys[middle].Time > time {
			middle--
		}
	} else if t.Keys[middle].Time < time {
		middle++
	}
	if limit && middle > -1 && middle < n {
		diff := t.Length - t.Keys[middle].Time
		if (t.Keys[middle].Time < 0 && !isZeroApprox(t.Keys[middle].Time)) ||
			(diff < 0 && !isZeroApprox(diff)) {
			return -1
		}
	}
	return middle
}

// Interpolate 直译 Animation::_interpolate（标量版）。
func (t *Track) Interpolate(time float64, backward bool) (float64, bool) {
	keys := t.Keys
	n := t.find(t.Length, false, false) + 1 // 超过 length 的关键帧被丢弃
	if n <= 0 {
		return 0, false
	}
	if n == 1 {
		return keys[0].Value, true
	}
	idx := t.find(time, backward, false)
	maxi := n - 1
	isStartEdge := idx == -1
	isEndEdge := idx >= maxi
	if backward {
		isStartEdge = idx >= n
		isEndEdge = idx == 0
	}

	var delta, from float64
	next := 0
	interp := t.Interp
	if t.UpdateDiscete {
		interp = Nearest
	}

	switch {
	case !t.LoopWrap || t.Loop == LoopNone:
		if isStartEdge {
			if backward {
				idx = maxi
			} else {
				idx = 0
			}
		}
		next = idx + 1
		if next < 0 {
			next = 0
		}
		if next > maxi {
			next = maxi
		}
	case t.Loop == LoopLinear:
		if isStartEdge {
			if backward {
				idx = 0
			} else {
				idx = maxi
			}
		}
		next = int(posmod(float64(idx+1), float64(n)))
		if isStartEdge {
			endtime := t.Length - keys[idx].Time
			if endtime < 0 {
				endtime = 0
			}
			delta = endtime + keys[next].Time
			from = endtime + time
		} else if isEndEdge {
			delta = (t.Length - keys[idx].Time) + keys[next].Time
			from = time - keys[idx].Time
		}
	default: // LOOP_PINGPONG
		if isStartEdge {
			idx = -1
		}
		next = pingpongIndex(idx+1, n)
		idx = pingpongIndex(idx, n)
		if isStartEdge {
			endtime := keys[idx].Time
			if endtime < 0 {
				endtime = 0
			}
			delta = endtime + keys[next].Time
			from = endtime + time
		} else if isEndEdge {
			delta = t.Length*2 - keys[idx].Time - keys[next].Time
			from = time - keys[idx].Time
		}
	}

	if !isStartEdge && !isEndEdge {
		delta = keys[next].Time - keys[idx].Time
		from = time - keys[idx].Time
	}

	c := 0.0
	if !isZeroApprox(delta) {
		c = from / delta
	}

	if keys[idx].Transition == 0 { // transition == 0 表示不做插值
		return keys[idx].Value, true
	}
	switch interp {
	case Nearest:
		return keys[idx].Value, true
	case Linear:
		return (1-c)*keys[idx].Value + c*keys[next].Value, true
	case LinearAngle:
		d := keys[next].Value - keys[idx].Value
		d = d - math.Pi*2*math.Floor((d+math.Pi)/(math.Pi*2))
		return posmod(keys[idx].Value+d*c, math.Pi*2), true
	}
	return keys[idx].Value, true
}

func main() {
	base := []Key{{Time: 0, Value: 0, Transition: 1}, {Time: 0.5, Value: 10, Transition: 1}}
	for _, lm := range []LoopMode{LoopNone, LoopLinear, LoopPingPong} {
		tr := &Track{Keys: base, Length: 1, Loop: lm, Interp: Linear, LoopWrap: true}
		vals := []float64{}
		for i := 0; i <= 8; i++ {
			v, _ := tr.Interpolate(float64(i)/8, false)
			vals = append(vals, math.Round(v*10000)/10000)
		}
		fmt.Println("[loop", lm, "] t=0..1:", vals)
	}
	ang := &Track{Keys: []Key{{Time: 0, Value: 0.1, Transition: 1},
		{Time: 1, Value: math.Pi*2 - 0.1, Transition: 1}},
		Length: 1, Loop: LoopNone, Interp: LinearAngle, LoopWrap: true}
	v, _ := ang.Interpolate(0.5, false)
	fmt.Println("[LINEAR_ANGLE] 跨 2π 中点:", v)
}
