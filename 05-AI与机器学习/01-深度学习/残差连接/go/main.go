// 残差连接的 Go 实现与自检(与 python/residual.py 同题,规模缩小以便纯 Go 跑完)。
//
// 运行:go run .
// 断言只挑「结构性成立」的结论(梯度剖面方向、前向信号增长、短路变体排序):训练损失的
// 绝对值依赖 lr/步数,留给 Python 版,这里不重复。
package main

import (
	"fmt"
	"math"
	"os"
)

const eps = 1e-5

// mtx 是 row-major 的 n×m 矩阵。
type mtx struct {
	n, m int
	v    []float64
}

func newMtx(n, m int) *mtx { return &mtx{n: n, m: m, v: make([]float64, n*m)} }

func (a *mtx) at(i, j int) float64 { return a.v[i*a.m+j] }

func (a *mtx) set(i, j int, x float64) { a.v[i*a.m+j] = x }

func mul(a, b *mtx) *mtx {
	c := newMtx(a.n, b.m)
	for i := 0; i < a.n; i++ {
		for p := 0; p < a.m; p++ {
			w := a.at(i, p)
			for j := 0; j < b.m; j++ {
				c.v[i*c.m+j] += w * b.at(p, j)
			}
		}
	}
	return c
}

func mulT(a, b *mtx) *mtx { // aᵀ·b
	c := newMtx(a.m, b.m)
	for p := 0; p < a.n; p++ {
		for i := 0; i < a.m; i++ {
			w := a.at(p, i)
			for j := 0; j < b.m; j++ {
				c.v[i*c.m+j] += w * b.at(p, j)
			}
		}
	}
	return c
}

func transpose(a *mtx) *mtx { // aᵀ
	t := newMtx(a.m, a.n)
	for i := 0; i < a.n; i++ {
		for j := 0; j < a.m; j++ {
			t.set(j, i, a.at(i, j))
		}
	}
	return t
}

func normM(a *mtx) float64 {
	s := 0.0
	for _, x := range a.v {
		s += x * x
	}
	return math.Sqrt(s)
}

// lnNorm 逐样本归一化(与 Python 版同一层,保证 plain/residual 公平可比)。
func lnNorm(h *mtx) (*mtx, *mtx, []float64) {
	out := newMtx(h.n, h.m)
	mean := make([]float64, h.n)
	rstd := make([]float64, h.n)
	for i := 0; i < h.n; i++ {
		s := 0.0
		for j := 0; j < h.m; j++ {
			s += h.at(i, j)
		}
		mean[i] = s / float64(h.m)
		q := 0.0
		for j := 0; j < h.m; j++ {
			d := h.at(i, j) - mean[i]
			q += d * d
		}
		rstd[i] = 1 / math.Sqrt(q/float64(h.m)+eps)
		for j := 0; j < h.m; j++ {
			out.set(i, j, (h.at(i, j)-mean[i])*rstd[i])
		}
	}
	return out, h, rstd // 第二项即 cache 里的 h
}

func lnNormBack(dy, h *mtx, rstd []float64) *mtx {
	dx := newMtx(dy.n, dy.m)
	dim := float64(dy.m)
	for i := 0; i < dy.n; i++ {
		mu, dvar := 0.0, 0.0
		for j := 0; j < dy.m; j++ {
			mu += h.at(i, j)
		}
		mu /= dim
		for j := 0; j < dy.m; j++ {
			dvar += dy.at(i, j) * (h.at(i, j) - mu)
		}
		dvar *= -0.5 * math.Pow(rstd[i], 3)
		for j := 0; j < dy.m; j++ {
			dx.set(i, j, dy.at(i, j)*rstd[i]+dvar*2.0*(h.at(i, j)-mu)/dim)
		}
	}
	return dx
}

type unit struct{ w1, b1, w2, b2 *mtx }

type lcg struct{ s uint64 }

func (r *lcg) next() float64 {
	r.s = r.s*6364136223846793005 + 1442695040888963407
	return float64(r.s>>11) / float64(1<<53)
}

func (r *lcg) normal() float64 {
	u1, u2 := r.next(), r.next()
	if u1 < 1e-12 {
		u1 = 1e-12
	}
	return math.Sqrt(-2*math.Log(u1)) * math.Cos(2*math.Pi*u2)
}

func randMtx(rng *lcg, n, m int, scale float64) *mtx {
	a := newMtx(n, m)
	for i := range a.v {
		a.v[i] = rng.normal() * scale
	}
	return a
}

func initUnits(rng *lcg, depth, dim int) []unit {
	us := make([]unit, depth)
	sc := 1.0 / math.Sqrt(float64(dim))
	for i := range us {
		us[i] = unit{
			w1: randMtx(rng, dim, dim, sc), b1: newMtx(1, dim),
			w2: randMtx(rng, dim, dim, sc), b2: newMtx(1, dim),
		}
	}
	return us
}

// shortcut 三种短路:恒等 / 0.9h / 0.9·倒序(像无参数的 1×1 卷积)。
func shortcut(h *mtx, kind string) *mtx {
	if kind == "identity" {
		return h
	}
	o := newMtx(h.n, h.m)
	for i := 0; i < h.n; i++ {
		for j := 0; j < h.m; j++ {
			if kind == "scale" {
				o.set(i, j, 0.9*h.at(i, j))
			} else {
				o.set(i, j, 0.9*h.at(i, h.m-1-j))
			}
		}
	}
	return o
}

