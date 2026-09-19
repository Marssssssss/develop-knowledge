package main

import (
	"math/big"
)

// ------------------------------- SM2 -------------------------------
var (
	sm2P, _ = new(big.Int).SetString("FFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF", 16)
	sm2N, _ = new(big.Int).SetString("FFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123", 16)
	sm2A    = new(big.Int).Sub(sm2P, big.NewInt(3))
	sm2B, _ = new(big.Int).SetString("28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93", 16)
	sm2GX, _ = new(big.Int).SetString("32C4AE2C1F1981195F9904466A39C9948FE30BBFF2660BE1715A4589334C74C7", 16)
	sm2GY, _ = new(big.Int).SetString("BC3736A2F4F6779C59BDCEE36B692153D0A9877CC62A474002DF32E52139F0A0", 16)
)

type point struct{ x, y *big.Int }

func ptAdd(p, q *point) *point {
	if p == nil {
		return q
	}
	if q == nil {
		return p
	}
	if p.x.Cmp(q.x) == 0 && new(big.Int).Add(p.y, q.y).Cmp(sm2P) == 0 {
		return nil
	}
	var lam *big.Int
	if p.x.Cmp(q.x) == 0 && p.y.Cmp(q.y) == 0 {
		num := new(big.Int).Mul(big.NewInt(3), new(big.Int).Mul(p.x, p.x))
		num.Add(num, sm2A)
		lam = new(big.Int).ModInverse(new(big.Int).Mul(big.NewInt(2), p.y), sm2P)
		lam.Mul(num, lam)
	} else {
		num := new(big.Int).Sub(q.y, p.y)
		lam = new(big.Int).ModInverse(new(big.Int).Sub(q.x, p.x), sm2P)
		lam.Mul(num, lam)
	}
	lam.Mod(lam, sm2P)
	x := new(big.Int).Mul(lam, lam)
	x.Sub(x, p.x).Sub(x, q.x).Mod(x, sm2P)
	y := new(big.Int).Sub(p.x, x)
	y.Mul(lam, y).Sub(y, p.y).Mod(y, sm2P)
	return &point{x, y}
}

func ptMul(k *big.Int, p *point) *point {
	var r *point
	q := &point{new(big.Int).Set(p.x), new(big.Int).Set(p.y)}
	for i := k.BitLen() - 1; i >= 0; i-- {
		r = ptAdd(r, r)
		if k.Bit(i) == 1 {
			r = ptAdd(r, q)
		}
	}
	return r
}

func sm2Z(id []byte, pub *point) []byte {
	entl := len(id) * 8
	buf := []byte{byte(entl >> 8), byte(entl & 0xFF)}
	buf = append(buf, id...)
	for _, v := range []*big.Int{sm2A, sm2B, sm2GX, sm2GY, pub.x, pub.y} {
		buf = append(buf, v.FillBytes(make([]byte, 32))...)
	}
	return sm3(buf)
}

func sm2Sign(d *big.Int, msg, k, id []byte) (*big.Int, *big.Int) {
	pub := ptMul(d, &point{sm2GX, sm2GY})
	e := new(big.Int).SetBytes(sm3(append(sm2Z(id, pub), msg...)))
	p1 := ptMul(k, &point{sm2GX, sm2GY})
	r := new(big.Int).Add(e, p1.x)
	r.Mod(r, sm2N)
	onePlusD := new(big.Int).Add(big.NewInt(1), d)
	inv := new(big.Int).ModInverse(onePlusD, sm2N)
	s := new(big.Int).Mul(r, d)
	s.Sub(k, s).Mul(s, inv).Mod(s, sm2N)
	return r, s
}

func sm2Verify(pub *point, msg, id []byte, r, s *big.Int) bool {
	if r.Sign() < 1 || r.Cmp(sm2N) >= 0 || s.Sign() < 1 || s.Cmp(sm2N) >= 0 {
		return false
	}
	e := new(big.Int).SetBytes(sm3(append(sm2Z(id, pub), msg...)))
	t := new(big.Int).Add(r, s)
	t.Mod(t, sm2N)
	if t.Sign() == 0 {
		return false
	}
	pt := ptAdd(ptMul(s, &point{sm2GX, sm2GY}), ptMul(t, pub))
	if pt == nil {
		return false
	}
	rr := new(big.Int).Add(e, pt.x)
	rr.Mod(rr, sm2N)
	return rr.Cmp(r) == 0
}

