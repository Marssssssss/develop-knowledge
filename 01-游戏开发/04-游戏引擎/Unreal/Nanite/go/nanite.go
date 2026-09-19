// nanite.go — Nanite 虚拟化几何关键机制的最小模型（与 python/nanite.py 同题）。
//
// 事实来源：Brian Karis《Nanite A Deep Dive》(SIGGRAPH 2021 Advances in Real-Time Rendering)
// 与 Epic 官方 Nanite 文档。覆盖 128 三角形 cluster、group→merge→simplify 50%→split 的
// 构建循环、DAG 结构、组内共享 unioned error/bounds、误差单调强制、可并行的 LOD 选择、
// ParentError 剪枝与 visibility buffer 的材质求值次数。
package main

import (
	"fmt"
	"math"
)

const (
	clusterTris   = 128
	groupSize     = 4
	pixelThresh   = 1.0
	proj          = 1000.0
)

type cluster struct {
	cid     int
	level   int
	tris    int
	error   float64
	cx, cy  float64
	cz      float64
	radius  float64
	children []*cluster
	parents  []*cluster
}

func rawError(level, idx, gen int) float64 {
	jitter := 1.0 + float64((level*37+idx*17+gen*11)%7)*0.3
	return 0.001 * math.Pow(2.0, float64(level)) * jitter
}

func sphereUnion(cs []*cluster) (float64, float64, float64, float64) {
	minX, maxX := cs[0].cx, cs[0].cx
	minY, maxY := cs[0].cy, cs[0].cy
	minZ, maxZ := cs[0].cz, cs[0].cz
	for _, c := range cs {
		minX, maxX = math.Min(minX, c.cx), math.Max(maxX, c.cx)
		minY, maxY = math.Min(minY, c.cy), math.Max(maxY, c.cy)
		minZ, maxZ = math.Min(minZ, c.cz), math.Max(maxZ, c.cz)
	}
	cx, cy, cz := (minX+maxX)/2, (minY+maxY)/2, (minZ+maxZ)/2
	r := 0.0
	for _, c := range cs {
		d := math.Sqrt((c.cx-cx)*(c.cx-cx) + (c.cy-cy)*(c.cy-cy) + (c.cz-cz)*(c.cz-cz))
		if d+c.radius > r {
			r = d + c.radius
		}
	}
	return cx, cy, cz, r
}

type dag struct {
	levels [][]*cluster
	roots  []*cluster
}

func buildDAG(leafCount int) *dag {
	leaves := make([]*cluster, leafCount)
	for i := range leaves {
		leaves[i] = &cluster{cid: i, level: 0, tris: clusterTris,
			cx: float64(i % 8), cy: float64(i / 8), radius: 0.5}
	}
	d := &dag{levels: [][]*cluster{leaves}}
	for len(d.levels[len(d.levels)-1]) > 1 {
		cur := d.levels[len(d.levels)-1]
		level := len(d.levels)
		nxt := []*cluster{}
		for gi := 0; gi < len(cur); gi += groupSize {
			end := gi + groupSize
			if end > len(cur) {
				end = len(cur)
			}
			group := cur[gi:end]
			cx, cy, cz, r := sphereUnion(group)
			unionErr := 0.0
			for _, c := range group {
				if c.error > unionErr {
					unionErr = c.error
				}
			}
			for _, c := range group { // 同组共享 unioned error 与 bounds
				c.cx, c.cy, c.cz, c.radius, c.error = cx, cy, cz, r, unionErr
			}
			merged := 0
			for _, c := range group {
				merged += c.tris
			}
			newCount := (merged / 2) / clusterTris // 简化 50% 后切成 128 三角的 cluster
			for j := 0; j < newCount; j++ {
				err := rawError(level, len(nxt), gi)
				if unionErr > err { // 强制单调：parent 误差 >= 孩子
					err = unionErr
				}
				p := &cluster{cid: len(nxt), level: level, tris: clusterTris,
					error: err, cx: cx, cy: cy, cz: cz, radius: r}
				p.children = append(p.children, group...) // 组内每个 cluster 都是它的孩子
				for _, c := range group {
					c.parents = append(c.parents, p)
				}
				nxt = append(nxt, p)
			}
		}
		d.levels = append(d.levels, nxt)
	}
	d.roots = d.levels[len(d.levels)-1]
	return d
}

func viewError(c *cluster, camZ float64) float64 {
	dx, dy, dz := c.cx, c.cy, c.cz-camZ
	dist := math.Sqrt(dx*dx+dy*dy+dz*dz) - c.radius
	if dist < 0.05 {
		dist = 0.05
	}
	return c.error * proj / dist
}

func parentViewError(c *cluster, camZ float64) float64 {
	if len(c.parents) == 0 {
		return math.Inf(1)
	}
	m := 0.0
	for _, p := range c.parents {
		if v := viewError(p, camZ); v > m {
			m = v
		}
	}
	return m
}

