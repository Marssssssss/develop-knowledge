package main

// P-256 曲线算术（参数取自 FIPS 186-4 附录 D.1.2.3 "Curve P-256"）。
// E: y^2 = x^3 - 3x + b (mod p)，素阶 n，余因子 h = 1。

import (
	"errors"
	"math/big"
)

var (
	p256P, _ = new(big.Int).SetString("115792089210356248762697446949407573530086143415290314195533631308867097853951", 10)
	p256N, _ = new(big.Int).SetString("115792089210356248762697446949407573529996955224135760342422259061068512044369", 10)
	p256A    = new(big.Int).Sub(p256P, big.NewInt(3))
	p256B, _ = new(big.Int).SetString("5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b", 16)
	p256GX, _ = new(big.Int).SetString("6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296", 16)
	p256GY, _ = new(big.Int).SetString("4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5", 16)

	p256H        = 1
	p256FieldLen = 32
)

// Point 是仿射坐标点；nil 表示无穷远点。
type Point struct {
	X, Y *big.Int
}

// p256G 生成元。
func p256G() *Point { return &Point{p256GX, p256GY} }

func fieldMod(x *big.Int) *big.Int {
	r := new(big.Int).Mod(x, p256P)
	if r.Sign() < 0 {
		r.Add(r, p256P)
	}
	return r
}

func p256Inv(x *big.Int) *big.Int {
	e := new(big.Int).Sub(p256P, big.NewInt(2))
	return new(big.Int).Exp(fieldMod(x), e, p256P)
}

func p256IsOnCurve(pt *Point) bool {
	if pt == nil {
		return true
	}
	lhs := new(big.Int).Mul(pt.Y, pt.Y)
	x3 := new(big.Int).Exp(pt.X, big.NewInt(3), p256P)
	rhs := new(big.Int).Mul(p256A, pt.X)
	rhs.Add(rhs, x3)
	rhs.Add(rhs, p256B)
	return fieldMod(new(big.Int).Sub(lhs, rhs)).Sign() == 0
}

func p256Neg(pt *Point) *Point {
	if pt == nil {
		return nil
	}
	return &Point{pt.X, new(big.Int).Mod(new(big.Int).Neg(pt.Y), p256P)}
}

func p256Add(p1, p2 *Point) *Point {
	if p1 == nil {
		return p2
	}
	if p2 == nil {
		return p1
	}
	var lam *big.Int
	if p1.X.Cmp(p2.X) == 0 {
		if fieldMod(new(big.Int).Add(p1.Y, p2.Y)).Sign() == 0 {
			return nil
		}
		num := new(big.Int).Mul(big.NewInt(3), new(big.Int).Mul(p1.X, p1.X))
		num.Add(num, p256A)
		den := p256Inv(new(big.Int).Mul(big.NewInt(2), p1.Y))
		lam = fieldMod(new(big.Int).Mul(num, den))
	} else {
		num := new(big.Int).Sub(p2.Y, p1.Y)
		den := p256Inv(new(big.Int).Sub(p2.X, p1.X))
		lam = fieldMod(new(big.Int).Mul(num, den))
	}
	x3 := new(big.Int).Mul(lam, lam)
	x3.Sub(x3, p1.X)
	x3.Sub(x3, p2.X)
	x3 = fieldMod(x3)
	y3 := new(big.Int).Sub(p1.X, x3)
	y3.Mul(lam, y3)
	y3.Sub(y3, p1.Y)
	return &Point{x3, fieldMod(y3)}
}

func p256Mul(k *big.Int, pt *Point) *Point {
	kk := new(big.Int).Mod(k, p256N)
	if k.Sign() < 0 {
		return p256Mul(kk, p256Neg(pt))
	}
	r := (*Point)(nil)
	base := pt
	for kk.Sign() > 0 {
		if kk.Bit(0) == 1 {
			r = p256Add(r, base)
		}
		base = p256Add(base, base)
		kk.Rsh(kk, 1)
	}
	return r
}

// ------------------------------------------------------------- SEC1 编解码

func p256Encode(pt *Point) []byte {
	if pt == nil {
		panic("p256: cannot encode the identity")
	}
	out := make([]byte, 0, 1+2*p256FieldLen)
	out = append(out, 0x04)
	out = append(out, fieldMod(pt.X).FillBytes(make([]byte, p256FieldLen))...)
	out = append(out, fieldMod(pt.Y).FillBytes(make([]byte, p256FieldLen))...)
	return out
}

func p256Decode(b []byte) (*Point, error) {
	if len(b) == 0 {
		return nil, errors.New("p256: empty encoding")
	}
	switch b[0] {
	case 0x04:
		if len(b) != 1+2*p256FieldLen {
			return nil, errors.New("p256: bad uncompressed length")
		}
		pt := &Point{new(big.Int).SetBytes(b[1 : 1+p256FieldLen]),
			new(big.Int).SetBytes(b[1+p256FieldLen:])}
		if !p256IsOnCurve(pt) {
			return nil, errors.New("p256: point not on curve")
		}
		return pt, nil
	case 0x02, 0x03:
		if len(b) != 1+p256FieldLen {
			return nil, errors.New("p256: bad compressed length")
		}
		return p256Decompress(b)
	}
	return nil, errors.New("p256: unsupported prefix")
}

func p256Decompress(b []byte) (*Point, error) {
	x := new(big.Int).SetBytes(b[1:])
	if x.Cmp(p256P) >= 0 {
		return nil, errors.New("p256: x out of range")
	}
	x3 := new(big.Int).Exp(x, big.NewInt(3), p256P)
	y2 := new(big.Int).Mul(p256A, x)
	y2.Add(y2, x3)
	y2.Add(y2, p256B)
	y2 = fieldMod(y2)
	// p ≡ 3 (mod 4)
	e := new(big.Int).Add(p256P, big.NewInt(1))
	e.Rsh(e, 2)
	y := new(big.Int).Exp(y2, e, p256P)
	if new(big.Int).Mod(new(big.Int).Mul(y, y), p256P).Cmp(y2) != 0 {
		return nil, errors.New("p256: x is not the abscissa of any point")
	}
	if y.Bit(0) != uint(b[0]&1) {
		y = new(big.Int).Sub(p256P, y)
	}
	pt := &Point{x, y}
	if !p256IsOnCurve(pt) {
		return nil, errors.New("p256: decompressed point not on curve")
	}
	return pt, nil
}
