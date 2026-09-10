// gjk.go — GJK (Gilbert-Johnson-Keerthi) 凸多边形碰撞检测最小实现（2D）。
//
// 核心原理：两凸形状 A、B 相交 <=> 它们的 Minkowski 差 A-B 包含原点。
// GJK 不显式构造 A-B，而是通过 support 函数在其边界上取点，
// 迭代演化一个 simplex（点 -> 线段 -> 三角形），试探能否包围原点。
//
// 实现依据：dyn4j "Collision Detection for Convex Shapes" 教程的
// simplex 演化 + Voronoi 区域判断法（含评论区修正：AB = B - A）。
//
// 运行：go run gjk.go
package main

import "fmt"

const (
	maxIter = 64    // 迭代上限：退化情况下防止死循环
	eps     = 1e-12 // 零向量判定阈值
)

// ---------- 2D 向量运算 ----------

type Vec2 struct{ X, Y float64 }

func (a Vec2) add(b Vec2) Vec2    { return Vec2{a.X + b.X, a.Y + b.Y} }
func (a Vec2) sub(b Vec2) Vec2    { return Vec2{a.X - b.X, a.Y - b.Y} }
func (a Vec2) neg() Vec2          { return Vec2{-a.X, -a.Y} }
func (a Vec2) dot(b Vec2) float64 { return a.X*b.X + a.Y*b.Y }
func (a Vec2) mul(s float64) Vec2 { return Vec2{a.X * s, a.Y * s} }
func (a Vec2) len2() float64      { return a.dot(a) }

// tripleProduct 三重积 (a x b) x c = b*(c·a) - a*(c·b)。
// 用途：求“垂直于 b、且指向 c 一侧”的向量。
func tripleProduct(a, b, c Vec2) Vec2 {
	return b.mul(c.dot(a)).sub(a.mul(c.dot(b)))
}

// ---------- 凸多边形与 support 函数 ----------

type Polygon []Vec2

// supportPoly 返回形状在方向 d 上投影最大的顶点。
// GJK 唯一需要的几何接口，因此对圆/胶囊等曲边形状同样适用。
func supportPoly(s Polygon, d Vec2) Vec2 {
	best, bestProj := s[0], s[0].dot(d)
	for _, p := range s[1:] {
		if proj := p.dot(d); proj > bestProj {
			bestProj, best = proj, p
		}
	}
	return best
}

// minkSupport 返回 Minkowski 差上的 support 点：S_{A-B}(d) = S_A(d) - S_B(-d)。
// 这样完全不需要构造 A-B 的全部点（不可行，点数是 |A|*|B|）。
func minkSupport(a, b Polygon, d Vec2) Vec2 {
	return supportPoly(a, d).sub(supportPoly(b, d.neg()))
}

// centroid 顶点平均质心（凸多边形可用作初始方向的参考点）。
func centroid(s Polygon) Vec2 {
	var c Vec2
	for _, p := range s {
		c = c.add(p)
	}
	return c.mul(1 / float64(len(s)))
}

// ---------- simplex 演化 ----------

// handleLine 线段情形：A 为最后加入点，B 为另一点。
// 新方向取垂直于 AB 且指向原点一侧：d = (AB x AO) x AB。
// 返回 true 表示已判定碰撞；需要继续迭代时截断/更新通过指针传出。
func handleLine(simplex *[]Vec2, d *Vec2) bool {
	s := *simplex
	a, b := s[1], s[0]
	ab := b.sub(a) // 注意：AB = B - A（dyn4j 评论区修正）
	ao := a.neg()  // AO = O - A = -A

	newD := tripleProduct(ab, ao, ab)
	if newD.len2() < eps {
		// 原点恰好在 AB 线上：视为接触（这里按“包含边界即碰撞”处理）
		return true
	}
	*d = newD
	return false
}

