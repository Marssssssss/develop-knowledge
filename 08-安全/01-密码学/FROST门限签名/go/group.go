package main

// RFC 9591 §3.1 素数阶群的一个可复算实例：Schnorr 群（p = 2q+1）。
// 与 Python 侧 frost_group.py 同参数，便于两侧对拍。

import (
	"crypto/sha512"
	"math/big"
)

var (
	qBig, _ = new(big.Int).SetString("83f515064919a3dd423569f0ad5dd69fb204575536feac18d623193999d1c1d1", 16)
	pBig, _ = new(big.Int).SetString("107ea2a0c923347ba846ad3e15abbad3f6408aeaa6dfd5831ac46327333a383a3", 16)
	gBig    = big.NewInt(4)
	one     = big.NewInt(1)
	two     = big.NewInt(2)
)

const contextString = "FROST-DEMO-SCHNORR-v1"

// Order 返回群/标量域的阶 q。
func Order() *big.Int { return new(big.Int).Set(qBig) }

// ScalarBaseMult 计算 g^s。
func ScalarBaseMult(s *big.Int) *big.Int {
	return new(big.Int).Exp(gBig, new(big.Int).Mod(s, qBig), pBig)
}

// ScalarMult 计算 a^s。
func ScalarMult(a, s *big.Int) *big.Int {
	return new(big.Int).Exp(a, new(big.Int).Mod(s, qBig), pBig)
}

// ElementAdd 在乘法群记号下是模乘。
func ElementAdd(a, b *big.Int) *big.Int {
	return new(big.Int).Mod(new(big.Int).Mul(a, b), pBig)
}

// ScalarAdd / ScalarSub / ScalarMul / ScalarDiv 都在模 q 下。
func ScalarAdd(a, b *big.Int) *big.Int {
	return new(big.Int).Mod(new(big.Int).Add(a, b), qBig)
}

func ScalarSub(a, b *big.Int) *big.Int {
	return new(big.Int).Mod(new(big.Int).Sub(a, b), qBig)
}

func ScalarMul(a, b *big.Int) *big.Int {
	return new(big.Int).Mod(new(big.Int).Mul(a, b), qBig)
}

func ScalarDiv(a, b *big.Int) *big.Int {
	inv := new(big.Int).ModInverse(b, qBig)
	return new(big.Int).Mod(new(big.Int).Mul(a, inv), qBig)
}

// SerializeElement 固定 33 字节大端（p 是 257 位）。
func SerializeElement(a *big.Int) []byte {
	b := a.Bytes()
	out := make([]byte, 33)
	copy(out[33-len(b):], b)
	return out
}

// SerializeScalar 固定 32 字节小端。
func SerializeScalar(s *big.Int) []byte {
	s = new(big.Int).Mod(s, qBig)
	b := s.Bytes()
	out := make([]byte, 32)
	for i := 0; i < len(b) && i < 32; i++ {
		out[i] = b[len(b)-1-i]
	}
	return out
}

func h(m []byte) []byte {
	d := sha512.Sum512(m)
	return d[:]
}

func toScalar(digest []byte) *big.Int {
	return new(big.Int).Mod(new(big.Int).SetBytes(digest), qBig)
}

// H1 是绑定因子 rho，H2 是挑战（RFC 9591 特意不给 H2 加域分隔），H3 是 nonce。
func H1(m []byte) *big.Int { return toScalar(h(append([]byte(contextString+"rho"), m...))) }

func H2(m []byte) *big.Int { return toScalar(h(m)) }

func H3(m []byte) *big.Int { return toScalar(h(append([]byte(contextString+"nonce"), m...))) }

// H4 压缩消息，H5 压缩承诺列表。
func H4(m []byte) []byte { return h(append([]byte(contextString+"msg"), m...)) }

func H5(m []byte) []byte { return h(append([]byte(contextString+"com"), m...)) }

// IsPrime 用 Miller-Rabin（crypto/rand 选基）验证素数，自检里复核 p 与 q。
func IsPrime(n *big.Int) bool { return n.ProbablyPrime(24) }
