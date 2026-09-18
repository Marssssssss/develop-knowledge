// 九宫格缩放（9-slice / border-image）原理 demo —— Go 版。
//
// 依据 W3C CSS Backgrounds and Borders Module Level 3 §5「Border Images」：
//   §5.2 border-image-slice  —— 四条内缩偏移把图像切成九块
//   §5.3 border-image-width  —— 目标侧「border image 区」的四条内缩偏移
//   §5.5 border-image-repeat —— stretch / repeat / round / space 四种平铺
//   §5.6 Drawing the Border Image —— 先按 border-image-width 缩放，再按 repeat 平铺
//
// 运行：go run nine_slice.go
package main

import (
	"fmt"
	"math"
)

// Region 是源图或目标区上的一个矩形分区（w/h 为 0 表示该块为空）。
type Region struct {
	Name   string
	X, Y   float64
	W, H   float64
}

// Tiling 是一条边/中块在某个轴向上的平铺结果。
type Tiling struct {
	Kind   string
	N      int
	Tile   float64
	Offset float64
	Gap    float64
}

// Positions 返回每块的 (起点, 终点)。
func (t Tiling) Positions() [][2]float64 {
	out := [][2]float64{}
	if t.N <= 0 || t.Tile <= 0 {
		return out
	}
	cur := t.Offset
	for i := 0; i < t.N; i++ {
		out = append(out, [2]float64{cur, cur + t.Tile})
		cur += t.Tile + t.Gap
	}
	return out
}

var ninths = [9]string{"tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br"}

// SliceImage 按四条内缩偏移切九宫格。
// 规范要点：百分比相对图像自身尺寸；偏移超过图像尺寸按 100% 解释；
// 左右偏移之和 >= 图宽 → 上/下边与中间为空；上下偏移之和 >= 图高 → 左/右边与中间为空。
func SliceImage(iw, ih, top, right, bottom, left float64, pct bool) []Region {
	if pct {
		top, bottom = top/100*ih, bottom/100*ih
		right, left = right/100*iw, left/100*iw
	}
	top, bottom = math.Min(top, ih), math.Min(bottom, ih)
	right, left = math.Min(right, iw), math.Min(left, iw)

	hEmpty := top+bottom >= ih
	vEmpty := left+right >= iw

	mw, mh := 0.0, 0.0
	if !vEmpty {
		mw = math.Max(0, iw-left-right)
	}
	if !hEmpty {
		mh = math.Max(0, ih-top-bottom)
	}

	xs := [3]float64{0, left, left + mw}
	ws := [3]float64{left, mw, right}
	ys := [3]float64{0, top, top + mh}
	hs := [3]float64{top, mh, bottom}

	out := make([]Region, 0, 9)
	for r := 0; r < 3; r++ {
		for c := 0; c < 3; c++ {
			w, h := ws[c], hs[r]
			if vEmpty && c == 1 {
				w = 0
			}
			if hEmpty && r == 1 {
				h = 0
			}
			out = append(out, Region{ninths[r*3+c], xs[c], ys[r], w, h})
		}
	}
	return out
}

// Find 按名字取分区。
func Find(regions []Region, name string) Region {
	for _, r := range regions {
		if r.Name == name {
			return r
		}
	}
	panic("no region " + name)
}

// ResolveTiling 把长度 src 的一块按 kind 铺进长度 dst 的区域。
func ResolveTiling(kind string, src, dst float64) Tiling {
	if src <= 0 || dst <= 0 {
		return Tiling{kind, 0, 0, 0, 0}
	}
	switch kind {
	case "stretch":
		return Tiling{kind, 1, dst, 0, 0}
	case "repeat":
		n := int(math.Floor(dst / src))
		return Tiling{kind, n, src, (dst - float64(n)*src) / 2, 0}
	case "round":
		n := int(math.Round(dst / src))
		if n < 1 {
			n = 1
		}
		return Tiling{kind, n, dst / float64(n), 0, 0}
	case "space":
		n := int(math.Floor(dst / src))
		gap := dst
		if n > 0 {
			gap = (dst - float64(n)*src) / float64(n+1)
		}
		return Tiling{kind, n, src, gap, gap}
	}
	panic("unknown tiling kind " + kind)
}

// LayoutEdge 一条水平边（上/中/下）的两步布局：
// 先按区域高度等比缩放，再按 kind 在中间区域长度上平铺。
func LayoutEdge(srcW, srcH, regionH, middleLen float64, kind string) Tiling {
	if srcH <= 0 || regionH <= 0 {
		return Tiling{kind, 0, 0, 0, 0}
	}
	scale1 := regionH / srcH
	return ResolveTiling(kind, srcW*scale1, middleLen)
}

