// zbuffer.go — Z-Buffer(深度缓冲)与 Z-Fighting 最小软件光栅化演示。
// 场景与算法同 ../python/zbuffer.py、../c/zbuffer.c,详见 ../README.md。
//
// 运行:go run zbuffer.go
package main

import (
	"fmt"
	"math"
)

const (
	W, H      = 64, 24          // ASCII 帧缓冲尺寸(列 x 行)
	near, far = 1.0, 400.0      // 近/远裁剪面:近面离 0 越近,整体精度越差
	focal     = 40.0            // 针孔投影焦距(像素单位)
)

// vec3 相机空间坐标(右手系,z 朝前为正)
type vec3 struct{ x, y, z float64 }

// canvas 帧缓冲 + 深度缓冲。bits=0 表示浮点深度,否则为定点量化位数
type canvas struct {
	color [H][W]byte
	depth [H][W]float64
	bits  int
}

func newCanvas(bits int) *canvas {
	cv := &canvas{bits: bits}
	for y := 0; y < H; y++ {
		for x := 0; x < W; x++ {
			cv.color[y][x] = '.'
			cv.depth[y][x] = math.Inf(1) // clear 到最远(OpenGL 惯例)
		}
	}
	return cv
}

// toBuffer 把相机空间 z 编码为缓冲值:对 1/z 线性,z=near->0,z=far->1
func (cv *canvas) toBuffer(z float64) float64 {
	d := (1/z - 1/near) / (1/far - 1/near)
	if cv.bits > 0 {
		return math.Round(d * float64(uint(1)<<cv.bits-1)) // 定点量化
	}
	return d
}

func (cv *canvas) put(x, y int, z float64, tag byte) {
	d := cv.toBuffer(z)
	if d < cv.depth[y][x] { // GL_LESS:严格小于才写入
		cv.depth[y][x] = d
		cv.color[y][x] = tag
	}
}

func (cv *canvas) show(title string) {
	border := "+"
	for i := 0; i < W; i++ {
		border += "-"
	}
	border += "+"
	fmt.Printf("\n%s\n%s\n", title, border)
	for y := 0; y < H; y++ {
		fmt.Printf("|%s|\n", string(cv.color[y][:]))
	}
	fmt.Println(border)
}

// project 透视投影:相机空间 -> 像素坐标(含透视除法)
func project(p vec3) (sx, sy float64) {
	return W/2 + focal*p.x/p.z, H/2 - focal*p.y/p.z
}

// rasterize 三角形光栅化:包围盒遍历 + 重心坐标,逐片元处理。
// 透视校正:投影除法后 1/z 在屏幕空间线性,对顶点 1/z 做重心插值再取倒数,
// 即得该片元正确的相机空间深度(与 GPU 行为一致)。
func rasterize(cv *canvas, tri [3]vec3, tag byte, depthTest bool) {
	ax, ay := project(tri[0])
	bx, by := project(tri[1])
	cx, cy := project(tri[2])

	x0 := max(int(math.Min(math.Min(ax, bx), cx)), 0)
	x1 := min(int(math.Max(math.Max(ax, bx), cx))+1, W-1)
	y0 := max(int(math.Min(math.Min(ay, by), cy)), 0)
	y1 := min(int(math.Max(math.Max(ay, by), cy))+1, H-1)

	area := (bx-ax)*(cy-ay) - (by-ay)*(cx-ax)
	if math.Abs(area) < 1e-12 {
		return
	}
	inv := [3]float64{1 / tri[0].z, 1 / tri[1].z, 1 / tri[2].z}
	neg := area < 0

	for py := y0; py <= y1; py++ {
		for px := x0; px <= x1; px++ {
			// e0/e1/e2 分别是顶点 0/1/2 的(未归一化)重心权重
			e0 := (cx-bx)*(float64(py)-by) - (cy-by)*(float64(px)-bx)
			e1 := (ax-cx)*(float64(py)-cy) - (ay-cy)*(float64(px)-cx)
			e2 := (bx-ax)*(float64(py)-ay) - (by-ay)*(float64(px)-ax)
			if (e0 < 0) != neg || (e1 < 0) != neg || (e2 < 0) != neg {
				continue // 片元中心在三角形外(符号不一致)
			}
			if depthTest {
				iz := (e0*inv[0] + e1*inv[1] + e2*inv[2]) / area
				cv.put(px, py, 1/iz, tag)
			} else {
				cv.color[py][px] = tag // 画家算法:直接覆盖
			}
		}
	}
}