func selectCut(all []*cluster, thr, camZ float64) []*cluster {
	out := []*cluster{}
	for _, c := range all {
		if parentViewError(c, camZ) > thr && viewError(c, camZ) <= thr {
			out = append(out, c)
		}
	}
	return out
}

func selectWithCull(roots []*cluster, thr, camZ float64) ([]*cluster, int) {
	selected := []*cluster{}
	seen := map[int]bool{}
	evaluated := 0
	var walk func(c *cluster)
	walk = func(c *cluster) {
		if seen[c.cid*100+c.level] {
			return
		}
		seen[c.cid*100+c.level] = true
		evaluated++
		if parentViewError(c, camZ) > thr && viewError(c, camZ) <= thr {
			selected = append(selected, c)
		}
		if len(c.children) == 0 {
			return
		}
		bound := 0.0
		for _, ch := range c.children {
			if v := parentViewError(ch, camZ); v > bound {
				bound = v
			}
		}
		if bound <= thr { // 整棵子树都不可能被选中
			return
		}
		for _, ch := range c.children {
			walk(ch)
		}
	}
	for _, r := range roots {
		walk(r)
	}
	return selected, evaluated
}

func pathsToLeaves(roots []*cluster) [][]*cluster {
	out := [][]*cluster{}
	var walk func(c *cluster, path []*cluster)
	walk = func(c *cluster, path []*cluster) {
		p := append(append([]*cluster{}, path...), c)
		if len(c.children) == 0 {
			out = append(out, p)
			return
		}
		for _, ch := range c.children {
			walk(ch, p)
		}
	}
	for _, r := range roots {
		walk(r, nil)
	}
	return out
}

func allClusters(d *dag) []*cluster {
	out := []*cluster{}
	for _, lv := range d.levels {
		out = append(out, lv...)
	}
	return out
}

func frustumCull(all []*cluster, camZ float64) (kept []*cluster, occluded int) {
	for _, c := range all {
		if c.cx < -6 || c.cx > 6 { // 视锥外
			continue
		}
		if c.cz > camZ-0.5 { // 相机背后
			continue
		}
		if c.level <= 1 && c.cy > 3.0 { // 简化 HZB 遮挡
			occluded++
			continue
		}
		kept = append(kept, c)
	}
	return kept, occluded
}

func shadeCost(pixels int, overdraw []int, mode string) int {
	if mode == "visibility_buffer" {
		return pixels
	}
	sum := 0
	for _, o := range overdraw {
		sum += o
	}
	return sum
}

func main() {
	d := buildDAG(64)
	all := allClusters(d)
	counts := []int{}
	for _, lv := range d.levels {
		counts = append(counts, len(lv))
	}
	fmt.Printf("各层 cluster 数 = %v（每级减半）\n", counts)
	fmt.Printf("顶层 parent 的孩子数 = %d（DAG 而非二叉树）\n", len(d.levels[1][0].children))
	fmt.Printf("leaf 的 parent 数 = %d（兄弟共享 parent）\n", len(d.levels[0][0].parents))

	paths := pathsToLeaves(d.roots)
	for _, thr := range []float64{0.5, 1.0, 2.0, 4.0} {
		sel := selectCut(all, thr, 10.0)
		hitOK := true
		for _, p := range paths {
			n := 0
			for _, a := range p {
				for _, b := range sel {
					if a == b {
						n++
						break
					}
				}
			}
			if n != 1 {
				hitOK = false
			}
		}
		fmt.Printf("阈值 %.1f：选中 %d 个，%d 条路径各命中 1 个 = %v\n", thr, len(sel), len(paths), hitOK)
	}

	flat := selectCut(all, pixelThresh, 10.0)
	tree, evaluated := selectWithCull(d.roots, pixelThresh, 10.0)
	fmt.Printf("ParentError 剪枝：结果 %d == %d，评估 %d < 全量 %d\n",
		len(tree), len(flat), evaluated, len(all))

	near := len(selectCut(all, pixelThresh, 6.0))
	far := len(selectCut(all, pixelThresh, 40.0))
	fmt.Printf("相机 6 → %d 个 cluster，相机 40 → %d 个（拉远更粗）\n", near, far)

	pixels := 1920 * 1080
	overdraw := []int{pixels / 2, pixels / 2 * 4}
	fmt.Printf("材质求值：visibility buffer %d 次 vs forward %d 次\n",
		shadeCost(pixels, overdraw, "visibility_buffer"), shadeCost(pixels, overdraw, "forward"))

	kept, occluded := frustumCull(all, 10.0)
	fmt.Printf("剔除后候选 %d / %d，其中被遮挡剔除 %d\n", len(kept), len(all), occluded)
}
