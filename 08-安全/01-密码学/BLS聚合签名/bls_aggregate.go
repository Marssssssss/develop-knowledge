// BLS 聚合签名的 Go 实现：与 Python 版同一条玩具超奇异曲线（嵌入次数 2），
// 走真实的 Miller 循环 + 最终幂，因此双线性、非退化、阶 r 这三条性质都实测成立。
//
// E: y^2 = x^3 + x over F_p, p = 16252507 ≡ 3 (mod 4) ⇒ 超奇异, #E = p+1 = 4·r
package main

import (
	"crypto/sha256"
	"fmt"
	"math/big"
)

var (
	pF    = big.NewInt(16252507)
	rN    = big.NewInt(4063127)
	hCof  = big.NewInt(4)
	final = new(big.Int).Mul(new(big.Int).Sub(pF, big.NewInt(1)), hCof) // (p-1)·h
	one2  = f2{big.NewInt(1), big.NewInt(0)}
)

type f2 struct{ a, b *big.Int } // F_p^2 = F_p[i]/(i^2+1)

func f2new(a, b int64) f2 { return f2{big.NewInt(a), big.NewInt(b)} }

func (x f2) mul(y f2) f2 {
	return f2{new(big.Int).Mod(new(big.Int).Sub(
		new(big.Int).Mul(x.a, y.a), new(big.Int).Mul(x.b, y.b)), pF),
		new(big.Int).Mod(new(big.Int).Add(
			new(big.Int).Mul(x.a, y.b), new(big.Int).Mul(x.b, y.a)), pF)}
}

func (x f2) inv() f2 {
	n := new(big.Int).ModInverse(new(big.Int).Mod(new(big.Int).Add(
		new(big.Int).Mul(x.a, x.a), new(big.Int).Mul(x.b, x.b)), pF), pF)
	return f2{new(big.Int).Mod(new(big.Int).Mul(x.a, n), pF),
		new(big.Int).Mod(new(big.Int).Neg(new(big.Int).Mul(x.b, n)), pF)}
}

func (x f2) eq(y f2) bool { return x.a.Cmp(y.a) == 0 && x.b.Cmp(y.b) == 0 }

func f2pow(x f2, e *big.Int) f2 {
	out, base := one2, x
	for i := e.BitLen() - 1; i >= 0; i-- {
		out = out.mul(out)
		if e.Bit(i) == 1 {
			out = out.mul(base)
		}
	}
	return out
}

// ------------------------------ E(F_p) ------------------------------
type pt struct{ x, y *big.Int }

func invp(a *big.Int) *big.Int { return new(big.Int).ModInverse(a, pF) }

func add(A, B *pt) *pt {
	if A == nil {
		return B
	}
	if B == nil {
		return A
	}
	if A.x.Cmp(B.x) == 0 && new(big.Int).Mod(new(big.Int).Add(A.y, B.y), pF).Sign() == 0 {
		return nil
	}
	var lam *big.Int
	if A.x.Cmp(B.x) == 0 && A.y.Cmp(B.y) == 0 {
		lam = new(big.Int).Mul(big.NewInt(3), new(big.Int).Mul(A.x, A.x))
		lam.Add(lam, big.NewInt(1)).Mul(lam, invp(new(big.Int).Mul(big.NewInt(2), A.y)))
	} else {
		lam = new(big.Int).Sub(B.y, A.y)
		lam.Mul(lam, invp(new(big.Int).Sub(B.x, A.x)))
	}
	lam.Mod(lam, pF)
	x := new(big.Int).Mul(lam, lam)
	x.Sub(x, A.x).Sub(x, B.x).Mod(x, pF)
	y := new(big.Int).Sub(A.x, x)
	y.Mul(lam, y).Sub(y, A.y).Mod(y, pF)
	return &pt{x, y}
}

func mul(k *big.Int, A *pt) *pt {
	kk := new(big.Int).Mod(k, rN)
	var T *pt
	Q := &pt{new(big.Int).Set(A.x), new(big.Int).Set(A.y)}
	for i := kk.BitLen() - 1; i >= 0; i-- {
		T = add(T, T)
		if kk.Bit(i) == 1 {
			T = add(T, Q)
		}
	}
	return T
}

func neg(A *pt) *pt {
	return &pt{new(big.Int).Set(A.x), new(big.Int).Mod(new(big.Int).Neg(A.y), pF)}
}

// ------------------------------ 配对 ------------------------------
func psi(A *pt) (f2, f2) { // 畸变映射 ψ(x,y) = (-x, i·y)
	return f2{new(big.Int).Mod(new(big.Int).Neg(A.x), pF), big.NewInt(0)},
		f2{big.NewInt(0), new(big.Int).Set(A.y)}
}

