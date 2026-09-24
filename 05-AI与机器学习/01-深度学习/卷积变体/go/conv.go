// Package conv —— pytorch torch/nn/modules/conv.py 的 Go 转写（尺寸公式 / groups / 转置卷积）。
package main

import "fmt"

// Conv2dOutput 对应 Conv2d docstring 的 H_out 公式（Go 整数除法本身即向下取整）。
func Conv2dOutput(hIn, padding, dilation, kernel, stride int) int {
	return (hIn+2*padding-dilation*(kernel-1)-1)/stride + 1
}

// EffectiveKernel 空洞卷积的等效核 d(k-1)+1。
func EffectiveKernel(kernel, dilation int) int {
	return dilation*(kernel-1) + 1
}

// ConvTransposeOutput 对应转置卷积的 Shape 公式。
func ConvTransposeOutput(hIn, stride, padding, dilation, kernel, outputPadding int) int {
	return (hIn-1)*stride - 2*padding + dilation*(kernel-1) + outputPadding + 1
}

// MinOutputFor 对应 _output_padding 里的 min_sizes。
func MinOutputFor(hIn, stride, padding, dilation, kernel int) int {
	return (hIn-1)*stride - 2*padding + dilation*(kernel-1) + 1
}

// ResolveOutputPadding 对应 _output_padding 的 output_size 分支；越界返回 error。
func ResolveOutputPadding(hIn, outputSize, stride, padding, dilation, kernel int) (int, error) {
	lo := MinOutputFor(hIn, stride, padding, dilation, kernel)
	hi := lo + stride - 1
	if outputSize < lo || outputSize > hi {
		return 0, fmt.Errorf("requested an output size of %d, but valid sizes range from %d to %d", outputSize, lo, hi)
	}
	return outputSize - lo, nil
}

// SamePadding 对应 padding='same'（源码 Note：stride 非 1 不支持）。
func SamePadding(kernel, stride int) (int, error) {
	if stride != 1 {
		return 0, fmt.Errorf("padding='same' is not supported for strided convolutions")
	}
	return (kernel - 1) / 2, nil
}

// WeightShape 对应 _ConvNd 的 weight 形状 (out, in/groups, kH, kW)。
func WeightShape(inCh, outCh, kh, kw, groups int) (int, int, int, int, error) {
	if inCh%groups != 0 {
		return 0, 0, 0, 0, fmt.Errorf("in_channels must be divisible by groups")
	}
	if outCh%groups != 0 {
		return 0, 0, 0, 0, fmt.Errorf("out_channels must be divisible by groups")
	}
	return outCh, inCh / groups, kh, kw, nil
}

// ParamCount 参数量（含 bias）。
func ParamCount(outCh, inPerG, kh, kw int, bias bool) int {
	n := outCh * inPerG * kh * kw
	if bias {
		return n + outCh
	}
	return n
}

// DepthwiseSeparableParams DW(groups=in) + PW(1x1) 的参数量。
func DepthwiseSeparableParams(inCh, outCh, kernel int, bias bool) (int, int) {
	_, _, _, _, _ = WeightShape(inCh, inCh, kernel, kernel, inCh)
	dw := ParamCount(inCh, 1, kernel, kernel, bias)
	pw := ParamCount(outCh, inCh, 1, 1, bias)
	return dw, pw
}

// PadIndex 返回越界下标在指定 padding_mode 下的取法；-1 表示补 0。
func PadIndex(i, size int, mode string) int {
	if i >= 0 && i < size {
		return i
	}
	switch mode {
	case "zeros":
		return -1
	case "replicate":
		if i < 0 {
			return 0
		}
		return size - 1
	case "circular":
		return ((i % size) + size) % size
	}
	return -2
}

// Sample 按 padding_mode 取 x[c][i][j]。
func Sample(x [][][]float64, c, i, j int, mode string) float64 {
	h, w := len(x[c]), len(x[c][0])
	pi := PadIndex(i, h, mode)
	pj := PadIndex(j, w, mode)
	if pi < 0 || pj < 0 {
		return 0.0
	}
	return x[c][pi][pj]
}

// Conv2d 互相关前向，支持 groups / dilation / stride / padding_mode。x 为 [C][H][W]。
func Conv2d(x [][][]float64, w [][][][]float64, bias []float64, stride, padding, dilation, groups int, mode string) [][][]float64 {
	cin := len(x)
	cout := len(w)
	kh, kw := len(w[0][0]), len(w[0][0][0])
	hOut := Conv2dOutput(len(x[0]), padding, dilation, kh, stride)
	wOut := Conv2dOutput(len(x[0][0]), padding, dilation, kw, stride)
	out := make([][][]float64, cout)
	for o := 0; o < cout; o++ {
		out[o] = make([][]float64, hOut)
		for i := 0; i < hOut; i++ {
			out[o][i] = make([]float64, wOut)
		}
	}
	outPerG := cout / groups
	inPerG := cin / groups
	for o := 0; o < cout; o++ {
		g := o / outPerG
		for i := 0; i < hOut; i++ {
			for j := 0; j < wOut; j++ {
				acc := 0.0
				for kc := 0; kc < inPerG; kc++ {
					c := g*inPerG + kc
					for ki := 0; ki < kh; ki++ {
						for kj := 0; kj < kw; kj++ {
							hi := i*stride - padding + ki*dilation
							wj := j*stride - padding + kj*dilation
							acc += w[o][kc][ki][kj] * Sample(x, c, hi, wj, mode)
						}
					}
				}
				if bias != nil {
					acc += bias[o]
				}
				out[o][i][j] = acc
			}
		}
	}
	return out
}

// SupportChannels 支持集探针：逐个输入通道单独置 1，返回被点亮的输出通道下标。
func SupportChannels(x [][][]float64, w [][][][]float64, stride, padding, dilation, groups int, mode string) [][]int {
	cin := len(x)
	res := make([][]int, cin)
	for c := 0; c < cin; c++ {
		probe := make([][][]float64, cin)
		for i := range probe {
			probe[i] = make([][]float64, len(x[0]))
			for r := range probe[i] {
				probe[i][r] = make([]float64, len(x[0][0]))
			}
		}
		for r := 0; r < len(x[0]); r++ {
			for cc := 0; cc < len(x[0][0]); cc++ {
				probe[c][r][cc] = 1.0
			}
		}
		o := Conv2d(probe, w, nil, stride, padding, dilation, groups, mode)
		for oi, och := range o {
			if channelNonZero(och) {
				res[c] = append(res[c], oi)
			}
		}
	}
	return res
}

// channelNonZero 判断某个输出通道是否被点亮。
func channelNonZero(ch [][]float64) bool {
	for _, row := range ch {
		for _, v := range row {
			if v > 1e-12 || v < -1e-12 {
				return true
			}
		}
	}
	return false
}
