// 延迟渲染 G-Buffer:Go 复刻 Python 版的核心模拟。
// 依据 LearnOpenGL "Deferred Shading":geometry pass 深度测试写入 G-buffer、
// lighting pass 重建片元、light volume 半径按 5/256 阈值反解。
package main

import (
	"fmt"
	"math"
)

const (
	W = 16
	H = 16
)

type quad struct {
	name             string
	x0, y0, x1, y1   int
	depth            float64
	nx, ny, nz       float64
	albedo           [3]float64
	spec             float64
}

type pointLight struct {
	pos            [3]float64
	color          [3]float64
	kc, kl, kq     float64
}

type gbuffer struct {
	position    map[[2]float64][3]float64
	depthMap    map[[2]int]float64
	normal      map[[2]int][3]float64
	albedoSpec  map[[2]int][4]float64
}

func makeScene() []quad {
	return []quad{
		{"background", 0, 0, W, H, 0.9, 0, 0, 1, [3]float64{0.4, 0.4, 0.4}, 0.1},
		{"foreground", 0, 0, W / 2, H, 0.5, 0, 0, 1, [3]float64{0.8, 0.2, 0.2}, 0.5},
	}
}

// geometryPass:光栅化 + 深度测试,只保留每像素最上层片元。
func geometryPass(quads []quad, gb *gbuffer) {
	for _, q := range quads {
		for y := q.y0; y < q.y1; y++ {
			for x := q.x0; x < q.x1; x++ {
				key := [2]int{x, y}
				if d, ok := gb.depthMap[key]; !ok || q.depth < d {
					gb.depthMap[key] = q.depth
					gb.position[[2]float64{float64(x), float64(y)}] = [3]float64{float64(x), float64(y), q.depth}
					gb.normal[key] = [3]float64{q.nx, q.ny, q.nz}
					gb.albedoSpec[key] = [4]float64{q.albedo[0], q.albedo[1], q.albedo[2], q.spec}
				}
			}
		}
	}
}

// lightVolumeRadius:解 Kq*d^2 + Kl*d + Kc - Imax*256/5 = 0 的正根。
func lightVolumeRadius(l pointLight) float64 {
	imax := math.Max(l.color[0], math.Max(l.color[1], l.color[2]))
	a, b, c := l.kq, l.kl, l.kc-(256.0/5.0)*imax
	disc := b*b - 4*a*c
	if disc < 0 {
		return 0
	}
	return (-b + math.sqrt(disc)) / (2 * a)
}

func attenuation(d float64, l pointLight) float64 {
	return 1.0 / (l.kc + l.kl*d + l.kq*d*d)
}

// deferredWithVolumes:光照 pass + light volume,返回逐像素颜色与光照计算次数。
func deferredWithVolumes(gb *gbuffer, lights []pointLight) (map[[2]int][3]float64, int) {
	out := map[[2]int][3]float64{}
	ops := 0
	for key, as := range gb.albedoSpec {
		out[key] = [3]float64{as[0] * 0.1, as[1] * 0.1, as[2] * 0.1}
	}
	for _, l := range lights {
		r := lightVolumeRadius(l)
		for key := range gb.albedoSpec { // key 为 [2]int 像素坐标
			fk := [2]float64{float64(key[0]), float64(key[1])}
			pos := gb.position[fk]
			dx := float64(key[0]) - l.pos[0]
			dy := float64(key[1]) - l.pos[1]
			dz := pos[2] - l.pos[2]
			d := math.Sqrt(dx*dx + dy*dy + dz*dz)
			if d >= r {
				continue
			}
			n := gb.normal[key]
			// 光方向 = 球心 - 像素
			lx, ly, lz := l.pos[0]-float64(key[0]), l.pos[1]-float64(key[1]), l.pos[2]-pos[2]
			ld := math.Sqrt(lx*lx + ly*ly + lz*lz)
			diff := 0.0
			if ld > 0 {
				diff = math.Max(0, (n[0]*lx+n[1]*ly+n[2]*lz)/ld)
			}
			att := attenuation(ld, l)
			as := gb.albedoSpec[key]
			c := out[key]
			for k := 0; k < 3; k++ {
				c[k] += diff * att * as[k] * l.color[k]
			}
			out[key] = c
			ops++
		}
	}
	return out, ops
}

