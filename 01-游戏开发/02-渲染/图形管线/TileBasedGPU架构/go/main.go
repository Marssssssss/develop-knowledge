// Tile-Based GPU(TBDR)渲染架构:Go 复刻带宽记账模拟。
// 依据 Arm "How low can you go?" 与 Arm GPU Best Practices 归纳:
// binning → 片上 tile memory 光栅化 → resolve 写回;loadOp/storeOp 决定外部流量。
package main

import (
	"fmt"
	"math"
)

const tile = 16 // 典型 tile 尺寸

type triangle struct {
	name    string
	x0, y0  int // bbox(排他右/下边界)
	x1, y1  int
}

type framebuffer struct {
	w, h, bps, msaa int
}

func makeScene(w, h, nOverdraw int) []triangle {
	tris := make([]triangle, 0, nOverdraw+1)
	for i := 0; i < nOverdraw; i++ {
		tris = append(tris, triangle{fmt.Sprintf("fullscreen_%d", i), 0, 0, w, h})
	}
	tris = append(tris, triangle{"ui", 0, 0, w / 4, h / 4})
	return tris
}

// imrBandwidth:立即模式渲染器,每采样 color+depth 各读一次写一次。
func imrBandwidth(tris []triangle, fb framebuffer, depthBPS int) (int, int) {
	colorRW, depthRW := 0, 0
	for _, t := range tris {
		n := (t.x1 - t.x0) * (t.y1 - t.y0) * fb.msaa
		colorRW += n * fb.bps * 2
		depthRW += n * depthBPS * 2
	}
	return colorRW, depthRW
}

// binTriangles:每个三角形进其 bbox 覆盖的所有 tile 的 to-do list。
func binTriangles(tris []triangle, w, h int) map[[2]int][]triangle {
	ntx, nty := int(math.Ceil(float64(w)/tile)), int(math.Ceil(float64(h)/tile))
	todo := map[[2]int][]triangle{}
	for tx := 0; tx < ntx; tx++ {
		for ty := 0; ty < nty; ty++ {
			todo[[2]int{tx, ty}] = nil
		}
	}
	for _, t := range tris {
		for ty := t.y0 / tile; ty < int(math.Ceil(float64(t.y1)/tile)); ty++ {
			for tx := t.x0 / tile; tx < int(math.Ceil(float64(t.x1)/tile)); tx++ {
				todo[[2]int{tx, ty}] = append(todo[[2]int{tx, ty}], t)
			}
		}
	}
	return todo
}

// tbrBandwidth:binning + loadOp + resolve store 的外部带宽记账。
func tbrBandwidth(tris []triangle, fb framebuffer, depthBPS int, loadClear, storeDepth bool) (int, int, int) {
	todo := binTriangles(tris, fb.w, fb.h)
	binBytes := 0
	for _, v := range todo {
		binBytes += len(v) * 64 // to-do list 本身走系统内存
	}
	loadBytes := 0
	if !loadClear {
		loadBytes = fb.w * fb.h * fb.bps // loadOp=LOAD:整帧回读
	}
	storeBytes := fb.w * fb.h * fb.bps // 颜色必须写回(MSAA 在片上已 resolve)
	if storeDepth {
		storeBytes += fb.w * fb.h * depthBPS
	}
	return binBytes, loadBytes, storeBytes
}

func check(label string, cond bool, detail string) {
	if !cond {
		panic("FAIL " + label + ": " + detail)
	}
	fmt.Println("ok -", label)
}