func miller(A *pt, QX, QY f2) f2 {
	T, f := A, one2
	bits := rN.Text(2)
	for i := 1; i < len(bits); i++ {
		if T == nil {
			break
		}
		if T.y.Sign() == 0 { // 2-挠点，切线退化
			T = add(T, T)
			continue
		}
		lam := new(big.Int).Mul(big.NewInt(3), new(big.Int).Mul(T.x, T.x))
		lam.Add(lam, big.NewInt(1)).Mul(lam, invp(new(big.Int).Mul(big.NewInt(2), T.y))).Mod(lam, pF)
		dx := new(big.Int).Sub(QX.a, T.x)
		l := f2{new(big.Int).Mod(new(big.Int).Sub(new(big.Int).Sub(QY.a, T.y),
			new(big.Int).Mul(lam, dx)), pF),
			new(big.Int).Mod(new(big.Int).Sub(QY.b, new(big.Int).Mul(lam, QX.b)), pF)}
		T2 := add(T, T)
		v := one2
		if T2 != nil {
			v = f2{new(big.Int).Mod(new(big.Int).Sub(QX.a, T2.x), pF), QX.b}
		}
		f = f.mul(f).mul(l).mul(v.inv())
		T = T2
		if bits[i] == '1' {
			TP := add(T, A)
			if TP == nil { // T = -A：只剩垂直线
				l = f2{new(big.Int).Mod(new(big.Int).Sub(QX.a, A.x), pF), QX.b}
				v = one2
			} else {
				var lm *big.Int
				if T != nil && T.x.Cmp(A.x) == 0 && T.y.Cmp(A.y) == 0 {
					lm = new(big.Int).Mul(big.NewInt(3), new(big.Int).Mul(T.x, T.x))
					lm.Add(lm, big.NewInt(1)).Mul(lm, invp(new(big.Int).Mul(big.NewInt(2), T.y)))
				} else {
					lm = new(big.Int).Sub(A.y, T.y)
					lm.Mul(lm, invp(new(big.Int).Sub(A.x, T.x)))
				}
				lm.Mod(lm, pF)
				dx := new(big.Int).Sub(QX.a, T.x)
				l = f2{new(big.Int).Mod(new(big.Int).Sub(new(big.Int).Sub(QY.a, T.y),
					new(big.Int).Mul(lm, dx)), pF),
					new(big.Int).Mod(new(big.Int).Sub(QY.b, new(big.Int).Mul(lm, QX.b)), pF)}
				v = f2{new(big.Int).Mod(new(big.Int).Sub(QX.a, TP.x), pF), QX.b}
			}
			f = f.mul(l).mul(v.inv())
			T = TP
		}
	}
	return f
}

func pairing(A, B *pt) f2 {
	qx, qy := psi(B)
	return f2pow(miller(A, qx, qy), final)
}

// -------------------------- hash-to-point --------------------------
func h2p(msg []byte) *pt {
	for ctr := 0; ; ctr++ {
		h := sha256.Sum256(append(append([]byte{}, msg...), []byte(fmt.Sprintf("|%d", ctr))...))
		x := new(big.Int).SetBytes(h[:])
		x.Mod(x, pF)
		v := new(big.Int).Mul(x, new(big.Int).Mul(x, x))
		v.Add(v, x).Mod(v, pF)
		if new(big.Int).Exp(v, new(big.Int).Rsh(new(big.Int).Sub(pF, big.NewInt(1)), 1), pF).Cmp(big.NewInt(1)) == 0 {
			y := new(big.Int).Exp(v, new(big.Int).Rsh(new(big.Int).Add(pF, big.NewInt(1)), 2), pF)
			return mul(hCof, &pt{x, y}) // 乘共因子清到 r 阶子群
		}
	}
}

var G = func() *pt {
	for x := int64(1); x < 500; x++ {
		xx := big.NewInt(x)
		v := new(big.Int).Mul(xx, new(big.Int).Mul(xx, xx))
		v.Add(v, xx).Mod(v, pF)
		if new(big.Int).Exp(v, new(big.Int).Rsh(new(big.Int).Sub(pF, big.NewInt(1)), 1), pF).Cmp(big.NewInt(1)) == 0 {
			y := new(big.Int).Exp(v, new(big.Int).Rsh(new(big.Int).Add(pF, big.NewInt(1)), 2), pF)
			cand := mul(hCof, &pt{xx, y})
			if cand != nil && mul(rN, cand) == nil && mul(new(big.Int).Rsh(rN, 1), cand) != nil {
				return cand
			}
		}
	}
	panic("未找到生成元")
}()

// ------------------------------ BLS ------------------------------
func keygen(sk *big.Int) *pt { return mul(sk, G) }

