// GF(2^8) 上的 Shamir 分享（对应 python/gf256.py），逐行复刻 Vault shamir.go。

package main

import (
	"errors"
	"math/rand"
)

const irred uint8 = 0x1B // x^8+x^4+x^3+x+1 的低 8 位

func gAdd(a, b uint8) uint8 { return a ^ b }

// gMul 逐字照抄 Vault：-(x) 在无符号数上是「模 2^8 取负」，
// 所以 -1 得到 255，`(-bit)&a` 即「条件成立则取 a，否则取 0」。
func gMul(a, b uint8) uint8 {
	var r uint8
	for i := 7; i >= 0; i-- {
		bit := (b >> i) & 1
		r = (-bit)&a ^ (-(r >> 7))&irred ^ (r + r)
	}
	return r
}

// gInv 是 Vault 的求逆链：连乘 11 次得到 a^254 = a^-1（域的阶是 255）。
func gInv(a uint8) uint8 {
	b := gMul(a, a)   // a^2
	c := gMul(a, b)   // a^3
	b = gMul(c, c)    // a^6
	b = gMul(b, b)    // a^12
	c = gMul(b, c)    // a^15
	b = gMul(b, b)    // a^24
	b = gMul(b, b)    // a^48
	b = gMul(b, c)    // a^63
	b = gMul(b, b)    // a^126
	b = gMul(a, b)    // a^127
	return gMul(b, b) // a^254
}

func gDiv(a, b uint8) (uint8, error) {
	if b == 0 {
		return 0, errors.New("divide by zero")
	}
	if a == 0 {
		return 0, nil
	}
	return gMul(a, gInv(b)), nil
}

func gEval(coeffs []uint8, x uint8) uint8 {
	if x == 0 {
		return coeffs[0]
	}
	out := coeffs[len(coeffs)-1]
	for i := len(coeffs) - 2; i >= 0; i-- {
		out = gAdd(gMul(out, x), coeffs[i])
	}
	return out
}

func gInterpolate(xs, ys []uint8, x uint8) (uint8, error) {
	res := uint8(0)
	for i := range xs {
		basis := uint8(1)
		for j := range xs {
			if i == j {
				continue
			}
			num := gAdd(x, xs[j])
			den := gAdd(xs[i], xs[j])
			t, err := gDiv(num, den)
			if err != nil {
				return 0, err
			}
			basis = gMul(basis, t)
		}
		res = gAdd(res, gMul(ys[i], basis))
	}
	return res, nil
}

// SplitVault 复刻 Vault：每字节一条多项式，份额 = y_1..y_n 再附一个 x 字节。
func SplitVault(secret []byte, parts, threshold int, rnd *rand.Rand) ([][]byte, error) {
	if parts < threshold || parts > 255 || threshold < 2 || threshold > 255 {
		return nil, errors.New("bad parts/threshold")
	}
	if len(secret) == 0 {
		return nil, errors.New("cannot split an empty secret")
	}
	xs := rnd.Perm(255)
	out := make([][]byte, parts)
	for i := range out {
		out[i] = make([]byte, len(secret)+1)
		out[i][len(secret)] = uint8(xs[i]) + 1 // x ∈ [1,255]
	}
	for idx, val := range secret {
		coeffs := []uint8{val}
		for k := 0; k < threshold-1; k++ {
			coeffs = append(coeffs, uint8(rnd.Intn(256)))
		}
		for i := 0; i < parts; i++ {
			out[i][idx] = gEval(coeffs, out[i][len(secret)])
		}
	}
	return out, nil
}

// CombineVault 复刻 Vault：长度一致、x 不重复，逐字节插值回 x=0。
func CombineVault(parts [][]byte) ([]byte, error) {
	if len(parts) < 2 {
		return nil, errors.New("less than two parts")
	}
	n := len(parts[0])
	if n < 2 {
		return nil, errors.New("parts must be at least two bytes")
	}
	seen := map[uint8]bool{}
	xs := make([]uint8, len(parts))
	for i, q := range parts {
		if len(q) != n {
			return nil, errors.New("all parts must be the same length")
		}
		if seen[q[n-1]] {
			return nil, errors.New("duplicate part detected")
		}
		seen[q[n-1]] = true
		xs[i] = q[n-1]
	}
	secret := make([]byte, n-1)
	for idx := 0; idx < n-1; idx++ {
		ys := make([]uint8, len(parts))
		for i, q := range parts {
			ys[i] = q[idx]
		}
		v, err := gInterpolate(xs, ys, 0)
		if err != nil {
			return nil, err
		}
		secret[idx] = v
	}
	return secret, nil
}