type cache struct {
	h, a, n *mtx
	rstd    []float64
}

func forward(x *mtx, us []unit, isRes bool, sc string) (*mtx, []cache, []float64) {
	h := x
	cs := make([]cache, 0, len(us))
	mags := make([]float64, 0, len(us))
	x0 := normM(x)
	for _, u := range us {
		n, hc, rstd := lnNorm(h)
		a := mul(n, u.w1)
		r := newMtx(a.n, a.m)
		for i := 0; i < a.n; i++ {
			for j := 0; j < a.m; j++ {
				v := a.at(i, j) + u.b1.at(0, j)
				a.set(i, j, v)
				if v > 0 {
					r.set(i, j, v)
				}
			}
		}
		f := mul(r, u.w2)
		out := newMtx(f.n, f.m)
		var sk *mtx
		if isRes {
			sk = shortcut(h, sc)
		}
		for i := 0; i < f.n; i++ {
			for j := 0; j < f.m; j++ {
				f.set(i, j, f.at(i, j)+u.b2.at(0, j))
				if isRes {
					out.set(i, j, f.at(i, j)+sk.at(i, j))
				} else if f.at(i, j) > 0 {
					out.set(i, j, f.at(i, j))
				}
			}
		}
		cs = append(cs, cache{h: hc, a: a, n: n, rstd: rstd})
		mags = append(mags, normM(out)/x0)
		h = out
	}
	return h, cs, mags
}

// backward 返回每个单元 ‖∂L/∂W1‖;dhSkip 就是 ∂ε/∂x_l 展开式里的「1」那一项。
func backward(dh *mtx, us []unit, cs []cache, isRes bool, sc string) []float64 {
	norms := make([]float64, len(us))
	for i := len(us) - 1; i >= 0; i-- {
		var dhSkip *mtx
		if isRes {
			dhSkip = shortcut(dh, sc)
		}
		dr := mul(dh, transpose(us[i].w2))
		da := newMtx(dr.n, dr.m)
		for r := 0; r < dr.n; r++ {
			for c := 0; c < dr.m; c++ {
				if cs[i].a.at(r, c) > 0 {
					da.set(r, c, dr.at(r, c))
				}
			}
		}
		norms[i] = normM(mulT(cs[i].n, da))
		dn := mul(da, transpose(us[i].w1))
		dbr := lnNormBack(dn, cs[i].h, cs[i].rstd)
		if isRes {
			for k := range dbr.v {
				dbr.v[k] += dhSkip.v[k]
			}
		}
		dh = dbr
	}
	return norms
}

var total, pass, fail int

func check(name string, cond bool, detail string) {
	total++
	if cond {
		pass++
		fmt.Printf("  [PASS] %s  %s\n", name, detail)
	} else {
		fail++
		fmt.Printf("  [FAIL] %s  %s\n", name, detail)
	}
}

func profile(depth, dim int, isRes bool, sc string) (float64, float64) {
	rng := &lcg{s: 12345}
	us := initUnits(rng, depth, dim)
	x := randMtx(rng, 8, dim, 1.0)
	out, cs, mags := forward(x, us, isRes, sc)
	dh := newMtx(out.n, out.m)
	for i := range dh.v {
		dh.v[i] = out.v[i] / 8.0 // loss = 0.5·mean‖h‖²
	}
	norms := backward(dh, us, cs, isRes, sc)
	return norms[0] / norms[len(norms)-1], mags[len(mags)-1]
}

func main() {
	dim, depth := 16, 32
	fmt.Println("== A. 梯度剖面首/末比 ==")
	rp, mp := profile(depth, dim, false, "identity")
	rr, mr := profile(depth, dim, true, "identity")
	fmt.Printf("  plain %.3f   residual %.3f\n", rp, rr)
	check("plain 的梯度随深度被更强压制", rp > rr, fmt.Sprintf("%.3f > %.3f", rp, rr))

	fmt.Println("\n== B. 前向信号量级 ‖x_L‖/‖x_0‖ ==")
	fmt.Printf("  plain %.3f   residual %.3f\n", mp, mr)
	check("residual 累积信号(residual > plain)", mr > mp, fmt.Sprintf("%.3f > %.3f", mr, mp))
	check("plain 的量级有界(<2)", mp < 2.0, fmt.Sprintf("%.3f", mp))

	fmt.Println("\n== C. shortcut 变体的梯度剖面 ==")
	ri, _ := profile(depth, dim, true, "identity")
	rs, _ := profile(depth, dim, true, "scale")
	rj, _ := profile(depth, dim, true, "proj")
	fmt.Printf("  identity %.3f   scale %.3f   proj %.3f\n", ri, rs, rj)
	check("恒等短路的首/末比最大(非恒等压制输入侧梯度)", ri > rs && ri > rj,
		fmt.Sprintf("%.3f > %.3f / %.3f", ri, rs, rj))

	fmt.Printf("\n训练对比见 python/residual.py 第 3 节(纯 Go 三期循环太慢,不在此重复)\n")
	fmt.Printf("\n结果:%d/%d 通过\n", pass, total)
	if fail > 0 {
		os.Exit(1)
	}
}