// NineSlice 把 iw×ih 的源图按 9-slice 铺到 tw×th，返回九块的目标矩形与平铺结果。
func NineSlice(iw, ih float64, slices [4]float64, tw, th float64,
	widths *[4]float64, repeat [2]string, fill bool) ([]Region, map[string]Tiling, map[string][2]float64) {
	top, right, bottom, left := slices[0], slices[1], slices[2], slices[3]
	src := SliceImage(iw, ih, top, right, bottom, left)

	wt, wr, wb, wl := top, right, bottom, left
	if widths != nil {
		wt, wr, wb, wl = widths[0], widths[1], widths[2], widths[3]
	}

	mw := math.Max(0, tw-wl-wr)
	mh := math.Max(0, th-wt-wb)
	rx := [3]float64{0, wl, wl + mw}
	ry := [3]float64{0, wt, wt + mh}
	rw := [3]float64{wl, mw, wr}
	rh := [3]float64{wt, mh, wb}

	dst := make([]Region, 0, 9)
	for r := 0; r < 3; r++ {
		for c := 0; c < 3; c++ {
			dst = append(dst, Region{ninths[r*3+c], rx[c], ry[r], rw[c], rh[r]})
		}
	}

	tiles := map[string]Tiling{}
	scales := map[string][2]float64{}
	// 四角：只缩放、永不平铺
	for _, name := range []string{"tl", "tr", "bl", "br"} {
		s, d := Find(src, name), Find(dst, name)
		if s.W > 0 && s.H > 0 && d.W > 0 && d.H > 0 {
			scales[name] = [2]float64{d.W / s.W, d.H / s.H}
		}
	}
	// 上/中/下：水平方向按 repeat[0]
	for _, name := range []string{"tc", "mc", "bc"} {
		s, d := Find(src, name), Find(dst, name)
		if s.W > 0 && s.H > 0 && d.H > 0 {
			tiles[name] = LayoutEdge(s.W, s.H, d.H, d.W, repeat[0])
		}
	}
	// 左/右：垂直方向按 repeat[1]
	for _, name := range []string{"ml", "mr"} {
		s, d := Find(src, name), Find(dst, name)
		if s.W > 0 && s.H > 0 && d.W > 0 {
			scale1 := d.W / s.W
			tiles[name] = ResolveTiling(repeat[1], s.H*scale1, d.H)
		}
	}
	if !fill {
		delete(tiles, "mc")
	}
	return dst, tiles, scales
}

func main() {
	fmt.Println("[1] §5.2 切片：81x81 按 27 均分")
	regs := SliceImage(81, 81, 27, 27, 27, 27, false)
	fmt.Printf("  中间块 = (%.0f, %.0f) %.0fx%.0f\n",
		Find(regs, "mc").X, Find(regs, "mc").Y, Find(regs, "mc").W, Find(regs, "mc").H)

	fmt.Println("[2] §5.2 百分比：100x80 按 25% 30% 12% 20%")
	p := SliceImage(100, 80, 25, 30, 12, 20, true)
	fmt.Printf("  tl.h=%.1f tr.w=%.1f bc.h=%.2f ml.w=%.1f\n",
		Find(p, "tl").H, Find(p, "tr").W, Find(p, "bc").H, Find(p, "ml").W)

	fmt.Println("[3] §5.2 左+右 120 >= 81 → 上/下边与中间为空")
	big := SliceImage(81, 81, 27, 60, 27, 60, false)
	fmt.Printf("  tc.w=%.0f mc.w=%.0f ml.w=%.0f\n",
		Find(big, "tc").W, Find(big, "mc").W, Find(big, "ml").W)

	fmt.Println("[4] §5.5 四种平铺：src=27 → 区域长 200")
	for _, k := range []string{"stretch", "repeat", "round", "space"} {
		t := ResolveTiling(k, 27, 200)
		fmt.Printf("  %-8s n=%d tile=%.4f offset=%.4f gap=%.4f\n",
			k, t.N, t.Tile, t.Offset, t.Gap)
	}

	fmt.Println("[5] §5.6 绘制：81x81(slice 27) → 400x120")
	_, tiles, scales := NineSlice(81, 81, [4]float64{27, 27, 27, 27}, 400, 120,
		nil, [2]string{"stretch", "stretch"}, false)
	fmt.Printf("  角块缩放比 = %.2f（不参与拉伸）\n", scales["tl"][0])
	fmt.Printf("  上边 stretch → n=%d tile=%.0f\n", tiles["tc"].N, tiles["tc"].Tile)
	_, mcTiles, _ := NineSlice(81, 81, [4]float64{27, 27, 27, 27}, 400, 120,
		nil, [2]string{"round", "stretch"}, true)
	fmt.Printf("  fill+round → 上边 n=%d（区宽 346 / 块宽 27）\n", mcTiles["tc"].N)

	fmt.Println("[6] 对照：整图拉伸 vs 9-slice 的圆角半径")
	fmt.Printf("  整图拉伸 27px 圆角 → %.2fpx；9-slice 恒为 27px\n", 27*400/81.0)
}
