// Package gru —— pytorch torch/nn/modules/rnn.py 里 GRU / GRUCell 的 Go 转写。
// 与 python/gru.py 同题；Go 无 ** 运算、切片即视图，故所有拷贝显式 clone。
package main

import "math"

// Sigmoid 数值稳定实现（x<0 走 exp(x)/(1+exp(x)) 分支）。
func Sigmoid(x float64) float64 {
	if x >= 0 {
		return 1.0 / (1.0 + math.Exp(-x))
	}
	e := math.Exp(x)
	return e / (1.0 + e)
}

// GateSize 对应 RNNBase.__init__ 里的 gate_size：LSTM 4H、GRU 3H、RNN H。
func GateSize(kind string, hidden int) int {
	switch kind {
	case "lstm":
		return 4 * hidden
	case "gru":
		return 3 * hidden
	default:
		return hidden
	}
}

// Stdv 对应 reset_parameters 的 stdv = 1/sqrt(hidden_size)。
func Stdv(hidden int) float64 {
	if hidden <= 0 {
		return 0.0
	}
	return 1.0 / math.Sqrt(float64(hidden))
}

// MatVec：W 为 [out][in]，x 为 [in]。
func MatVec(W [][]float64, x []float64) []float64 {
	out := make([]float64, len(W))
	for i, row := range W {
		s := 0.0
		for j, v := range row {
			s += v * x[j]
		}
		out[i] = s
	}
	return out
}

// AddBias 把 bias 加到向量上；b 为 nil 时原样返回。
func AddBias(v, b []float64) []float64 {
	if b == nil {
		return v
	}
	for i := range v {
		v[i] += b[i]
	}
	return v
}

// Hadamard 逐元素乘。
func Hadamard(a, b []float64) []float64 {
	out := make([]float64, len(a))
	for i := range a {
		out[i] = a[i] * b[i]
	}
	return out
}

// Params 与 PyTorch 的形状约定一致：wIH 为 (3H, in)，wHH 为 (3H, H)。
type Params struct {
	WIH [][]float64
	WHH [][]float64
	BIH []float64
	BHH []float64
	H   int
}

// GruCell 对应 GRUCell.forward 的四式；paperVariant 走 Cho 2014 原式。
func GruCell(x, h []float64, p Params, paperVariant bool) ([]float64, []float64, []float64, []float64) {
	H := p.H
	rIR := p.WIH[0:H]
	rHR := p.WHH[0:H]
	zIZ := p.WIH[H : 2*H]
	zHZ := p.WHH[H : 2*H]
	nIN := p.WIH[2*H : 3*H]
	nHN := p.WHH[2*H : 3*H]

	r := AddBias(MatVec(rIR, x), p.BIH[0:H])
	rh := MatVec(rHR, h)
	for i := range r {
		r[i] = Sigmoid(r[i] + rh[i] + p.BHH[i])
	}
	z := AddBias(MatVec(zIZ, x), p.BIH[H:2*H])
	zh := MatVec(zHZ, h)
	for i := range z {
		z[i] = Sigmoid(z[i] + zh[i] + p.BHH[H+i])
	}

	var inside []float64
	if paperVariant {
		inside = MatVec(nHN, Hadamard(r, h))
		for i := range inside {
			inside[i] += p.BHH[2*H+i]
		}
	} else {
		wh := AddBias(MatVec(nHN, h), p.BHH[2*H:3*H])
		inside = Hadamard(r, wh)
	}
	n := AddBias(MatVec(nIN, x), p.BIH[2*H:3*H])
	for i := range n {
		n[i] = math.Tanh(n[i] + inside[i])
	}
	hp := make([]float64, H)
	for i := 0; i < H; i++ {
		hp[i] = (1-z[i])*n[i] + z[i]*h[i]
	}
	return hp, r, z, n
}

// GruLayer 单层单向；x 为 [T][N][X]，dropout 只作用在非最后层（由调用方决定）。
func GruLayer(x [][][]float64, h0 [][]float64, p Params, mask [][][]float64, paperVariant bool) ([][][]float64, [][]float64) {
	T := len(x)
	N := len(x[0])
	h := make([][]float64, N)
	for i := 0; i < N; i++ {
		if h0 == nil {
			h[i] = make([]float64, p.H)
		} else {
			h[i] = append([]float64(nil), h0[i]...)
		}
	}
	outs := make([][][]float64, T)
	for t := 0; t < T; t++ {
		row := make([][]float64, N)
		for b := 0; b < N; b++ {
			hp, _, _, _ := GruCell(x[t][b], h[b], p, paperVariant)
			h[b] = hp
			row[b] = append([]float64(nil), hp...)
		}
		if mask != nil {
			m := mask[t]
			for b := 0; b < N; b++ {
				for i := range h[b] {
					h[b][i] *= m[b][i]
				}
			}
		}
		outs[t] = row
	}
	return outs, h
}

// Gru 多层 / 双向；返回 (output [T][N][D*H], hN [D*numLayers][N][H])。
func Gru(x [][][]float64, ps [][]Params, bidirectional bool, mask [][][]float64) ([][][]float64, [][][]float64) {
	D := 1
	if bidirectional {
		D = 2
	}
	T, N := len(x), len(x[0])
	var hN [][][]float64
	layerIn := x
	for l := range ps {
		dirs := make([][][][]float64, D)
		for d := 0; d < D; d++ {
			seq := layerIn
			if d == 1 {
				seq = make([][][]float64, T)
				for t := 0; t < T; t++ {
					seq[t] = layerIn[T-1-t]
				}
			}
			drop := mask
			if l == len(ps)-1 {
				drop = nil
			}
			outs, hT := GruLayer(seq, nil, ps[l][d], drop, false)
			if d == 1 {
				rev := make([][][]float64, T)
				for t := 0; t < T; t++ {
					rev[t] = outs[T-1-t]
				}
				outs = rev
			}
			dirs[d] = outs
			hN = append(hN, hT)
		}
		next := make([][][]float64, T)
		for t := 0; t < T; t++ {
			next[t] = make([][]float64, N)
			for b := 0; b < N; b++ {
				row := append([]float64(nil), dirs[0][t][b]...)
				if D == 2 {
					row = append(row, dirs[1][t][b]...)
				}
				next[t][b] = row
			}
		}
		layerIn = next
	}
	return layerIn, hN
}
