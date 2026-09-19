package main

import "math/big"

var P, _ = new(big.Int).SetString("2305843009213693951", 10) // 2^61 - 1

func trim(c []*big.Int) []*big.Int {
	for len(c) > 0 && c[len(c)-1].Sign() == 0 {
		c = c[:len(c)-1]
	}
	return c
}

func pAdd(a, b []*big.Int) []*big.Int {
	n := len(a)
	if len(b) > n {
		n = len(b)
	}
	out := make([]*big.Int, n)
	for i := 0; i < n; i++ {
		x, y := zero(), zero()
		if i < len(a) {
			x = a[i]
		}
		if i < len(b) {
			y = b[i]
		}
		out[i] = add(x, y)
	}
	return trim(out)
}

func pSub(a, b []*big.Int) []*big.Int {
	n := len(a)
	if len(b) > n {
		n = len(b)
	}
	out := make([]*big.Int, n)
	for i := 0; i < n; i++ {
		x, y := zero(), zero()
		if i < len(a) {
			x = a[i]
		}
		if i < len(b) {
			y = b[i]
		}
		out[i] = sub(x, y)
	}
	return trim(out)
}

func pMul(a, b []*big.Int) []*big.Int {
	if len(a) == 0 || len(b) == 0 {
		return nil
	}
	out := make([]*big.Int, len(a)+len(b)-1)
	for i := range out {
		out[i] = zero()
	}
	for i, ai := range a {
		if ai.Sign() == 0 {
			continue
		}
		for j, bj := range b {
			out[i+j] = add(out[i+j], mul(ai, bj))
		}
	}
	return trim(out)
}

func pEval(a []*big.Int, x *big.Int) *big.Int {
	acc := zero()
	for i := len(a) - 1; i >= 0; i-- {
		acc = add(mul(acc, x), a[i])
	}
	return acc
}

func pDivMod(a, b []*big.Int) ([]*big.Int, []*big.Int) {
	r := make([]*big.Int, len(a))
	copy(r, a)
	q := make([]*big.Int, max2(0, len(a)-len(b)+1))
	for i := range q {
		q[i] = zero()
	}
	binv := inv(b[len(b)-1])
	for i := len(a) - 1; i >= len(b)-1; i-- {
		if r[i].Sign() == 0 {
			continue
		}
		coef := mul(r[i], binv)
		q[i-len(b)+1] = coef
		for j, bj := range b {
			k := i - len(b) + 1 + j
			r[k] = sub(r[k], mul(coef, bj))
		}
	}
	return trim(q), trim(r)
}

func max2(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func lagrange(xs, ys []*big.Int) []*big.Int {
	out := []*big.Int{}
	for i, xi := range xs {
		term := []*big.Int{ys[i]}
		for j, xj := range xs {
			if i == j {
				continue
			}
			den := inv(sub(xi, xj))
			term = pMul(term, []*big.Int{mul(new(big.Int).Neg(xj), den), den})
		}
		out = pAdd(out, term)
	}
	return out
}

