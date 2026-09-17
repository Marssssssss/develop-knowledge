// Forward+ 分块光源剔除:Go 复刻 tile 视锥构建与球体剔除。
// 依据 3dgep.com "Forward+ Rendering" 归纳的算法:
// tile 角射线叉积 → 4 侧平面(过视点);球-视锥平面距离测试 + tile 深度区间测试。
package main

import (
	"fmt"
	"math"
)

const (
	W     = 64
	H     = 64
	TILE  = 16
	NTX   = W / TILE
	NTY   = H / TILE
	FOCAL = W / 2.0
)

type vec3 [3]float64

func normalize(v vec3) vec3 {
	n := math.Sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])
	return vec3{v[0] / n, v[1] / n, v[2] / n}
}

func cross(a, b vec3) vec3 {
	return vec3{a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0]}
}

func dot(a, b vec3) float64 { return a[0]*b[0] + a[1]*b[1] + a[2]*b[2] }

// pixelRay:像素中心发出的视空间方向(x 右 / y 上 / z 前)。
func pixelRay(px, py float64) vec3 {
	return normalize(vec3{(px + 0.5 - W/2.0) / FOCAL, (H/2.0 - py - 0.5) / FOCAL, 1.0})
}

// buildTileFrustum:4 个角射线两两叉积得平面法线,统一指向视锥内部。
func buildTileFrustum(tx, ty int) [4]vec3 {
	tl := pixelRay(float64(tx*TILE), float64(ty*TILE))
	tr := pixelRay(float64((tx+1)*TILE), float64(ty*TILE))
	bl := pixelRay(float64(tx*TILE), float64((ty+1)*TILE))
	br := pixelRay(float64((tx+1)*TILE), float64((ty+1)*TILE))
	center := pixelRay(float64(tx*TILE)+TILE/2.0, float64(ty*TILE)+TILE/2.0)
	planes := [4]vec3{cross(tl, bl), cross(br, tr), cross(tr, tl), cross(bl, br)}
	for i, n := range planes {
		n = normalize(n)
		if dot(n, center) < 0 {
			n = vec3{-n[0], -n[1], -n[2]}
		}
		planes[i] = n
	}
	return planes
}

// sphereInFrustum:任一平面有向距离 < -r → 整球在平面外侧 → 剔除。
func sphereInFrustum(c vec3, r float64, planes [4]vec3) bool {
	for _, n := range planes {
		if dot(n, c) < -r {
			return false
		}
	}
	return true
}

type pointLight struct {
	c vec3    // 视空间球心
	r float64 // 作用半径
}

// cullTile:平面测试 + 深度区间测试(transparent 时近端放宽为 0)。
func cullTile(lights []pointLight, planes [4]vec3, zmin, zmax float64, transparent bool) []int {
	near := 0.0
	if !transparent {
		near = zmin
	}
	var out []int
	for i, l := range lights {
		if !sphereInFrustum(l.c, l.r, planes) {
			continue
		}
		if l.c[2]-l.r > zmax || l.c[2]+l.r < near {
			continue
		}
		out = append(out, i)
	}
	return out
}

// sceneDepth:每像素视空间深度(模拟深度 pre-pass)。
type sceneDepth struct {
	px map[[2]int]float64
}

func newSceneDepth(base float64) *sceneDepth {
	d := &sceneDepth{px: map[[2]int]float64{}}
	for y := 0; y < H; y++ {
		for x := 0; x < W; x++ {
			d.px[[2]int{x, y}] = base
		}
	}
	return d
}

func (d *sceneDepth) patch(x0, y0, x1, y1 int, z float64) {
	for y := y0; y < y1; y++ {
		for x := x0; x < x1; x++ {
			d.px[[2]int{x, y}] = z
		}
	}
}

func (d *sceneDepth) tileMinMax(tx, ty int) (float64, float64) {
	zmin, zmax := math.Inf(1), math.Inf(-1)
	for y := ty * TILE; y < (ty+1)*TILE; y++ {
		for x := tx * TILE; x < (tx+1)*TILE; x++ {
			z := d.px[[2]int{x, y}]
			zmin = math.Min(zmin, z)
			zmax = math.Max(zmax, z)
		}
	}
	return zmin, zmax
}