// handleTriangle 三角形情形：通过 Voronoi 区域测试判断原点位置。
//   - abPerp·AO > 0：原点在 AB 外侧区域 -> 丢弃 C，朝 abPerp 继续
//   - acPerp·AO > 0：原点在 AC 外侧区域 -> 丢弃 B，朝 acPerp 继续
//   - 否则：原点在三角形内部 -> 碰撞
func handleTriangle(simplex *[]Vec2, d *Vec2) bool {
	s := *simplex
	a, b, c := s[2], s[1], s[0]
	ab := b.sub(a)
	ac := c.sub(a)
	ao := a.neg()

	abPerp := tripleProduct(ac, ab, ab) // 垂直 AB，背离 C
	acPerp := tripleProduct(ab, ac, ac) // 垂直 AC，背离 B

	if abPerp.dot(ao) > 0 {
		s[0], s[1] = s[1], s[2] // 丢弃 C，保留 A、B
		*simplex = s[:2]
		*d = abPerp
		return false
	}
	if acPerp.dot(ao) > 0 {
		s[1] = s[2] // 丢弃 B，保留 A、C
		*simplex = s[:2]
		*d = acPerp
		return false
	}
	return true // 原点在三角形内 -> 相交
}

// gjkIntersect GJK 主循环。返回 (是否相交, 迭代次数)。
func gjkIntersect(a, b Polygon) (bool, int) {
	// 初始方向任意；取中心连线利于尽早退出（dyn4j 建议）
	d := centroid(b).sub(centroid(a))
	if d.len2() < eps {
		d = Vec2{1, 0}
	}

	simplex := []Vec2{minkSupport(a, b, d)}
	d = d.neg()

	for iters := 1; iters < maxIter; iters++ {
		newPt := minkSupport(a, b, d)
		simplex = append(simplex, newPt)

		// 关键终止条件 1：沿 d 方向的最远点都没有越过原点，
		// 说明整个 Minkowski 差在垂直 d 的直线一侧 -> 不含原点 -> 分离
		if newPt.dot(d) <= 0 {
			return false, iters
		}

		// 终止条件 2：simplex 包含原点 -> 相交；否则演化 simplex
		var hit bool
		if len(simplex) == 2 {
			hit = handleLine(&simplex, &d)
		} else {
			hit = handleTriangle(&simplex, &d)
		}
		if hit {
			return true, iters
		}
	}
	return false, maxIter // 浮点退化，保守返回分离
}

// ---------- 测试 ----------

func runCase(name string, a, b Polygon, expect bool) {
	hit, iters := gjkIntersect(a, b)
	status, expectStr, hitStr := "PASS", "SEPARATE", "SEPARATE"
	if expect {
		expectStr = "COLLIDE"
	}
	if hit {
		hitStr = "COLLIDE"
	}
	if hit != expect {
		status = "FAIL"
	}
	fmt.Printf("%-28s -> %s (iters=%d)  expected=%s  [%s]\n",
		name, hitStr, iters, expectStr, status)
}

func main() {
	sqA := Polygon{{0, 0}, {2, 0}, {2, 2}, {0, 2}}  // [0,2]x[0,2]
	sqB1 := Polygon{{1, 1}, {3, 1}, {3, 3}, {1, 3}} // 与 A 重叠
	sqB2 := Polygon{{5, 5}, {7, 5}, {7, 7}, {5, 7}} // 远离 A
	sqB3 := Polygon{{3, 0}, {5, 0}, {5, 2}, {3, 2}} // 与 A 间隙 1
	// 薄重叠：x 方向只侵入 0.001
	sqB4 := Polygon{{1.999, 1.9}, {3.999, 1.9}, {3.999, 3.9}, {1.999, 3.9}}
	pent := Polygon{{3, 1}, {1.618, 2.618}, {-0.618, 1.618}, {-0.618, 0.382}, {1.618, -0.618}}
	tri := Polygon{{2, 2}, {4, 2}, {3, 4}}

	runCase("square vs square overlap", sqA, sqB1, true)
	runCase("square vs square far", sqA, sqB2, false)
	runCase("square vs square gap=1", sqA, sqB3, false)
	runCase("square vs square thin", sqA, sqB4, true)
	runCase("pentagon vs triangle", pent, tri, true)
}
