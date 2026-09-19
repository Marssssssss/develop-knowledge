// Groth16 zk-SNARK 的 Go 实现：与 Python 版同一套构造（eprint 2016/260 第 14 页）。
// 群元素用 F_p 里的指数表示，配对 e(X,Y) 记作指数相乘，因此验证等式可以直接核对。
package main

import (
	"fmt"
	"math/big"
)

func f(v int64) *big.Int { return new(big.Int).Mod(big.NewInt(v), P) }
func add(a, b *big.Int) *big.Int { return new(big.Int).Mod(new(big.Int).Add(a, b), P) }
func sub(a, b *big.Int) *big.Int { return new(big.Int).Mod(new(big.Int).Sub(a, b), P) }
func mul(a, b *big.Int) *big.Int { return new(big.Int).Mod(new(big.Int).Mul(a, b), P) }
func inv(a *big.Int) *big.Int { return new(big.Int).ModInverse(a, P) }
func div(a, b *big.Int) *big.Int { return mul(a, inv(b)) }
func zero() *big.Int { return big.NewInt(0) }

// ------------------------------ 多项式（系数低→高） ------------------------------
// -------------------------------- R1CS → QAP --------------------------------
type r1cs struct {
	u, v, w             [][]*big.Int
	n, m, ell           int
}

func (cs *r1cs) holds(a []*big.Int) bool {
	for j := 0; j < cs.n; j++ {
		au, av, aw := zero(), zero(), zero()
		for i := 0; i <= cs.m; i++ {
			au = add(au, mul(cs.u[j][i], a[i]))
			av = add(av, mul(cs.v[j][i], a[i]))
			aw = add(aw, mul(cs.w[j][i], a[i]))
		}
		if mul(au, av).Cmp(aw) != 0 {
			return false
		}
	}
	return true
}

type qap struct {
	us, vs, ws, t []*big.Int
}

func qapFromR1CS(cs *r1cs, roots []*big.Int) ([]*big.Int, []*big.Int, []*big.Int, []*big.Int) {
	us, vs, ws := [][]*big.Int{}, [][]*big.Int{}, [][]*big.Int{}
	for i := 0; i <= cs.m; i++ {
		cu, cv, cw := make([]*big.Int, cs.n), make([]*big.Int, cs.n), make([]*big.Int, cs.n)
		for j := 0; j < cs.n; j++ {
			cu[j], cv[j], cw[j] = cs.u[j][i], cs.v[j][i], cs.w[j][i]
		}
		us = append(us, lagrange(roots, cu))
		vs = append(vs, lagrange(roots, cv))
		ws = append(ws, lagrange(roots, cw))
	}
	t := []*big.Int{f(1)}
	for _, r := range roots {
		t = pMul(t, []*big.Int{new(big.Int).Neg(r), f(1)})
	}
	return us, vs, ws, t
}

func qapH(us, vs, ws, t []*big.Int, a []*big.Int) ([]*big.Int, []*big.Int) {
	U, V, W := []*big.Int{}, []*big.Int{}, []*big.Int{}
	for i, ai := range a {
		if ai.Sign() == 0 {
			continue
		}
		U = pAdd(U, pMul([]*big.Int{ai}, us[i]))
		V = pAdd(V, pMul([]*big.Int{ai}, vs[i]))
		W = pAdd(W, pMul([]*big.Int{ai}, ws[i]))
	}
	num := pSub(pMul(U, V), W)
	h, rem := pDivMod(num, t)
	return h, rem
}

// --------------------------------- Groth16 ---------------------------------
type srs struct {
	alpha, beta, gamma, delta, x *big.Int
	ux, vx, wx                   []*big.Int
	tx                           *big.Int
	sigPub                       []*big.Int
	us, vs, ws, t                [][]*big.Int
	ell                          int
}

func newSRS(us, vs, ws [][]*big.Int, t []*big.Int, ell int, rnd []*big.Int) *srs {
	s := &srs{alpha: rnd[0], beta: rnd[1], gamma: rnd[2], delta: rnd[3], x: rnd[4],
		us: us, vs: vs, ws: ws, t: t, ell: ell}
	s.ux = make([]*big.Int, len(us))
	s.vx = make([]*big.Int, len(us))
	s.wx = make([]*big.Int, len(us))
	s.sigPub = make([]*big.Int, len(us))
	for i := range us {
		s.ux[i] = pEval(us[i], s.x)
		s.vx[i] = pEval(vs[i], s.x)
		s.wx[i] = pEval(ws[i], s.x)
		num := add(add(mul(s.beta, s.ux[i]), mul(s.alpha, s.vx[i])), s.wx[i])
		s.sigPub[i] = div(num, s.gamma)
	}
	s.tx = pEval(t, s.x)
	return s
}