// exponentialSlices:Z = near*(far/near)^(s/n),指数深度切片。
func exponentialSlices(near, far float64, n int) []float64 {
	out := make([]float64, n+1)
	for s := 0; s <= n; s++ {
		out[s] = near * math.Pow(far/near, float64(s)/float64(n))
	}
	return out
}

func check(label string, cond bool, detail string) {
	if !cond {
		panic("FAIL " + label + ": " + detail)
	}
	fmt.Println("ok -", label)
}

func main() {
	// 1) 视锥平面方向:4 个法线都指向 tile 中心射线一侧
	for ty := 0; ty < NTY; ty++ {
		for tx := 0; tx < NTX; tx++ {
			planes := buildTileFrustum(tx, ty)
			c := pixelRay(float64(tx*TILE)+TILE/2.0, float64(ty*TILE)+TILE/2.0)
			for _, n := range planes {
				check("plane orientation", dot(n, c) > 0, fmt.Sprintf("%v", n))
			}
		}
	}

	// 2) 平面剔除用例:tile(0,0) 在 z=1 处覆盖 x∈[-1,-0.5], y∈[0.5,1]
	p00 := buildTileFrustum(0, 0)
	check("left plane normal +x", p00[0][0] > 0, fmt.Sprintf("%v", p00[0]))
	check("sphere inside", sphereInFrustum(vec3{-0.73, 0.73, 1.0}, 0.05, p00), "")
	check("sphere outside", !sphereInFrustum(vec3{-3.0, 0.3, 1.0}, 0.05, p00), "")
	check("big sphere kept", sphereInFrustum(vec3{-3.0, 0.3, 1.0}, 4.0, p00), "")

	// 3) 深度区间剔除
	depth := newSceneDepth(20.0)
	depth.patch(0, 0, W, H/2, 2.0)
	zmin, zmax := depth.tileMinMax(0, 0)
	check("tile minmax", zmin == 2.0 && zmax == 2.0, fmt.Sprintf("%v %v", zmin, zmax))
	lights := []pointLight{
		{vec3{-1.5, 1.5, 2.0}, 0.5},    // 贴近墙 → 保留
		{vec3{-12.0, 12.0, 19.0}, 0.5}, // z 区间与 [2,2] 不相交 → 剔除
		{vec3{-12.0, 12.0, 19.0}, 18.0}, // 半径跨回区间 → 保留
	}
	lst := cullTile(lights, p00, zmin, zmax, false)
	check("opaque list", len(lst) == 2 && lst[0] == 0 && lst[1] == 2, fmt.Sprintf("%v", lst))

	// 4) 深度不连续:2D tile 过度指派 vs clustered 精确切片
	depth2 := newSceneDepth(20.0)
	depth2.patch(16, 32, 24, 48, 2.0) // tile(1,2) 左半近墙
	zmin2, zmax2 := depth2.tileMinMax(1, 2)
	check("discont minmax", zmin2 == 2.0 && zmax2 == 20.0, "")
	farLights := []pointLight{
		{vec3{-6.0, -5.0, 19.5}, 0.3}, {vec3{-4.5, -5.0, 19.5}, 0.3},
		{vec3{-3.0, -5.0, 19.5}, 0.3}, {vec3{-1.5, -5.0, 19.5}, 0.3},
	}
	planes2 := buildTileFrustum(1, 2)
	tLst := cullTile(farLights, planes2, zmin2, zmax2, false)
	check("tiled over-assign", len(tLst) == 4, fmt.Sprintf("%v", tLst))
	slices := exponentialSlices(0.5, 40.0, 16)
	nearT, nearC, farC := 0, 0, 0
	for y := 32; y < 48; y += 4 {
		for x := 16; x < 32; x += 4 {
			z := depth2.px[[2]int{x, y}]
			// 找 z 所在切片
			s := 0
			for s < len(slices)-2 && z >= slices[s+1] {
				s++
			}
			cLst := cullTile(farLights, planes2, slices[s], slices[s+1], false)
			if z == 2.0 {
				nearT, nearC = len(tLst), len(cLst)
			} else {
				farC = len(cLst)
			}
		}
	}
	check("near pixel: tiled=4 clustered=0", nearT == 4 && nearC == 0,
		fmt.Sprintf("tiled=%d clustered=%d", nearT, nearC))
	check("far pixel: clustered=4", farC == 4, fmt.Sprintf("clustered=%d", farC))

	fmt.Println("ALL TESTS PASSED")
}
