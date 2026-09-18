package main

import "errors"

// RawDelta 是范围向量首尾差，并按需补偿计数器重置。
func RawDelta(pts []Sample, isCounter bool) float64 {
	result := pts[len(pts)-1].V - pts[0].V
	if isCounter {
		prev := pts[0].V
		for _, p := range pts[1:] {
			if p.V < prev {
				result += prev // 重置：把重置前的高度加回去
			}
			prev = p.V
		}
	}
	return result
}

// ExtrapolatedRate 实现 rate / increase / delta 共用的外推计算。
//
// order 选择「超阈值项替换为 avg/2」与「计数器零点截断」两步的先后：
// 官方文档只说明外推的存在与目的，未规定分支顺序；两种顺序在
// durationToZero 落在 (avg/2, threshold) 区间时给出不同结果。
func ExtrapolatedRate(pts []Sample, rangeStart, rangeEnd float64,
	isCounter, isRate bool, order string) (float64, bool) {
	if len(pts) < 2 {
		return 0, false
	}
	sampledInterval := pts[len(pts)-1].T - pts[0].T
	if sampledInterval <= 0 {
		return 0, false
	}
	result := RawDelta(pts, isCounter)
	dStart := pts[0].T - rangeStart
	dEnd := rangeEnd - pts[len(pts)-1].T
	avg := sampledInterval / float64(len(pts)-1)
	threshold := avg * 1.1
	half := avg / 2

	zeroPoint := func(ds float64) float64 {
		// 计数器不可能为负：若序列有上升趋势，可把「零点」当作序列起点，
		// 从而避免把结果外推到负的计数器值。
		if isCounter && result > 0 && pts[0].V >= 0 {
			dtz := sampledInterval * (pts[0].V / result)
			if dtz < ds {
				return dtz
			}
		}
		return ds
	}

	if order == "threshold_first" {
		if dStart >= threshold {
			dStart = half
		}
		dStart = zeroPoint(dStart)
		if dEnd >= threshold {
			dEnd = half
		}
	} else {
		dStart = zeroPoint(dStart)
		if dStart >= threshold {
			dStart = half
		}
		if dEnd >= threshold {
			dEnd = half
		}
	}

	extrapolateTo := sampledInterval + dStart + dEnd
	factor := extrapolateTo / sampledInterval
	if isRate {
		factor /= rangeEnd - rangeStart
	}
	return result * factor, true
}

// InstantValue 实现 irate / idelta：只看最后两个点，不做外推。
func InstantValue(pts []Sample, isCounter, isRate bool) (float64, bool) {
	if len(pts) < 2 {
		return 0, false
	}
	last, prev := pts[len(pts)-1], pts[len(pts)-2]
	var result float64
	if isRate && isCounter && last.V < prev.V {
		result = last.V // 重置：假设从 0 重新计数
	} else {
		result = last.V - prev.V
	}
	span := last.T - prev.T
	if span <= 0 {
		return 0, false
	}
	if isRate {
		return result / span, true
	}
	return result, true
}

// ApplyFunc 把范围向量喂给指定函数。
func ApplyFunc(name string, pts []Sample, rangeStart, rangeEnd float64) (float64, bool) {
	switch name {
	case "rate":
		return ExtrapolatedRate(pts, rangeStart, rangeEnd, true, true, "threshold_first")
	case "increase":
		return ExtrapolatedRate(pts, rangeStart, rangeEnd, true, false, "threshold_first")
	case "delta":
		return ExtrapolatedRate(pts, rangeStart, rangeEnd, false, false, "threshold_first")
	case "irate":
		return InstantValue(pts, true, true)
	case "idelta":
		return InstantValue(pts, false, false)
	case "resets":
		n := 0
		for i := 1; i < len(pts); i++ {
			if pts[i].V < pts[i-1].V {
				n++
			}
		}
		return float64(n), true
	case "changes":
		n := 0
		for i := 1; i < len(pts); i++ {
			if pts[i].V != pts[i-1].V {
				n++
			}
		}
		return float64(n), true
	}
	return 0, false
}

// Result 是求值结果，要么是即时向量要么是矩阵。
type Result struct {
	Points   []Point
	Matrix   []MatrixEntry
	IsMatrix bool
}

// Engine 是求值引擎；EvalInterval 即全局 evaluation interval（默认 1m）。
type Engine struct {
	Store        *MemStore
	EvalInterval float64
}

// NewEngine 构造求值引擎。
func NewEngine(store *MemStore) *Engine {
	return &Engine{Store: store, EvalInterval: 60}
}

// Instant 求即时向量（或矩阵，若表达式是范围向量/子查询）。
func (e *Engine) Instant(text string, evalT float64) (*Result, error) {
	ex, err := Parse(text)
	if err != nil {
		return nil, err
	}
	return e.eval(ex, evalT)
}

func (e *Engine) eval(ex *Expr, evalT float64) (*Result, error) {
	at := evalT
	if ex.HasAt {
		at = ex.At
	}
	if ex.AtFunc == "start()" || ex.AtFunc == "end()" {
		at = evalT // 即时查询里 start()/end() 都解析为求值时刻
	}

	if ex.HasSub {
		res := e.EvalInterval
		if ex.HasRes {
			res = ex.SubRes
		}
		if res <= 0 {
			return nil, errors.New("子查询 resolution 必须为正")
		}
		inner := *ex
		inner.HasSub, inner.HasRes, inner.SubRange, inner.SubRes = false, false, 0, 0
		grouped := map[string]MatrixEntry{}
		order := []string{}
		for t := at - ex.SubRange; ; t = minF(t+res, at) {
			r, err := e.eval(&inner, t)
			if err != nil {
				return nil, err
			}
			for _, p := range r.Points {
				k := seriesKey("", p.Labels)
				me, ok := grouped[k]
				if !ok {
					me = MatrixEntry{Labels: p.Labels}
					grouped[k] = me
					order = append(order, k)
				}
				me.Pts = append(me.Pts, Sample{T: t, V: p.V})
				grouped[k] = me
			}
			if t >= at {
				break
			}
		}
		out := &Result{IsMatrix: true}
		for _, k := range order {
			out.Matrix = append(out.Matrix, grouped[k])
		}
		return out, nil
	}

	selectAt := at - ex.OffsetS
	if !ex.HasRange {
		return &Result{Points: e.Store.Select(ex.Name, ex.Matchers, selectAt)}, nil
	}
	rangeEnd := selectAt
	rangeStart := rangeEnd - ex.RangeS
	matrix := e.Store.RangeSelect(ex.Name, ex.Matchers, rangeStart, rangeEnd)
	if ex.Func == "" {
		return &Result{Matrix: matrix, IsMatrix: true}, nil
	}
	out := &Result{}
	for _, me := range matrix {
		v, ok := ApplyFunc(ex.Func, me.Pts, rangeStart, rangeEnd)
		if ok {
			out.Points = append(out.Points, Point{Labels: me.Labels, V: v})
		}
	}
	return out, nil
}

func minF(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
