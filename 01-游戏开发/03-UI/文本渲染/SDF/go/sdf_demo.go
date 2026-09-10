// sdf_demo.go — 有符号距离场(SDF)字形渲染原理演示
//
// 镜像 C 版实现（流程说明见 ../README.md）：
//   1. "7" 字形闭合多边形作为矢量轮廓（单位坐标 [0,16]，y 向下）
//   2. 64x64 网格采样精确有符号距离构建 SDF 纹理（0.5 恰在轮廓上）
//   3. 8x 放大(512x512)对比三种采样：最近邻 / 二值双线性 / SDF 双线性+smoothstep
//   4. 基于 SDF 的阴影 + 描边特效（Valve 论文 specialized effects）
//
// 输出为二进制 PPM(P6)，可用 GIMP / IrfanView / magick 查看。
// 运行: go run sdf_demo.go
package main

import (
	"fmt"
	"math"
	"os"
)

const (
	domain = 16.0  // 形状定义域边长（单位坐标系）
	texN   = 64    // SDF / coverage 纹理分辨率
	outN   = 512   // 输出图像分辨率（8x 纹理放大）
	rng    = 4.0   // 距离归一化截断半径(spread)，单位坐标
	aaW    = 0.01  // smoothstep 半宽（值域单位），约 2 个输出像素
)

// glyph 是 "7" 字形闭合多边形轮廓（屏幕坐标，y 向下）
var glyph = [][2]float64{
	{2.0, 2.0},   // 左上
	{14.0, 2.0},  // 右上
	{14.0, 4.5},  // 顶横条下缘（右）
	{9.0, 14.0},  // 斜笔右下端
	{5.5, 14.0},  // 斜笔左下端
	{10.5, 4.5},  // 斜笔上端（左）
	{2.0, 4.5},   // 顶横条下缘（左）
}