// precisionTable 打印不同距离处的深度分辨率(依据 Khronos Wiki 推导)
func precisionTable() {
	fmt.Println("\n深度分辨率表(相邻两个可表示深度的世界空间距离 dz,越小越好):")
	fmt.Println("  依据 Khronos OpenGL Wiki: dz ~= z^2*(far-near)/(far*near*(2^bits-1))")
	fmt.Printf("  NEAR=%.0f, FAR=%.0f\n", near, far)
	fmt.Printf("  %6s %12s %12s\n", "z", "16-bit dz", "24-bit dz")
	for _, z := range []float64{10, 50, 100, 200, 300, 400} {
		base := z * z * (far - near) / (far * near)
		fmt.Printf("  %6.0f %12.4f %12.6f\n", z, base/65535, base/16777215)
	}
	fmt.Printf("  经验法则:log2(FAR/NEAR) = %.1f bit 精度损失(OpenGL 蓝皮书)\n",
		math.Log2(far/near))
}

// 场景 A:互相贯穿的三角形对,平均深度相同(画家算法的噩梦)。
// T1 平面 z = 3.2 + 0.5*y,T2 镜像 z = 3.2 - 0.5*y,在 y=0 相交
var sceneA = [2][3]vec3{
	{{-1.8, -0.7, 2.85}, {1.8, -0.7, 2.85}, {0, 0.7, 3.55}}, // 'A'
	{{-1.8, 0.7, 2.85}, {1.8, 0.7, 2.85}, {0, -0.7, 3.55}},  // 'B'
}

func main() {
	fmt.Println("==================================================================")
	fmt.Println("场景 A:互相贯穿的三角形 —— 画家算法 vs Z-Buffer")
	fmt.Println("==================================================================")

	// 画家算法:平均深度相同,先画 A 后画 B,B 整体覆盖(贯穿处错误)
	painter := newCanvas(0)
	rasterize(painter, sceneA[0], 'A', false)
	rasterize(painter, sceneA[1], 'B', false)
	painter.show("画家算法(无深度测试,后画的 B 整体覆盖,贯穿处错误):")

	zbuf := newCanvas(0)
	rasterize(zbuf, sceneA[0], 'A', true)
	rasterize(zbuf, sceneA[1], 'B', true)
	zbuf.show("Z-Buffer 浮点深度(逐像素测试,相交线正确):")

	fmt.Println()
	fmt.Println("==================================================================")
	fmt.Println("场景 B:近平行三角形对(D 恒比 C 近 0.3)—— 深度位数与 Z-Fighting")
	fmt.Println("==================================================================")

	slantC := [3]vec3{{-100, -40, 100}, {100, -40, 300}, {0, 40, 200}}
	slantD := [3]vec3{}
	for i, p := range slantC {
		slantD[i] = vec3{p.x, p.y, p.z + 0.3}
	}
	modes := []struct {
		bits int
		name string
	}{
		{0, "Z-Buffer 浮点深度(正确:D 全胜):"},
		{16, "Z-Buffer 16-bit 定点深度(远处量化步长 > 0.3,Z-Fighting 条带):"},
		{24, "Z-Buffer 24-bit 定点深度(步长足够小,D 仍全胜):"},
	}
	for _, m := range modes {
		cv := newCanvas(m.bits)
		rasterize(cv, slantC, 'C', true)
		rasterize(cv, slantD, 'D', true)
		cv.show(m.name)
	}

	precisionTable()
}