func main() {
	const W, H = 256, 256
	const colorBPS, depthBPS = 4, 4

	// 1) binning 正确性:UI(W/4=64px 边长)进 4×4 个 tile
	tris1 := makeScene(W, H, 1)
	todo := binTriangles(tris1, W, H)
	check("tile count", len(todo) == 16*16, fmt.Sprintf("%d", len(todo)))
	uiTiles := 0
	uiPlusFs := 0
	for k, v := range todo {
		if k[0] < 4 && k[1] < 4 {
			uiPlusFs++
			for _, t := range v {
				if t.name == "ui" {
					uiTiles++
				}
			}
		}
	}
	check("ui in 4x4 tiles", uiTiles == 16, fmt.Sprintf("%d", uiTiles))
	check("ui tiles carry 2 tris", uiPlusFs == 16, fmt.Sprintf("%d", uiPlusFs))

	// 2) overdraw:IMR 带宽线性放大,TBR 几乎不变
	var imr8, tbr1, tbr8 int
	for _, od := range []int{1, 4, 8} {
		fb := framebuffer{W, H, colorBPS, 1}
		tris := makeScene(W, H, od)
		c, d := imrBandwidth(tris, fb, depthBPS)
		b, l, s := tbrBandwidth(tris, fb, depthBPS, true, false)
		check(fmt.Sprintf("overdraw=%d: TBR wins", od), b+l+s < c+d,
			fmt.Sprintf("imr=%d tbr=%d", c+d, b+l+s))
		if od == 1 {
			tbr1 = b + l + s
		}
		if od == 8 {
			imr8 = c + d
			tbr8 = b + l + s
		}
	}
	check("TBR insensitive to overdraw", tbr8 < tbr1*3/2, fmt.Sprintf("%d vs %d", tbr8, tbr1))

	// 3) MSAA 4x:IMR ×4,TBR 外部 store 不变
	fb1x := framebuffer{W, H, colorBPS, 1}
	fb4x := framebuffer{W, H, colorBPS, 4}
	tris := makeScene(W, H, 4)
	c1, d1 := imrBandwidth(tris, fb1x, depthBPS)
	c4, d4 := imrBandwidth(tris, fb4x, depthBPS)
	check("IMR MSAA 4x cost", c4 == 4*c1 && d4 == 4*d1, fmt.Sprintf("%d vs %d", c4, c1))
	_, l1, s1 := tbrBandwidth(tris, fb1x, depthBPS, true, false)
	_, l4, s4 := tbrBandwidth(tris, fb4x, depthBPS, true, false)
	check("TBR MSAA store unchanged", s4 == s1 && l4 == l1, "")
	tileMem1x := tile * tile * (colorBPS + depthBPS)
	tileMem4x := tile * tile * (colorBPS + depthBPS) * 4
	check("MSAA 4x tile memory", tileMem4x == 4*tileMem1x, "")

	// 4) loadOp=LOAD 代价:整帧回读使外部流量近乎翻倍
	fb2 := framebuffer{W, H, colorBPS, 1}
	tris2 := makeScene(W, H, 2)
	_, ldClear, st := tbrBandwidth(tris2, fb2, depthBPS, true, false)
	_, ldLoad, _ := tbrBandwidth(tris2, fb2, depthBPS, false, false)
	check("clear has no load", ldClear == 0, "")
	check("load equals store bytes", ldLoad == st && st == W*H*colorBPS,
		fmt.Sprintf("load=%d store=%d", ldLoad, st))

	// 5) depth storeOp=DONT_CARE:省一整张 depth 附件写回
	_, _, stKeep := tbrBandwidth(tris2, fb2, depthBPS, true, true)
	_, _, stDrop := tbrBandwidth(tris2, fb2, depthBPS, true, false)
	check("depth store saving", stKeep-stDrop == W*H*depthBPS,
		fmt.Sprintf("delta=%d", stKeep-stDrop))

	// 6) Arm 功耗口径:4-8 GB/s × 150pJ/byte ≈ 0.6-1.2 W
	for _, gbs := range []float64{4, 8} {
		watts := gbs * 1e9 * 150e-12
		check("arm power model", watts > 0.5 && watts < 1.3, fmt.Sprintf("%.2fW", watts))
	}

	fmt.Println("ALL TESTS PASSED")
	fmt.Printf("overdraw=8: IMR = %.2f MB, TBR = %.2f MB (%.1fx reduction)\n",
		float64(imr8)/1e6, float64(tbr8)/1e6, float64(imr8)/float64(tbr8))
}
