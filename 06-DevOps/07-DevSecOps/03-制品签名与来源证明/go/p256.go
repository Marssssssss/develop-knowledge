// Package sigstore 的 P-256 部分，是 python/p256.py 的 Go 转写：
// 纯 Go 标准库实现的 NIST P-256 ECDSA（签名用 RFC 6979 确定性 nonce）。
//
// 存在的唯一目的是把 DSSE protocol.md 的官方测试向量逐字节复现出来。
package sigstore

import (
	"crypto/hmac"
	"crypto/sha256"
	"math/big"
)

// P-256 参数（NIST FIPS 186-4）。
var (
	p256P, _ = new(big.Int).SetString("ffffffff00000001000000000000000000000000ffffffffffffffffffffffff", 16)
	p256A    = new(big.Int).Sub(p256P, big.NewInt(3))
	p256B, _ = new(big.Int).SetString("5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b", 16)
	p256N, _ = new(big.Int).SetString("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16)
	gx, _    = new(big.Int).SetString("6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296", 16)
	gy, _    = new(big.Int).SetString("4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5", 16)
)

// Point 是仿射坐标下的椭圆曲线点；nil 表示无穷远点。
type Point struct {
	X, Y *big.Int
}

// G 是 P-256 的基点。
func G() Point { return Point{new(big.Int).Set(gx), new(big.Int).Set(gy)} }

// N 返回群的阶。
func N() *big.Int { return new(big.Int).Set(p256N) }

// IsOnCurve 校验点是否满足曲线方程。
func IsOnCurve(pt Point) bool {
	if pt.X == nil || pt.Y == nil {
		return false
	}
	lhs := new(big.Int).Exp(pt.Y, big.NewInt(2), p256P)
	x3 := new(big.Int).Exp(pt.X, big.NewInt(3), p256P)
	ax := new(big.Int).Mul(p256A, pt.X)
	rhs := new(big.Int).Add(new(big.Int).Add(x3, ax), p256B)
	rhs.Mod(rhs, p256P)
	return lhs.Cmp(rhs) == 0
}

// Add 是点加法（含倍点）。
func Add(p1, p2 Point) Point {
	if p1.X == nil {
		return p2
	}
	if p2.X == nil {
		return p1
	}
	if p1.X.Cmp(p2.X) == 0 {
		sum := new(big.Int).Add(p1.Y, p2.Y)
		sum.Mod(sum, p256P)
		if sum.Sign() == 0 {
			return Point{}
		}
	}
	var lam *big.Int
	if p1.X.Cmp(p2.X) == 0 && p1.Y.Cmp(p2.Y) == 0 {
		num := new(big.Int).Mul(big.NewInt(3), new(big.Int).Exp(p1.X, big.NewInt(2), p256P))
		num.Add(num, p256A)
		num.Mod(num, p256P)
		den := new(big.Int).Mul(big.NewInt(2), p1.Y)
		den.Mod(den, p256P)
		lam = new(big.Int).Exp(den, new(big.Int).Sub(p256P, big.NewInt(2)), p256P)
		lam.Mul(num, lam)
	} else {
		num := new(big.Int).Sub(p2.Y, p1.Y)
		num.Mod(num, p256P)
		den := new(big.Int).Sub(p2.X, p1.X)
		den.Mod(den, p256P)
		denInv := new(big.Int).Exp(den, new(big.Int).Sub(p256P, big.NewInt(2)), p256P)
		lam = new(big.Int).Mul(num, denInv)
	}
	lam.Mod(lam, p256P)
	x3 := new(big.Int).Exp(lam, big.NewInt(2), p256P)
	x3.Sub(x3, p1.X)
	x3.Sub(x3, p2.X)
	x3.Mod(x3, p256P)
	y3 := new(big.Int).Sub(p1.X, x3)
	y3.Mul(lam, y3)
	y3.Sub(y3, p1.Y)
	y3.Mod(y3, p256P)
	return Point{x3, y3}
}

// Mul 是标量乘（二进制展开）。
func Mul(pt Point, k *big.Int) Point {
	kk := new(big.Int).Mod(k, p256N)
	var result Point
	addend := pt
	for kk.Sign() > 0 {
		if kk.Bit(0) == 1 {
			result = Add(result, addend)
		}
		addend = Add(addend, addend)
		kk.Rsh(kk, 1)
	}
	return result
}

// ---------------------------------------------------------------- RFC 6979

func bits2int(b []byte) *big.Int {
	x := new(big.Int).SetBytes(b)
	blen := len(b) * 8
	if blen > p256N.BitLen() {
		x.Rsh(x, uint(blen-p256N.BitLen()))
	}
	return x
}

func int2octets(x *big.Int) []byte {
	out := make([]byte, 32)
	x.FillBytes(out)
	return out
}

func bits2octets(b []byte) []byte {
	z1 := bits2int(b)
	if z1.Cmp(p256N) >= 0 {
		z1.Sub(z1, p256N)
	}
	return int2octets(z1)
}

func hmacSHA256(k, m []byte) []byte {
	mac := hmac.New(sha256.New, k)
	mac.Write(m)
	return mac.Sum(nil)
}

func concat(parts ...[]byte) []byte {
	var out []byte
	for _, p := range parts {
		out = append(out, p...)
	}
	return out
}

// rfc6979Nonce 是 RFC 6979 的确定性 nonce 生成（HMAC-SHA256 版）。
func rfc6979Nonce(d *big.Int, h1 []byte) *big.Int {
	h := sha256.New().Size()
	v := make([]byte, h)
	for i := range v {
		v[i] = 0x01
	}
	k := make([]byte, h)
	priv := int2octets(d)
	hb := bits2octets(h1)
	k = hmacSHA256(k, concat(v, []byte{0x00}, priv, hb))
	v = hmacSHA256(k, v)
	k = hmacSHA256(k, concat(v, []byte{0x01}, priv, hb))
	v = hmacSHA256(k, v)
	for {
		var t []byte
		for len(t)*8 < p256N.BitLen() {
			v = hmacSHA256(k, v)
			t = append(t, v...)
		}
		cand := bits2int(t)
		if cand.Sign() > 0 && cand.Cmp(p256N) < 0 {
			pt := Mul(G(), cand)
			if pt.X != nil {
				r := new(big.Int).Mod(pt.X, p256N)
				if r.Sign() != 0 {
					return cand
				}
			}
		}
		k = hmacSHA256(k, concat(v, []byte{0x00}))
		v = hmacSHA256(k, v)
	}
}

// Sign 返回 (r, s)；签名编码是 r||s 的原始拼接，不是 ASN.1 DER。
func Sign(d *big.Int, msg []byte) (*big.Int, *big.Int) {
	sum := sha256.Sum256(msg)
	h := sum[:]
	e := bits2int(h)
	for {
		k := rfc6979Nonce(d, h)
		pt := Mul(G(), k)
		r := new(big.Int).Mod(pt.X, p256N)
		if r.Sign() == 0 {
			continue
		}
		kinv := new(big.Int).Exp(k, new(big.Int).Sub(p256N, big.NewInt(2)), p256N)
		rd := new(big.Int).Mul(r, d)
		s := new(big.Int).Add(e, rd)
		s.Mul(kinv, s)
		s.Mod(s, p256N)
		if s.Sign() == 0 {
			continue
		}
		return r, s
	}
}

// Verify 校验 (r, s) 是否是 pub 对 msg 的有效签名。
func Verify(pub Point, msg []byte, r, s *big.Int) bool {
	if r.Sign() < 1 || r.Cmp(p256N) >= 0 || s.Sign() < 1 || s.Cmp(p256N) >= 0 {
		return false
	}
	if !IsOnCurve(pub) {
		return false
	}
	if Mul(pub, p256N).X != nil {
		return false
	}
	sum := sha256.Sum256(msg)
	e := new(big.Int).SetBytes(sum[:])
	w := new(big.Int).Exp(s, new(big.Int).Sub(p256N, big.NewInt(2)), p256N)
	u1 := new(big.Int).Mul(e, w)
	u1.Mod(u1, p256N)
	u2 := new(big.Int).Mul(r, w)
	u2.Mod(u2, p256N)
	pt := Add(Mul(G(), u1), Mul(pub, u2))
	if pt.X == nil {
		return false
	}
	return new(big.Int).Mod(pt.X, p256N).Cmp(r) == 0
}