func (s *srs) prove(a []*big.Int, h []*big.Int, r, sc *big.Int) (*big.Int, *big.Int, *big.Int) {
	A, B := s.alpha, s.beta
	wt := zero()
	for i, ai := range a {
		if ai.Sign() == 0 {
			continue
		}
		A = add(A, mul(ai, s.ux[i]))
		B = add(B, mul(ai, s.vx[i]))
		if i > s.ell {
			t := add(add(mul(s.beta, s.ux[i]), mul(s.alpha, s.vx[i])), s.wx[i])
			wt = add(wt, mul(ai, t))
		}
	}
	A = add(A, mul(r, s.delta))
	B = add(B, mul(sc, s.delta))
	C := div(add(wt, mul(pEval(h, s.x), s.tx)), s.delta)
	C = add(C, mul(A, sc))
	C = add(C, mul(r, B))
	C = sub(C, mul(mul(r, sc), s.delta))
	return A, B, C
}

func (s *srs) verify(pub []*big.Int, A, B, C *big.Int) bool {
	lhs := mul(A, B)
	acc := zero()
	for i := 0; i <= s.ell; i++ {
		acc = add(acc, mul(pub[i], s.sigPub[i]))
	}
	rhs := add(add(mul(s.alpha, s.beta), mul(acc, s.gamma)), mul(C, s.delta))
	return lhs.Cmp(rhs) == 0
}

func (s *srs) forge(pub []*big.Int) (*big.Int, *big.Int, *big.Int) {
	A, B := f(0x1234), f(0x5678)
	acc := zero()
	for i := 0; i <= s.ell; i++ {
		t := add(add(mul(s.beta, s.ux[i]), mul(s.alpha, s.vx[i])), s.wx[i])
		acc = add(acc, mul(pub[i], t))
	}
	C := div(sub(sub(mul(A, B), mul(s.alpha, s.beta)), acc), s.delta)
	return A, B, C
}

func main() {
	ok := 0
	chk := func(c bool, m string) {
		if !c {
			panic("断言失败: " + m)
		}
		ok++
	}
	// 电路：x^3 + x + 5 = out，赋值 a = [1, out, x, x^2, x^3]
	mk := func(rows ...[]int64) [][]*big.Int {
		out := [][]*big.Int{}
		for _, r := range rows {
			row := make([]*big.Int, len(r))
			for i, v := range r {
				row[i] = f(v)
			}
			out = append(out, row)
		}
		return out
	}
	cs := &r1cs{
		u: mk([]int64{0, 0, 1, 0, 0}, []int64{0, 0, 0, 1, 0}, []int64{5, 0, 1, 0, 1}),
		v: mk([]int64{0, 0, 1, 0, 0}, []int64{0, 0, 1, 0, 0}, []int64{1, 0, 0, 0, 0}),
		w: mk([]int64{0, 0, 0, 1, 0}, []int64{0, 0, 0, 0, 1}, []int64{0, 1, 0, 0, 0}),
		n: 3, m: 4, ell: 1}
	x, outv := int64(3), int64(3*3*3+3+5)
	a := []*big.Int{f(1), f(outv), f(x), f(9), f(27)}
	chk(cs.holds(a), "合法赋值通过 R1CS 检查")
	chk(!cs.holds([]*big.Int{f(1), f(outv + 1), f(x), f(9), f(27)}), "公开输出改 1 ⇒ R1CS 失败")

	roots := []*big.Int{f(11), f(22), f(33)}
	us, vs, ws, t := qapFromR1CS(cs, roots)
	chk(len(t) == cs.n+1, "t(X) 的次数 = 约束条数")
	for _, r := range roots {
		chk(pEval(t, r).Sign() == 0, "t(X) 在每个根处为 0")
	}
	h, rem := qapH(us, vs, ws, t, a)
	chk(len(rem) == 0, "合法赋值 ⇒ (U·V−W)/t(X) 余式为 0")
	_, remBad := qapH(us, vs, ws, t, []*big.Int{f(1), f(outv), f(x), f(10), f(27)})
	chk(len(remBad) > 0, "非法赋值 ⇒ 余式非零")

	rnd := []*big.Int{f(1001), f(2003), f(3005), f(4007), f(5009)}
	s := newSRS(us, vs, ws, t, cs.ell, rnd)
	A, B, C := s.prove(a, h, f(6011), f(7013))
	pub := []*big.Int{f(1), f(outv)}
	chk(s.verify(pub, A, B, C), "合法证明通过验证")
	chk(!s.verify([]*big.Int{f(1), f(outv + 1)}, A, B, C), "公开输入改 1 ⇒ 验证失败")
	chk(!s.verify(pub, add(A, f(1)), B, C), "A 加 1 ⇒ 验证失败")
	chk(!s.verify(pub, A, B, add(C, f(1))), "C 加 1 ⇒ 验证失败")

	A2, B2, C2 := s.prove(a, h, f(8019), f(9011))
	chk(A.Cmp(A2) != 0 && B.Cmp(B2) != 0 && C.Cmp(C2) != 0, "换 (r,s) ⇒ 三个分量全变")
	chk(s.verify(pub, A2, B2, C2), "随机化后的证明仍然合法（零知识）")

	fA, fB, fC := s.forge(pub)
	chk(s.verify(pub, fA, fB, fC), "持有 τ 可凭空造出通过验证的证明 ⇒ τ 必须销毁")

	fmt.Printf("Groth16 Go 自检通过：%d 项断言\n", ok)
}
