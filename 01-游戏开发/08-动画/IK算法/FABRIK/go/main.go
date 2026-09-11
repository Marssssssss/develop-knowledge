// Package main implements 2D single-chain FABRIK (Forward And Backward
// Reaching Inverse Kinematics) without joint constraints, after:
//
//   Aristidou & Lasenby, "FABRIK: A fast, iterative solver for the
//   Inverse Kinematics problem", Graphical Models 73(5): 243-260, 2011.
//
// 运行:
//   go run .
//
// 期望输出 (允许最后一位浮点误差):
//   Case 1: 末端误差 ~ 1e-12, 3-6 次迭代
//   Case 2: 不可达目标 -> 链条沿 +x 拉直
//   Case 3: 单次反向 + 正向, 根被拉离又被钉回
package main

import (
	"fmt"
	"math"
)

type Vec2 struct{ X, Y float64 }

func sub(a, b Vec2) Vec2  { return Vec2{a.X - b.X, a.Y - b.Y} }
func add(a, b Vec2) Vec2  { return Vec2{a.X + b.X, a.Y + b.Y} }
func scale(a Vec2, s float64) Vec2 {
	return Vec2{a.X * s, a.Y * s}
}
func dot(a, b Vec2) float64 { return a.X*b.X + a.Y*b.Y }
func length(a Vec2) float64 { return math.Hypot(a.X, a.Y) }

// norm 单位化; |a| 接近 0 时退化为 (1, 0).
func norm(a Vec2) Vec2 {
	l := length(a)
	if l < 1e-12 {
		return Vec2{1, 0}
	}
	return Vec2{a.X / l, a.Y / l}
}

// fabrikBackward 反向: 末端钉到 target, 从 n-1 递减回拉每个关节到固定骨头长度.
func fabrikBackward(p []Vec2, d []float64, target Vec2) {
	n := len(d)
	p[n] = target
	for i := n - 1; i >= 0; i-- {
		dir := norm(sub(p[i], p[i+1]))
		p[i] = add(p[i+1], scale(dir, d[i]))
	}
}

// fabrikForward 正向: 根钉回 root, 从 1 递增前推每个关节到固定骨头长度.
func fabrikForward(p []Vec2, d []float64, root Vec2) {
	n := len(d)
	p[0] = root
	for i := 1; i <= n; i++ {
		dir := norm(sub(p[i], p[i-1]))
		p[i] = add(p[i-1], scale(dir, d[i-1]))
	}
}

// fabrikSolve 完整 FABRIK 求解. 返回实际迭代次数 (0 表示不可达已直接伸展).
func fabrikSolve(p []Vec2, d []float64, target Vec2,
	tol float64, maxIter int) int {
	root := p[0]
	total := 0.0
	for _, di := range d {
		total += di
	}
	if length(sub(target, root)) > total {
		// 不可达: 沿 (root -> target) 单位向量直接拉直
		dir := norm(sub(target, root))
		p[0] = root
		for i := 1; i < len(p); i++ {
			p[i] = add(p[i-1], scale(dir, d[i-1]))
		}
		return 0
	}
	for it := 0; it < maxIter; it++ {
		if length(sub(p[len(p)-1], target)) < tol {
			return it
		}
		fabrikBackward(p, d, target)
		fabrikForward(p, d, root)
	}
	return maxIter
}

func dump(p []Vec2, title string) {
	fmt.Println(title)
	for i, v := range p {
		fmt.Printf("  p[%d] = (%6.3f, %6.3f)\n", i, v.X, v.Y)
	}
}

func main() {
	// 3 段骨头, 总长 6, 初始沿 +x 直线
	d := []float64{2.0, 2.0, 2.0}
	p0 := []Vec2{{0, 0}, {2, 0}, {4, 0}, {6, 0}}

	// Case 1: 可达目标 (4, 3)
	p1 := append([]Vec2(nil), p0...)
	t1 := Vec2{4, 3}
	it1 := fabrikSolve(p1, d, t1, 1e-9, 100)
	dump(p1, "=== Case 1: reachable target ===")
	fmt.Printf("  target = %v, root = %v, iterations = %d\n", t1, p1[0], it1)
	fmt.Printf("  end-effector error = %.3e\n\n", length(sub(p1[len(p1)-1], t1)))

	// Case 2: 不可达目标 (10, 0) -> 沿 +x 拉直
	p2 := append([]Vec2(nil), p0...)
	t2 := Vec2{10, 0}
	fabrikSolve(p2, d, t2, 1e-9, 100)
	dump(p2, "=== Case 2: unreachable target (stretched along +x) ===")
	fmt.Printf("  target = %v, end-effector reached = %v\n\n", t2, p2[len(p2)-1])

	// Case 3: 一次反向 + 一次正向, 看中间态
	p3 := append([]Vec2(nil), p0...)
	t3 := Vec2{4, 3}
	fabrikBackward(p3, d, t3)
	dump(p3, "=== Case 3: 1 BACKWARD pass only ===")
	fmt.Printf("  end effector at target %v\n", p3[len(p3)-1])
	fmt.Printf("  but root drifted from (0,0) to %v (forward will fix it)\n", p3[0])
	fabrikForward(p3, d, Vec2{0, 0})
	dump(p3, "=== Case 3: 1 BACKWARD + 1 FORWARD ===")
	fmt.Printf("  root back to (0,0), end-effector error = %.3e\n\n",
		length(sub(p3[len(p3)-1], t3)))

	// 收敛速度演示
	fmt.Println("=== Convergence trace (case 1) ===")
	p4 := append([]Vec2(nil), p0...)
	for it := 0; it < 8; it++ {
		errBefore := length(sub(p4[len(p4)-1], t1))
		if errBefore < 1e-12 {
			fmt.Printf("  converged at iter %d, err = %.3e\n", it, errBefore)
			break
		}
		fabrikBackward(p4, d, t1)
		fabrikForward(p4, d, Vec2{0, 0})
		errAfter := length(sub(p4[len(p4)-1], t1))
		fmt.Printf("  iter %d: err = %.3e (before backward: %.3e)\n",
			it+1, errAfter, errBefore)
	}
}
