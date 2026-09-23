// SLIP-0039 的 SplitSecret / RecoverSecret 与 RS1024 校验和（对应 python/slip39.py）。

package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"errors"
	"math/rand"
)

const (
	secretIndex uint8 = 255 // 秘密是这条多项式在 x=255 处的取值
	digestIndex uint8 = 254 // 摘要是 x=254 处的取值
	maxShares         = 16
)

type share struct {
	X uint8
	Y []byte
}

func slipDigest(secret []byte, rnd *rand.Rand) []byte {
	r := make([]byte, len(secret)-4)
	rnd.Read(r)
	m := hmac.New(sha256.New, r)
	m.Write(secret)
	return append(m.Sum(nil)[:4], r...)
}

func slipInterp(x uint8, pts []share) []byte {
	out := make([]byte, len(pts[0].Y))
	for i := range pts {
		scalar := uint8(1)
		for j := range pts {
			if i == j {
				continue
			}
			t, _ := gDiv(gAdd(x, pts[j].X), gAdd(pts[i].X, pts[j].X))
			scalar = gMul(scalar, t)
		}
		for k := range out {
			out[k] ^= gMul(pts[i].Y[k], scalar)
		}
	}
	return out
}

// SplitSLIP39 复刻 SLIP-0039 §SplitSecret。
func SplitSLIP39(t, n int, secret []byte, rnd *rand.Rand) ([]share, error) {
	if t < 1 || t > n || n > maxShares {
		return nil, errors.New("require 0 < T <= N <= 16")
	}
	if len(secret)*8 < 128 || (len(secret)*8)%16 != 0 {
		return nil, errors.New("secret must be >=128 bits and multiple of 16 bits")
	}
	out := make([]share, n)
	if t == 1 {
		for i := range out {
			out[i] = share{uint8(i), append([]byte(nil), secret...)}
		}
		return out, nil
	}
	base := make([]share, 0, t)
	base = append(base, share{digestIndex, slipDigest(secret, rnd)})
	base = append(base, share{secretIndex, append([]byte(nil), secret...)})
	for k := 0; k < t-2; k++ {
		y := make([]byte, len(secret))
		rnd.Read(y)
		base = append(base, share{uint8(k), y}) // (0,y1) … (T-3,y_{T-2})
	}
	for i := 1; i <= n; i++ {
		if i <= t-2 {
			out[i-1] = base[i+1] // 前 T-2 份本就是随机值
		} else {
			out[i-1] = share{uint8(i - 1), slipInterp(uint8(i-1), base)}
		}
	}
	return out, nil
}

// RecoverSLIP39 复刻 §RecoverSecret：插值出 S 与 D 后验 HMAC。
func RecoverSLIP39(t int, shares []share) ([]byte, error) {
	if len(shares) < t {
		return nil, errors.New("not enough shares")
	}
	if t == 1 {
		return shares[0].Y, nil
	}
	s := slipInterp(secretIndex, shares)
	d := slipInterp(digestIndex, shares)
	m := hmac.New(sha256.New, d[4:])
	m.Write(s)
	if !hmac.Equal(m.Sum(nil)[:4], d[:4]) {
		return nil, errors.New("digest mismatch")
	}
	return s, nil
}

// --------------------------- RS1024：GF(1024) 上的 3 字 Reed-Solomon 校验和
// 生成多项式 (x-a)(x-a^2)(x-a^3)，a 是本原多项式 x^10+x^3+1 的根。
var gen = [10]int{0xE0E040, 0x1C1C080, 0x3838100, 0x7070200, 0xE0E0009,
	0x1C0C2412, 0x38086C24, 0x3090FC48, 0x21B1F890, 0x3F3F120}

func rs1024Values(cs string, data []int) []int {
	values := make([]int, 0, len(cs)+len(data))
	for _, c := range cs {
		values = append(values, int(c))
	}
	return append(values, data...)
}

func rs1024Polymod(values []int) int {
	chk := 1
	for _, v := range values {
		b := chk >> 20
		chk = (chk&0xFFFFF)<<10 ^ v
		for i := 0; i < 10; i++ {
			if (b>>i)&1 == 1 {
				chk ^= gen[i]
			}
		}
	}
	return chk
}

func rs1024Checksum(cs string, data []int) []int {
	values := append(rs1024Values(cs, data), 0, 0, 0)
	pm := rs1024Polymod(values) ^ 1
	out := make([]int, 3)
	for i := 0; i < 3; i++ {
		out[i] = (pm >> (10 * (2 - i))) & 1023
	}
	return out
}

func rs1024Verify(cs string, data []int) bool {
	return rs1024Polymod(rs1024Values(cs, data)) == 1
}
