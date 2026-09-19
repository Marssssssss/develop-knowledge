// timestep.go — 固定时间步 + accumulator + alpha 插值（与 python/timestep.py 同题）。
//
// 事实来源：Glenn Fiedler《Fix Your Timestep!》。时间一律用整数毫秒记账，
// 保证「步数只由总时长决定」这条结论不被浮点误差污染。
package main

import "fmt"

const (
	dtMs       = 10            // 固定步长 10ms
	maxFrameMs = 250           // 原文：frameTime > 0.25 时钳到 0.25 秒
	k          = 100.0         // 弹簧刚度
)

type state struct {
	x, v float64
}

func integrate(s *state, dt float64) {
	a := -k * s.x
	s.v += a * dt
	s.x += s.v * dt
}

type result struct {
	tMs      int
	acc      int
	x, v     float64
	steps    int
	spf      []int
	rendered []float64
	alphas   []float64
}

func run(frameTimes []int, maxSteps int, clamp bool, interp bool, costPerStep int) result {
	var acc, tMs, pending int
	prev, cur := state{x: 1.0}, state{x: 1.0}
	r := result{}
	for _, base := range frameTimes {
		ft := base + pending // 上一帧的仿真耗时计入本帧帧时间
		if clamp && ft > maxFrameMs {
			ft = maxFrameMs
		}
		acc += ft
		n := 0
		for acc >= dtMs && (maxSteps <= 0 || n < maxSteps) {
			prev = cur
			integrate(&cur, dtMs/1000.0*1.0)
			acc -= dtMs
			tMs += dtMs
			n++
		}
		pending = n * costPerStep
		alpha := float64(acc) / float64(dtMs)
		if interp {
			r.rendered = append(r.rendered, cur.x*alpha+prev.x*(1.0-alpha))
		} else {
			r.rendered = append(r.rendered, cur.x)
		}
		r.alphas = append(r.alphas, alpha)
		r.spf = append(r.spf, n)
		r.steps += n
	}
	r.tMs, r.acc, r.x, r.v = tMs, acc, cur.x, cur.v
	return r
}

func runVariable(frameTimes []int) state {
	s := state{x: 1.0}
	for _, ft := range frameTimes {
		integrate(&s, float64(ft)/1000.0)
	}
	return s
}

func pacing(totalMs, frame int) []int {
	out := []int{}
	for left := totalMs; left > 0; {
		step := frame
		if step > left {
			step = left
		}
		out = append(out, step)
		left -= step
	}
	return out
}

func stalls(frameTimes []int, interp bool) int {
	r := run(frameTimes, 0, true, interp, 0)
	n := 0
	for i := 1; i < len(r.rendered); i++ {
		if r.rendered[i] == r.rendered[i-1] {
			n++
		}
	}
	return n
}

func main() {
	a := run(pacing(1000, 16), 0, true, false, 0)
	b := run(pacing(1000, 33), 0, true, false, 0)
	fmt.Printf("60fps: %d 步, x=%.16f\n", a.steps, a.x)
	fmt.Printf("30fps: %d 步, x=%.16f  → 同输入同输出: %v\n", b.steps, b.x, a.x == b.x)

	va := runVariable(pacing(1000, 16))
	vb := runVariable(pacing(1000, 33))
	fmt.Printf("可变步长 60fps x=%.6f / 30fps x=%.6f → 与帧率耦合: %v\n",
		va.x, vb.x, va.x != vb.x)

	clamped := run([]int{2000, 16}, 0, true, false, 0)
	unclamped := run([]int{2000, 16}, 0, false, false, 0)
	fmt.Printf("2 秒卡顿：钳位 %d 步 vs 不钳位 %d 步\n", clamped.steps, unclamped.steps)

	spiral := run([]int{16, 16, 16, 16, 16, 16, 16, 16}, 0, true, false, 12)
	limited := run([]int{16, 16, 16, 16, 16, 16, 16, 16}, 3, true, false, 12)
	fmt.Printf("死亡螺旋（每步 12ms > dt）：spf=%v\n", spiral.spf)
	fmt.Printf("限制每帧最多 3 步：spf=%v, 末帧 accumulator=%dms\n", limited.spf, limited.acc)

	steadyFrames := []int{}
	for i := 0; i < 40; i++ {
		steadyFrames = append(steadyFrames, 16)
	}
	steady = run(steadyFrames, 0, true, false, 8)
	fmt.Printf("对照组（每步 8ms < dt）：末 4 帧 spf=%v\n", steady.spf[len(steady.spf)-4:])

	high := []int{}
	for i := 0; i < 120; i++ {
		high = append(high, 5)
	}
	fmt.Printf("200fps 下停顿帧：不插值 %d / 插值 %d\n", stalls(high, false), stalls(high, true))

	d := run(pacing(300, 16), 0, true, true, 0)
	inRange := true
	for i := range d.alphas {
		if d.alphas[i] < 0 || d.alphas[i] >= 1 {
			inRange = false
		}
	}
	fmt.Printf("alpha 恒在 [0,1): %v (样例 %v)\n", inRange, d.alphas[:4])
}