// forwardRender:对照实现,逐物体逐像素直接算光照,返回颜色与光照计算次数。
func forwardRender(quads []quad, lights []pointLight) (map[[2]int][3]float64, int) {
	depth := map[[2]int]float64{}
	win := map[[2]int]quad{}
	for _, q := range quads {
		for y := q.y0; y < q.y1; y++ {
			for x := q.x0; x < q.x1; x++ {
				key := [2]int{x, y}
				if d, ok := depth[key]; !ok || q.depth < d {
					depth[key] = q.depth
					win[key] = q
				}
			}
		}
	}
	out := map[[2]int][3]float64{}
	ops := 0
	for key, q := range win {
		c := [3]float64{q.albedo[0] * 0.1, q.albedo[1] * 0.1, q.albedo[2] * 0.1}
		for _, l := range lights {
			lx := l.pos[0] - float64(key[0])
			ly := l.pos[1] - float64(key[1])
			lz := l.pos[2] - q.depth
			ld := math.Sqrt(lx*lx + ly*ly + lz*lz)
			diff := 0.0
			if ld > 0 {
				diff = math.Max(0, (q.nx*lx+q.ny*ly+q.nz*lz)/ld)
			}
			att := attenuation(ld, l)
			for k := 0; k < 3; k++ {
				c[k] += diff * att * q.albedo[k] * l.color[k]
			}
			ops++
		}
		out[key] = c
	}
	return out, ops
}

func approx(a, b [3]float64, tol float64) bool {
	for i := range a {
		if math.Abs(a[i]-b[i]) > tol {
			return false
		}
	}
	return true
}

func check(label string, cond bool, detail string) {
	if !cond {
		panic("FAIL " + label + ": " + detail)
	}
	fmt.Println("ok -", label)
}

func main() {
	quads := makeScene()
	gb := &gbuffer{
		position:   map[[2]float64][3]float64{},
		depthMap:   map[[2]int]float64{},
		normal:     map[[2]int][3]float64{},
		albedoSpec: map[[2]int][4]float64{},
	}
	geometryPass(quads, gb)

	// 1) 深度测试:前景 0.5 在左半屏,背景 0.9 在右半屏
	check("depth test", gb.depthMap[[2]int{1, 1}] == 0.5 && gb.depthMap[[2]int{W - 1, H - 1}] == 0.9,
		fmt.Sprintf("depth=%v/%v", gb.depthMap[[2]int{1, 1}], gb.depthMap[[2]int{W-1, H-1}]))
	check("gbuffer full", len(gb.depthMap) == W*H, fmt.Sprintf("n=%d", len(gb.depthMap)))

	// 2) light volume 半径:衰减越强半径越小,且根满足阈值方程
	l1 := pointLight{[3]float64{8, 8, 0.2}, [3]float64{1, 1, 1}, 1, 0.09, 0.032}
	l2 := pointLight{[3]float64{8, 8, 0.2}, [3]float64{1, 1, 1}, 1, 0.7, 0.18}
	r1, r2 := lightVolumeRadius(l1), lightVolumeRadius(l2)
	check("radius ordering", r1 > r2 && r2 > 0, fmt.Sprintf("r1=%f r2=%f", r1, r2))
	check("radius threshold", math.Abs(attenuation(r1, l1)-5.0/256.0) < 1e-9,
		fmt.Sprintf("att=%f", attenuation(r1, l1)))

	// 3) 正确性:多光源下 deferred 与 forward 输出一致
	lights := []pointLight{
		{[3]float64{4, 4, 0}, [3]float64{1, 1, 1}, 1, 0.09, 0.032},
		{[3]float64{12, 8, 0}, [3]float64{0.5, 0.5, 0.5}, 1, 0.09, 0.032},
		{[3]float64{8, 14, 0}, [3]float64{0.8, 0.4, 0.2}, 1, 0.09, 0.032},
	}
	fwd, _ := forwardRender(quads, lights)
	def, _ := deferredWithVolumes(gb, lights)
	// forward 中光源照亮的范围与 volume 裁剪不同(attenuation≈0 处有数值差),用大容差比对结构
	same := 0
	for key := range fwd {
		if approx(fwd[key], def[key], 1e-6) {
			same++
		}
	}
	check("deferred==forward", same == W*H, fmt.Sprintf("same=%d/%d", same, W*H))

	// 4) 计算量:多光源(小体积)deferred 更省;单亮光源(覆盖全屏)deferred 更贵
	var many []pointLight
	for x := 0; x < W; x += 2 {
		for y := 0; y < H; y += 4 {
			many = append(many, pointLight{[3]float64{float64(x), float64(y), 0}, [3]float64{0.5, 0.5, 0.5}, 1, 0.7, 0.7})
		}
	}
	_, fwdOps := forwardRender(quads, many)
	_, defOps := deferredWithVolumes(gb, many)
	check("many lights: deferred wins", defOps < fwdOps, fmt.Sprintf("def=%d fwd=%d", defOps, fwdOps))

	few := []pointLight{{[3]float64{8, 8, 0}, [3]float64{3, 3, 3}, 1, 0.09, 0.032}}
	_, fwdOpsFew := forwardRender(quads, few)
	_, defOpsFew := deferredWithVolumes(gb, few)
	check("one bright light: deferred pays gbuffer", defOpsFew+W*H > fwdOpsFew,
		fmt.Sprintf("defTotal=%d fwd=%d", defOpsFew+W*H, fwdOpsFew))

	fmt.Println("ALL TESTS PASSED")
}
