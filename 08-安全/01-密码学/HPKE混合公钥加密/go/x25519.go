package main

// X25519（RFC 7748 §5）与 HKDF（RFC 5869），用 math/big 实现。

import (
	"crypto/hmac"
	"crypto/sha256"
	"math/big"
)

var (
	p25519, _ = new(big.Int).SetString("7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffed", 16)
	a24       = big.NewInt(121665)
	oneBig    = big.NewInt(1)
	twoBig    = big.NewInt(2)
)

// ClampScalar 清低 3 位、清最高位、置次高位。
func ClampScalar(k []byte) []byte {
	out := append([]byte{}, k...)
	out[0] &= 248
	out[31] &= 127
	out[31] |= 64
	return out
}

func reverse(b []byte) []byte {
	out := make([]byte, len(b))
	for i := range b {
		out[i] = b[len(b)-1-i]
	}
	return out
}

func padLeft(b []byte, n int) []byte {
	if len(b) >= n {
		return b
	}
	out := make([]byte, n)
	copy(out[n-len(b):], b)
	return out
}

// DecodeU 小端解码并屏蔽最高位（u 坐标只有 255 位有效）。
func DecodeU(u []byte) *big.Int {
	x := new(big.Int).SetBytes(reverse(u))
	mask := new(big.Int).Sub(new(big.Int).Lsh(oneBig, 255), oneBig)
	return x.And(x, mask)
}

// EncodeU 小端编码 32 字节。
func EncodeU(x *big.Int) []byte {
	mask := new(big.Int).Sub(new(big.Int).Lsh(oneBig, 255), oneBig)
	x = new(big.Int).And(x, mask)
	return reverse(padLeft(x.Bytes(), 32))
}

func mod(a *big.Int) *big.Int { return new(big.Int).Mod(a, p25519) }

func sqr(a *big.Int) *big.Int { return new(big.Int).Exp(a, twoBig, p25519) }

func cswap(swap int64, a, b *big.Int) (*big.Int, *big.Int) {
	dummy := mod(new(big.Int).Mul(big.NewInt(swap), mod(new(big.Int).Sub(a, b))))
	return mod(new(big.Int).Sub(a, dummy)), mod(new(big.Int).Add(b, dummy))
}

// X25519 是 Montgomery 阶梯，逐位（254 → 0）做差分加与倍点。
func X25519(k, u []byte) []byte {
	k = ClampScalar(k)
	x1 := DecodeU(u)
	x2, z2 := big.NewInt(1), big.NewInt(0)
	x3, z3 := new(big.Int).Set(x1), big.NewInt(1)
	var swap int64
	for t := 254; t >= 0; t-- {
		kt := int64(k[t/8]>>(t%8)) & 1
		swap ^= kt
		x2, x3 = cswap(swap, x2, x3)
		z2, z3 = cswap(swap, z2, z3)
		swap = kt

		aa := sqr(mod(new(big.Int).Add(x2, z2)))
		bval := mod(new(big.Int).Sub(x2, z2))
		bb := sqr(bval)
		e := mod(new(big.Int).Sub(aa, bb))
		cc := mod(new(big.Int).Add(x3, z3))
		d := mod(new(big.Int).Sub(x3, z3))
		da := mod(new(big.Int).Mul(d, mod(new(big.Int).Add(x2, z2))))
		cb := mod(new(big.Int).Mul(cc, bval))

		x3 = sqr(mod(new(big.Int).Add(da, cb)))
		z3 = mod(new(big.Int).Mul(x1, sqr(mod(new(big.Int).Sub(da, cb)))))
		x2 = mod(new(big.Int).Mul(aa, bb))
		z2 = mod(new(big.Int).Mul(e, mod(new(big.Int).Add(aa, mod(new(big.Int).Mul(a24, e))))))
		_ = d
	}
	x2, x3 = cswap(swap, x2, x3)
	z2, z3 = cswap(swap, z2, z3)
	_, _ = x3, z3
	zinv := new(big.Int).Exp(z2, new(big.Int).Sub(p25519, twoBig), p25519)
	return EncodeU(mod(new(big.Int).Mul(x2, zinv)))
}

// X25519Base 是基点 u = 9。
func X25519Base(k []byte) []byte { return X25519(k, EncodeU(big.NewInt(9))) }

// HKDFExtract / HKDFExpand 是 RFC 5869 的 Extract 与 Expand。
func HKDFExtract(salt, ikm []byte) []byte {
	m := hmac.New(sha256.New, salt)
	m.Write(ikm)
	return m.Sum(nil)
}

func HKDFExpand(prk, info []byte, length int) []byte {
	out := []byte{}
	t := []byte{}
	for i := 1; len(out) < length; i++ {
		m := hmac.New(sha256.New, prk)
		m.Write(t)
		m.Write(info)
		m.Write([]byte{byte(i)})
		t = m.Sum(nil)
		out = append(out, t...)
	}
	return out[:length]
}
