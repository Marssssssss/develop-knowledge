// Package ambisonic 实现 Ambisonic 声场的核心换算：ACN 通道序、SN3D 归一化、
// 一阶/高阶编解码、channel_map 与 SA3D 盒。口径来自 Google《Spatial Audio RFC》
// （spatial-media 仓库 docs/spatial-audio-rfc.md）。
package ambisonic

import (
	"encoding/binary"
	"errors"
	"math"
)

// Component 是 ACN 序上的一个球谐分量。
type Component struct {
	N int // ACN 下标
	L int // 次数 degree
	M int // 阶数 order
}

// ACNIndex 返回 n = l*(l+1) + m。
func ACNIndex(l, m int) int { return l*(l+1) + m }

// ACNComponents 按 ACN 升序列出所有分量（最高 maxOrder 阶）。
func ACNComponents(maxOrder int) []Component {
	out := []Component{}
	for l := 0; l <= maxOrder; l++ {
		for m := -l; m <= l; m++ {
			out = append(out, Component{ACNIndex(l, m), l, m})
		}
	}
	for i := 1; i < len(out); i++ { // 插入排序，保证稳定升序
		for j := i; j > 0 && out[j-1].N > out[j].N; j-- {
			out[j-1], out[j] = out[j], out[j-1]
		}
	}
	return out
}

// ChannelsForOrder 返回 (order+1)^2。
func ChannelsForOrder(order int) int { return (order + 1) * (order + 1) }

// OrderForChannels 返回 sqrt(n)-1，非完全平方数返回 (0, false)。
func OrderForChannels(channels int) (int, bool) {
	root := int(math.Round(math.Sqrt(float64(channels))))
	if root*root != channels {
		return 0, false
	}
	return root - 1, true
}

func factorial(n int) float64 {
	acc := 1.0
	for i := 2; i <= n; i++ {
		acc *= float64(i)
	}
	return acc
}

// SN3D 返回 sqrt((2-delta(m)) * (l-m)!/(l+m)!)，m 取绝对值。
func SN3D(l, m int) float64 {
	if m < 0 {
		m = -m
	}
	delta := 0.0
	if m == 0 {
		delta = 1.0
	}
	return math.Sqrt((2.0 - delta) * factorial(l-m) / factorial(l+m))
}

// N3DFactor 返回 N3D/SN3D = sqrt((2l+1)/4pi)。
func N3DFactor(l int) float64 {
	return math.Sqrt(float64(2*l+1) / (4.0 * math.Pi))
}

// legendre 计算无 Condon-Shortley 相位的 P(l,m,x)。
func legendre(l, m int, x float64) float64 {
	if m < 0 || m > l {
		return 0.0
	}
	pmm := 1.0
	if m > 0 {
		somx2 := math.Sqrt(math.Max(0.0, 1.0-x*x))
		fact := 1.0
		for i := 0; i < m; i++ {
			pmm *= fact * somx2
			fact += 2.0
		}
	}
	if l == m {
		return pmm
	}
	pmmp1 := float64(2*m+1) * x * pmm
	if l == m+1 {
		return pmmp1
	}
	for ll := m + 2; ll <= l; ll++ {
		pl := (float64(2*ll-1)*x*pmmp1 - float64(ll+m-1)*pmm) / float64(ll-m)
		pmm, pmmp1 = pmmp1, pl
	}
	return pmmp1
}

// Harmonic 返回 N(l,|m|) * P(l,|m|, sin E) * T(m, A)，normalization ∈ {SN3D, N3D}。
func Harmonic(l, m int, elevation, azimuth float64, normalization string) float64 {
	val := legendre(l, iabs(m), math.Sin(elevation))
	if m < 0 {
		val *= math.Sin(float64(-m) * azimuth)
	} else {
		val *= math.Cos(float64(m) * azimuth)
	}
	switch normalization {
	case "SN3D":
		return SN3D(l, m) * val
	case "N3D":
		return SN3D(l, m) * N3DFactor(l) * val
	}
	return 0.0
}

func iabs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

// Encode 按 ACN 序对平面波编码。
func Encode(maxOrder int, elevation, azimuth, gain float64, normalization string) []float64 {
	comps := ACNComponents(maxOrder)
	out := make([]float64, 0, len(comps))
	for _, c := range comps {
		out = append(out, gain*Harmonic(c.L, c.M, elevation, azimuth, normalization))
	}
	return out
}

// DecodeSampling 采样解码（口径：1/K）。
func DecodeSampling(b []float64, speakers [][2]float64) ([]float64, error) {
	order, ok := OrderForChannels(len(b))
	if !ok {
		return nil, errors.New("channels is not (order+1)^2")
	}
	comps := ACNComponents(order)
	out := make([]float64, 0, len(speakers))
	for _, sp := range speakers {
		acc := 0.0
		for i, c := range comps {
			acc += b[i] * Harmonic(c.L, c.M, sp[0], sp[1], "SN3D")
		}
		out = append(out, acc/float64(len(speakers)))
	}
	return out, nil
}

// BuildChannelMap 返回 channel_map：ACN 分量 i 所在的轨道通道下标。
func BuildChannelMap(stored []string) []int {
	position := map[string]int{}
	for i, name := range stored {
		position[name] = i
	}
	head := []int{}
	for _, name := range []string{"W", "Y", "Z", "X"} {
		if idx, ok := position[name]; ok {
			head = append(head, idx)
		}
	}
	extra := []int{}
	for name, idx := range position {
		switch name {
		case "W", "Y", "Z", "X":
		default:
			extra = append(extra, idx)
		}
	}
	for i := 1; i < len(extra); i++ {
		for j := i; j > 0 && extra[j-1] > extra[j]; j-- {
			extra[j-1], extra[j] = extra[j], extra[j-1]
		}
	}
	return append(head, extra...)
}

// SA3D 是 MP4 里的 Spatial Audio Box（大端）。
type SA3D struct {
	Version        uint8
	AmbisonicType  uint8
	AmbisonicOrder uint32
	Ordering       uint8
	Normalization  uint8
	ChannelMap     []uint32
}

// Render 序列化 SA3D。
func (s SA3D) Render() []byte {
	out := []byte{s.Version, s.AmbisonicType}
	buf := make([]byte, 4)
	binary.BigEndian.PutUint32(buf, s.AmbisonicOrder)
	out = append(out, buf...)
	out = append(out, s.Ordering, s.Normalization)
	binary.BigEndian.PutUint32(buf, uint32(len(s.ChannelMap)))
	out = append(out, buf...)
	for _, c := range s.ChannelMap {
		binary.BigEndian.PutUint32(buf, c)
		out = append(out, buf...)
	}
	return out
}

// ParseSA3D 解析 SA3D；长度不符返回错误。
func ParseSA3D(data []byte) (SA3D, error) {
	if len(data) < 12 {
		return SA3D{}, errors.New("SA3D too short")
	}
	num := binary.BigEndian.Uint32(data[8:12])
	if len(data) != 12+4*int(num) {
		return SA3D{}, errors.New("SA3D length mismatch")
	}
	box := SA3D{
		Version:        data[0],
		AmbisonicType:  data[1],
		AmbisonicOrder: binary.BigEndian.Uint32(data[2:6]),
		Ordering:       data[6],
		Normalization:  data[7],
	}
	for i := 0; i < int(num); i++ {
		box.ChannelMap = append(box.ChannelMap, binary.BigEndian.Uint32(data[12+4*i:]))
	}
	return box, nil
}
