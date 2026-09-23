// 素数域 GF(p) 上的 Shamir 秘密分享（对应 python/prime.py）。

package main

import (
	"errors"
	"math/rand"
)

const P int64 = 1<<31 - 1 // 2147483647，梅森素数；(P-1)^2 仍在 int64 内

func modPow(a, e int64) int64 {
	r := int64(1)
	a %= P
	for e > 0 {
		if e&1 == 1 {
			r = r * a % P
		}
		a = a * a % P
		e >>= 1
	}
	return r
}

// modInv 用费马小定理：a^(p-2) ≡ a^-1 (mod p)。
func modInv(a int64) int64 { return modPow(a, P-2) }

func makePoly(secret int64, degree int, rnd *rand.Rand) []int64 {
	c := []int64{secret % P}
	for i := 0; i < degree; i++ {
		c = append(c, rnd.Int63n(P-1)+1) // 非常数项系数非零
	}
	return c
}

func evalPoly(c []int64, x int64) int64 {
	out := int64(0)
	for i := len(c) - 1; i >= 0; i-- {
		out = (out*x + c[i]) % P
	}
	return out
}

func splitPrime(secret int64, n, t int, rnd *rand.Rand) ([][2]int64, error) {
	if t < 2 || t > n {
		return nil, errors.New("require 2 <= t <= n")
	}
	c := makePoly(secret, t-1, rnd)
	out := make([][2]int64, n)
	for i := 0; i < n; i++ {
		x := int64(i + 1) // x 从 1 起，x=0 就是秘密本身
		out[i] = [2]int64{x, evalPoly(c, x)}
	}
	return out, nil
}

func lagrangeBasis(shares [][2]int64, i int) int64 {
	num, den := int64(1), int64(1)
	for j := range shares {
		if j == i {
			continue
		}
		num = num * (0 - shares[j][0]) % P
		den = den * (shares[i][0] - shares[j][0]) % P
	}
	if num < 0 {
		num += P
	}
	if den < 0 {
		den += P
	}
	return num * modInv(den) % P
}

func recoverPrime(shares [][2]int64) int64 {
	total := int64(0)
	for i := range shares {
		total = (total + shares[i][1]*lagrangeBasis(shares, i)) % P
	}
	return total
}