func clamp01(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

// smoothstep 是 GLSL 同名内建函数的等价实现
func smoothstep(a, b, x float64) float64 {
	t := clamp01((x - a) / (b - a))
	return t * t * (3 - 2*t)
}

// pointInGlyph 射线法点在多边形内判定：向 +x 发射线，穿越边次数为奇数则在内
func pointInGlyph(px, py float64) bool {
	inside := false
	n := len(glyph)
	for i := 0; i < n; i++ {
		a, b := glyph[i], glyph[(i+1)%n]
		if (a[1] > py) != (b[1] > py) {
			xAt := a[0] + (py-a[1])*(b[0]-a[0])/(b[1]-a[1])
			if px < xAt {
				inside = !inside
			}
		}
	}
	return inside
}

// distPointSegment 计算点到线段最短欧氏距离（投影 clamp 到 [0,1]）
func distPointSegment(px, py, ax, ay, bx, by float64) float64 {
	dx, dy := bx-ax, by-ay
	len2 := dx*dx + dy*dy
	t := 0.0
	if len2 > 0 {
		t = clamp01(((px-ax)*dx + (py-ay)*dy) / len2)
	}
	cx, cy := ax+t*dx, ay+t*dy
	return math.Hypot(px-cx, py-cy)
}

// signedDistance 计算精确有符号距离：对所有轮廓边取最短；内部取负（内负外正约定）
func signedDistance(px, py float64) float64 {
	best := math.Inf(1)
	n := len(glyph)
	for i := 0; i < n; i++ {
		a, b := glyph[i], glyph[(i+1)%n]
		if d := distPointSegment(px, py, a[0], a[1], b[0], b[1]); d < best {
			best = d
		}
	}
	if pointInGlyph(px, py) {
		return -best
	}
	return best
}

// buildTextures 步骤 1：构建 SDF 纹理与二值 coverage 纹理
func buildTextures() (sdf, mask [][]float64) {
	sdf = make([][]float64, texN)
	mask = make([][]float64, texN)
	for j := 0; j < texN; j++ {
		sdf[j] = make([]float64, texN)
		mask[j] = make([]float64, texN)
		for i := 0; i < texN; i++ {
			px := (float64(i) + 0.5) / texN * domain
			py := (float64(j) + 0.5) / texN * domain
			d := signedDistance(px, py)
			sdf[j][i] = clamp01(0.5 - d/(2*rng)) // 0.5 即轮廓
			if d < 0 {
				mask[j][i] = 1 // 传统位图字体
			}
		}
	}
	return sdf, mask
}

// bilinear 双线性采样（等价 GPU 的 GL_LINEAR）；fx/fy 为连续纹素坐标
func bilinear(tex [][]float64, fx, fy float64) float64 {
	x0, y0 := math.Floor(fx), math.Floor(fy)
	tx, ty := fx-x0, fy-y0
	ix0, iy0 := int(x0), int(y0)
	ix1, iy1 := ix0+1, iy0+1
	if ix0 < 0 {
		ix0 = 0
	}
	if ix0 > texN-1 {
		ix0 = texN - 1
	}
	if iy0 < 0 {
		iy0 = 0
	}
	if iy0 > texN-1 {
		iy0 = texN - 1
	}
	if ix1 < 0 {
		ix1 = 0
	}
	if ix1 > texN-1 {
		ix1 = texN - 1
	}
	if iy1 < 0 {
		iy1 = 0
	}
	if iy1 > texN-1 {
		iy1 = texN - 1
	}
	v00, v10 := tex[iy0][ix0], tex[iy0][ix1]
	v01, v11 := tex[iy1][ix0], tex[iy1][ix1]
	return (v00*(1-tx) + v10*tx)*(1-ty) + (v01*(1-tx)+v11*tx)*ty
}

// writePPM 写出二进制 PPM(P6) 文件；rgb 为 outN*outN*3 字节
func writePPM(path string, rgb []byte) {
	f, err := os.Create(path)
	if err != nil {
		fmt.Fprintln(os.Stderr, "create", path, "failed:", err)
		os.Exit(1)
	}
	defer f.Close()
	if _, err := fmt.Fprintf(f, "P6\n%d %d\n255\n", outN, outN); err != nil {
		fmt.Fprintln(os.Stderr, "write", path, "failed:", err)
		os.Exit(1)
	}
	if _, err := f.Write(rgb); err != nil {
		fmt.Fprintln(os.Stderr, "write", path, "failed:", err)
		os.Exit(1)
	}
}

// toTexel 把输出像素映射到连续纹素坐标
func toTexel(x, y int) (fx, fy float64) {
	fx = (float64(x)+0.5)/outN*texN - 0.5
	fy = (float64(y)+0.5)/outN*texN - 0.5
	return fx, fy
}

// renderGray 按 sample(x, y) -> [0,1] 的灰度渲染整帧
func renderGray(out []byte, sample func(x, y int) float64) {
	for y := 0; y < outN; y++ {
		for x := 0; x < outN; x++ {
			g := byte(sample(x, y)*255.0 + 0.5)
			idx := (y*outN + x) * 3
			out[idx], out[idx+1], out[idx+2] = g, g, g
		}
	}
}

func main() {
	sdf, mask := buildTextures()
	fmt.Printf("SDF texture %dx%d built (range +/-%.1f units)\n", texN, texN, rng)

	out := make([]byte, outN*outN*3)

	// 5a. 距离场可视化
	renderGray(out, func(x, y int) float64 {
		ix := int((float64(x)+0.5)/outN*float64(texN))
		iy := int((float64(y)+0.5)/outN*float64(texN))
		if ix > texN-1 {
			ix = texN - 1
		}
		if iy > texN-1 {
			iy = texN - 1
		}
		return sdf[iy][ix]
	})
	writePPM("sdf_texture.ppm", out)

	// 5b. 最近邻 + alpha test：阶梯锯齿
	renderGray(out, func(x, y int) float64 {
		fx, fy := toTexel(x, y)
		ix := int(math.Round(fx))
		iy := int(math.Round(fy))
		if ix < 0 {
			ix = 0
		}
		if ix > texN-1 {
			ix = texN - 1
		}
		if iy < 0 {
			iy = 0
		}
		if iy > texN-1 {
			iy = texN - 1
		}
		return smoothstep(0.5-aaW, 0.5+aaW, sdf[iy][ix])
	})
	writePPM("nearest.ppm", out)

	// 5c. 二值 coverage 双线性：边缘线性模糊
	renderGray(out, func(x, y int) float64 {
		fx, fy := toTexel(x, y)
		return bilinear(mask, fx, fy) // 直接输出覆盖率 -> 可见模糊
	})
	writePPM("bilinear_mask.ppm", out)

	// 5d. SDF 双线性 + smoothstep：平滑边缘（主角）
	renderGray(out, func(x, y int) float64 {
		fx, fy := toTexel(x, y)
		v := bilinear(sdf, fx, fy)
		return smoothstep(0.5-aaW, 0.5+aaW, v)
	})
	writePPM("bilinear_sdf.ppm", out)

	// 5e. 特效：阴影（偏移采样）+ 描边（阈值带），均来自同一张 SDF
	for y := 0; y < outN; y++ {
		for x := 0; x < outN; x++ {
			fx, fy := toTexel(x, y)
			v := bilinear(sdf, fx, fy)
			sv := bilinear(sdf, fx+3.0, fy+3.0) // 右下偏移 3 纹素
			shadow := smoothstep(0.5-aaW, 0.5+aaW, sv)
			glyphA := smoothstep(0.5-aaW, 0.5+aaW, v)
			outline := v > 0.5-0.08 && v < 0.5+0.02 // 外描边带
			// 合成顺序：深蓝背景 -> 灰色阴影 -> 黄色描边 -> 白色字形
			r, g, b := 24.0, 28.0, 56.0
			r += (96.0 - r) * shadow
			g += (102.0 - g) * shadow
			b += (120.0 - b) * shadow
			if outline {
				r, g, b = 232.0, 180.0, 48.0
			}
			if glyphA > 0 {
				r, g, b = 245.0, 245.0, 245.0
			}
			idx := (y*outN + x) * 3
			out[idx] = byte(r + 0.5)
			out[idx+1] = byte(g + 0.5)
			out[idx+2] = byte(b + 0.5)
		}
	}
	writePPM("effects.ppm", out)

	fmt.Printf("wrote: sdf_texture.ppm nearest.ppm bilinear_mask.ppm "+
		"bilinear_sdf.ppm effects.ppm (%dx%d)\n", outN, outN)
}