func coreSign(sk *big.Int, msg []byte) *pt { return mul(sk, h2p(msg)) }

func coreVerify(pk *pt, msg []byte, sig *pt) bool {
	if pk == nil || sig == nil {
		return false
	}
	return pairing(h2p(msg), pk).eq(pairing(sig, G))
}

func aggregate(sigs []*pt) *pt {
	var out *pt
	for _, s := range sigs {
		out = add(out, s)
	}
	return out
}

func coreAggVerify(pks []*pt, msgs [][]byte, sig *pt) bool {
	rhs := one2
	for i := range pks {
		rhs = rhs.mul(pairing(h2p(msgs[i]), pks[i]))
	}
	return pairing(sig, G).eq(rhs)
}

func aggVerify(pks []*pt, msgs [][]byte, sig *pt) bool {
	seen := map[string]bool{}
	for _, m := range msgs {
		if seen[string(m)] {
			return false // Basic scheme：消息必须互不相同
		}
		seen[string(m)] = true
	}
	return coreAggVerify(pks, msgs, sig)
}

func main() {
	ok := 0
	chk := func(cond bool, msg string) {
		if !cond {
			panic("断言失败: " + msg)
		}
		ok++
	}

	e0 := pairing(G, G)
	chk(!e0.eq(one2), "e(G,G) ≠ 1（非退化）")
	chk(f2pow(e0, rN).eq(one2), "e(G,G)^r = 1（落到 μ_r）")
	a, b := big.NewInt(12345), big.NewInt(6789)
	chk(pairing(mul(a, G), mul(b, G)).eq(f2pow(e0, new(big.Int).Mul(a, b))),
		"双线性：e(aG,bG) = e(G,G)^{ab}")
	chk(pairing(add(mul(big.NewInt(11), G), mul(big.NewInt(22), G)), G).eq(
		pairing(mul(big.NewInt(11), G), G).mul(pairing(mul(big.NewInt(22), G), G))),
		"第一自变量可加")
	chk(pairing(G, neg(G)).eq(e0.inv()), "e(G,-G) = e(G,G)^{-1}")

	hp := h2p([]byte("hello"))
	chk(mul(rN, hp) == nil, "hash_to_point 落在 r 阶子群")
	chk(hp.x.Cmp(h2p([]byte("hello")).x) == 0, "hash_to_point 确定性")

	sk := big.NewInt(0x2A3B4C)
	pk := keygen(sk)
	sig := coreSign(sk, []byte("m1"))
	chk(coreVerify(pk, []byte("m1"), sig), "CoreVerify 通过")
	chk(!coreVerify(pk, []byte("m2"), sig), "换消息 ⇒ 失败")
	chk(!coreVerify(mul(big.NewInt(2), pk), []byte("m1"), sig), "换公钥 ⇒ 失败")

	sks := []*big.Int{big.NewInt(0x1111), big.NewInt(0x2222), big.NewInt(0x3333)}
	pks := make([]*pt, 3)
	sigs := make([]*pt, 3)
	msgs := [][]byte{[]byte("tx-1"), []byte("tx-2"), []byte("tx-3")}
	for i := range sks {
		pks[i] = keygen(sks[i])
		sigs[i] = coreSign(sks[i], msgs[i])
	}
	agg := aggregate(sigs)
	chk(coreAggVerify(pks, msgs, agg), "聚合签名一次验全通过")
	chk(aggVerify(pks, msgs, agg), "Basic scheme 的 AggregateVerify 通过")
	chk(!coreAggVerify(pks, msgs, add(agg, G)), "聚合签名被改 ⇒ 失败")
	chk(!aggVerify(pks[:2], [][]byte{[]byte("same"), []byte("same")}, aggregate(sigs[:2])),
		"消息重复 ⇒ Basic scheme 拒绝")

	// Rogue-key 攻击
	skV, skA := big.NewInt(0x7777), big.NewInt(0x8888)
	pkV, pkA := keygen(skV), keygen(skA)
	pkRogue := add(pkA, neg(pkV))
	m := []byte("evil")
	forged := mul(skA, h2p(m))
	chk(aggVerify([]*pt{pkV, pkRogue}, [][]byte{m, m}, forged) == false,
		"强制消息互异 ⇒ 伪造被拒")
	chk(coreAggVerify([]*pt{pkV, pkRogue}, [][]byte{m, m}, forged) == true,
		"去掉互异检查 ⇒ rogue-key 伪造通过")
	chk(add(pkV, pkRogue).x.Cmp(pkA.x) == 0, "rogue 公钥 + 受害者公钥 = 攻击者公钥")

	fmt.Printf("BLS 聚合签名 Go 自检通过：%d 项断言\n", ok)
}
