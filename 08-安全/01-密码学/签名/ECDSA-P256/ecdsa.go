// ECDSA(P-256) + RFC 6979 确定性签名。
// 依据:
//   - 曲线参数 secp256r1: SEC2 v2 §2.4.2 (https://www.secg.org/sec2-v2.pdf)
//   - RFC 6979 §3.2 确定性 k 生成 + A.2.5 (P-256+SHA-256) 测试向量
//   - 签名/验证方程: FIPS 186 / SEC1 §4.1
// 自测 5 组: 曲线自检 / A.2.5 向量 k+r+s / 验证与篡改 / 确定性与私钥雪崩 / 20 组 round-trip。
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"fmt"
	"math/big"
	"os"
)

// ---- P-256 参数(SEC2 v2 §2.4.2) ----
var (
	P, _ = new(big.Int).SetString("FFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF", 16)
	A    = new(big.Int).Sub(P, big.NewInt(3)) // a = p-3
	B, _ = new(big.Int).SetString("5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B", 16)
	GX, _ = new(big.Int).SetString("6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296", 16)
	GY, _ = new(big.Int).SetString("4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5", 16)
	N, _ = new(big.Int).SetString("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)
)

// 仿射点(None 用 nil 表示无穷远点)
func onCurve(x, y *big.Int) bool {
	lhs := new(big.Int).Mul(y, y)
	rhs := new(big.Int).Mul(x, x)
	rhs.Mul(rhs, x)
	rhs.Add(rhs, new(big.Int).Mul(A, x))
	rhs.Add(rhs, B)
	return lhs.Sub(lhs, rhs).Mod(lhs, P).Sign() == 0
}

func addP(p1, p2 *[2]*big.Int) *[2]*big.Int {
	if p1 == nil {
		return p2
	}
	if p2 == nil {
		return p1
	}
	x1, y1, x2, y2 := p1[0], p1[1], p2[0], p2[1]
	if x1.Cmp(x2) == 0 {
		sum := new(big.Int).Add(y1, y2)
		if sum.Mod(sum, P).Sign() == 0 {
			return nil
		}
	}
	var lam *big.Int
	if x1.Cmp(x2) == 0 { // 倍点
		num := new(big.Int).Mul(x1, x1)
		num.Mul(num, big.NewInt(3))
		num.Add(num, A)
		den := new(big.Int).Add(y1, y1)
		lam = num.Mul(num, den.ModInverse(den, P))
	} else {
		num := new(big.Int).Sub(y2, y1)
		den := new(big.Int).Sub(x2, x1)
		lam = num.Mul(num, den.ModInverse(den, P))
	}
	lam.Mod(lam, P)
	x3 := new(big.Int).Mul(lam, lam)
	x3.Sub(x3, x1)
	x3.Sub(x3, x2)
	x3.Mod(x3, P)
	y3 := new(big.Int).Sub(x1, x3)
	y3.Mul(y3, lam)
	y3.Sub(y3, y1)
	y3.Mod(y3, P)
	return &[2]*big.Int{x3, y3}
}

func mulP(k *big.Int, pt *[2]*big.Int) *[2]*big.Int {
	var r *[2]*big.Int
	for i := k.BitLen() - 1; i >= 0; i-- {
		r = addP(r, r)
		if k.Bit(i) == 1 {
			r = addP(r, pt)
		}
	}
	return r
}

// ---- RFC 6979 确定性 k(HMAC_DRBG, SHA-256) ----
func bits2int(b []byte) *big.Int { // qlen=256, 输入是 SHA-256 摘要(恰 256 bit)
	return new(big.Int).SetBytes(b)
}

func rfc6979k(x *big.Int, msg []byte, cb func(k *big.Int) bool) {
	h1 := sha256.Sum256(msg)
	rolen := 32
	bx := append(leftPad(x.Bytes(), rolen), leftPad(big.NewInt(0).Mod(bits2int(h1[:]), N).Bytes(), rolen)...)
	V := bytesOf(0x01, 32)
	K := bytesOf(0x00, 32)
	mac := func(key, data []byte) []byte {
		m := hmac.New(sha256.New, key)
		m.Write(data)
		return m.Sum(nil)
	}
	K = mac(K, concat(V, []byte{0x00}, bx))
	V = mac(K, V)
	K = mac(K, concat(V, []byte{0x01}, bx))
	V = mac(K, V)
	for {
		var T []byte
		for len(T) < 32 {
			V = mac(K, V)
			T = append(T, V...)
		}
		k := new(big.Int).SetBytes(T)
		if k.Sign() > 0 && k.Cmp(N) < 0 && !cb(k) {
			return
		}
		K = mac(K, concat(V, []byte{0x00}))
		V = mac(K, V)
	}
}

func bytesOf(v byte, n int) []byte {
	b := make([]byte, n)
	for i := range b {
		b[i] = v
	}
	return b
}

func concat(bs ...[]byte) []byte {
	var out []byte
	for _, b := range bs {
		out = append(out, b...)
	}
	return out
}

func leftPad(b []byte, n int) []byte {
	if len(b) >= n {
		return b
	}
	return append(make([]byte, n-len(b)), b...)
}

// ---- 签名/验证 ----
type sigT struct{ r, s *big.Int }

func sign(x *big.Int, msg []byte) *sigT {
	e := new(big.Int).Mod(bits2int(hash(msg)), N)
	var out *sigT
	rfc6979k(x, msg, func(k *big.Int) bool {
		R := mulP(k, &[2]*big.Int{GX, GY})
		if R == nil {
			return true
		}
		r := new(big.Int).Mod(R[0], N)
		if r.Sign() == 0 {
			return true
		}
		s := new(big.Int).Mul(r, x)
		s.Add(s, e)
		s.Mul(s, new(big.Int).ModInverse(k, N))
		s.Mod(s, N)
		if s.Sign() == 0 {
			return true
		}
		out = &sigT{r, s}
		return false
	})
	return out
}

func hash(msg []byte) []byte {
	h := sha256.Sum256(msg)
	return h[:]
}

func verify(Q *[2]*big.Int, msg []byte, sig *sigT) bool {
	if sig.r.Sign() <= 0 || sig.s.Sign() <= 0 || sig.r.Cmp(N) >= 0 || sig.s.Cmp(N) >= 0 {
		return false
	}
	if !onCurve(Q[0], Q[1]) {
		return false
	}
	e := new(big.Int).Mod(bits2int(hash(msg)), N)
	w := new(big.Int).ModInverse(sig.s, N)
	u1 := new(big.Int).Mul(e, w)
	u1.Mod(u1, N)
	u2 := new(big.Int).Mul(sig.r, w)
	u2.Mod(u2, N)
	Rp := addP(mulP(u1, &[2]*big.Int{GX, GY}), mulP(u2, Q))
	if Rp == nil {
		return false
	}
	return new(big.Int).Mod(Rp[0], N).Cmp(sig.r) == 0
}

func hexI(s string) *big.Int {
	v, _ := new(big.Int).SetString(s, 16)
	return v
}

func main() {
	fail := 0
	// demo1: 曲线自检
	if !onCurve(GX, GY) || mulP(N, &[2]*big.Int{GX, GY}) != nil {
		fmt.Println("FAIL demo1 曲线自检")
		fail++
	} else {
		fmt.Println("demo1 曲线/基点/阶自检: PASS")
	}
	// demo2: RFC 6979 A.2.5
	x := hexI("C9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721")
	Q := mulP(x, &[2]*big.Int{GX, GY})
	gotK := new(big.Int)
	rfc6979k(x, []byte("sample"), func(k *big.Int) bool { gotK.Set(k); return false })
	if gotK.Cmp(hexI("A6E3C57DD01ABE90086538398355DD4C3B17AA873382B0F24D6129493D8AAD60")) != 0 {
		fmt.Printf("FAIL demo2 k(sample)=%x\n", gotK)
		fail++
	}
	sig := sign(x, []byte("sample"))
	if sig.r.Cmp(hexI("EFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716")) != 0 ||
		sig.s.Cmp(hexI("F7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8")) != 0 {
		fmt.Printf("FAIL demo2 sample 签名 r=%x s=%x\n", sig.r, sig.s)
		fail++
	}
	sigT2 := sign(x, []byte("test"))
	if sigT2.r.Cmp(hexI("F1ABB023518351CD71D881567B1EA663ED3EFCF6C5132B354F28D3B0B7D38367")) != 0 ||
		sigT2.s.Cmp(hexI("019F4113742A2B14BD25926B49C649155F267E60D3814B4C0CC84250E46F0083")) != 0 {
		fmt.Println("FAIL demo2 test 签名")
		fail++
	}
	if fail == 0 {
		fmt.Println("demo2 RFC 6979 A.2.5 向量(sample/test): PASS")
	}
	// demo3: 验证与篡改
	if !verify(Q, []byte("sample"), sig) || verify(Q, []byte("sample!"), sig) {
		fmt.Println("FAIL demo3 验证/篡改")
		fail++
	} else {
		fmt.Println("demo3 签名验证 + 篡改检测: PASS")
	}
	// demo4: 确定性与私钥雪崩
	s1, s2 := sign(x, []byte("again")), sign(x, []byte("again"))
	xBad := new(big.Int).Xor(x, big.NewInt(1))
	s3 := sign(xBad, []byte("again"))
	if s1.r.Cmp(s2.r) != 0 || s1.s.Cmp(s2.s) != 0 || s1.r.Cmp(s3.r) == 0 {
		fmt.Println("FAIL demo4 确定性/雪崩")
		fail++
	} else {
		fmt.Println("demo4 确定性 + 私钥雪崩: PASS")
	}
	// demo5: 20 组 round-trip
	for i := 0; i < 20; i++ {
		d := new(big.Int).Mod(hexI(fmt.Sprintf("%x", hash([]byte(fmt.Sprintf("key%d", i))))), N)
		Qd := mulP(d, &[2]*big.Int{GX, GY})
		m := []byte(fmt.Sprintf("message-%d", i))
		sd := sign(d, m)
		if !verify(Qd, m, sd) || verify(Qd, append(m, 'x'), sd) {
			fmt.Printf("FAIL demo5 第 %d 组\n", i)
			fail++
			break
		}
	}
	if fail == 0 {
		fmt.Println("demo5 随机 20 组 round-trip: PASS")
		fmt.Println("ALL PASS")
	} else {
		os.Exit(1)
	}
}
