package main

import (
	"fmt"
	"math/big"
)

var total, failed int

func check(label string, cond bool, detail string) {
	total++
	if !cond {
		failed++
		fmt.Printf("  FAIL %s: %s\n", label, detail)
	}
}

func fixedH(i int) []byte {
	b := make([]byte, 32)
	for k := range b {
		b[k] = byte(0xA0 + i)
	}
	return b
}

func fixedB(i int) []byte {
	b := make([]byte, 32)
	for k := range b {
		b[k] = byte(0xB0 + i)
	}
	return b
}

func nonceGenerate(secret *big.Int, rand []byte) *big.Int {
	return H3(append(append([]byte{}, rand...), SerializeScalar(secret)...))
}

func setup(n, t int, s int64) ([][2]*big.Int, *big.Int, map[int64]*big.Int) {
	coeffs := make([]*big.Int, 0, t-1)
	for j := 0; j < t-1; j++ {
		coeffs = append(coeffs, big.NewInt(int64(0xAA+j)))
	}
	shares, _ := SecretShareShard(big.NewInt(s), coeffs, n)
	pk := ScalarBaseMult(big.NewInt(s))
	pkI := map[int64]*big.Int{}
	for _, sh := range shares {
		pkI[sh[0].Int64()] = ScalarBaseMult(sh[1])
	}
	return shares, pk, pkI
}

func testGroup() {
	check("q 通过素性测试", IsPrime(qBig), "")
	check("p 通过素性测试", IsPrime(pBig), "")
	twoQ := new(big.Int).Mul(two, qBig)
	twoQ.Add(twoQ, one)
	check("p == 2q+1", twoQ.Cmp(pBig) == 0, "")
	check("g 的阶为 q", new(big.Int).Exp(gBig, qBig, pBig).Cmp(one) == 0, "")
	check("g 不是单位元", gBig.Cmp(one) != 0, "")
	check("标量除法是真除法",
		ScalarMul(ScalarDiv(big.NewInt(7), big.NewInt(3)), big.NewInt(3)).Cmp(big.NewInt(7)) == 0, "")
}

func testShamir() {
	s := big.NewInt(0xC0FFEE)
	coeffs := []*big.Int{big.NewInt(0x1111), big.NewInt(0x2222)}
	shares, full := SecretShareShard(s, coeffs, 5)
	check("分成 5 份", len(shares) == 5, "")
	check("多项式常数项即秘密", full[0].Cmp(s) == 0, "")
	got, err := SecretShareCombine(shares[:3], 3)
	check("任取 3 份能还原", err == nil && got.Cmp(s) == 0, "")
	got2, err2 := SecretShareCombine(shares[:2], 2)
	check("只给 2 份还原不出 s", err2 == nil && got2.Cmp(s) != 0, "")
	xs := []*big.Int{big.NewInt(1), big.NewInt(2), big.NewInt(3)}
	sum := big.NewInt(0)
	for _, x := range xs {
		lam, err := DeriveInterpolatingValue(xs, x)
		if err != nil {
			check("拉格朗日系数可求", false, err.Error())
			continue
		}
		sum.Add(sum, lam)
	}
	check("三个拉格朗日系数之和为 1", new(big.Int).Mod(sum, Order()).Cmp(one) == 0, "")
	_, e1 := DeriveInterpolatingValue(xs, big.NewInt(4))
	check("x_i 不在 L 中时报错", e1 != nil, "")
	_, e2 := DeriveInterpolatingValue([]*big.Int{big.NewInt(1), big.NewInt(2), big.NewInt(2)}, big.NewInt(1))
	check("L 有重复时报错", e2 != nil, "")
}

func testFrost() {
	shares, pk, _ := setup(5, 3, 0x1234)
	sk := map[int64]*big.Int{}
	for _, sh := range shares {
		sk[sh[0].Int64()] = sh[1]
	}
	msg := []byte("round two")
	ids := []int64{1, 2, 3}
	hiding := map[int64]*big.Int{}
	binding := map[int64]*big.Int{}
	cl := make([]Commitment, 0, 3)
	for k, id := range ids {
		d := nonceGenerate(sk[id], fixedH(k))
		e := nonceGenerate(sk[id], fixedB(k))
		hiding[id], binding[id] = d, e
		cl = append(cl, Commitment{big.NewInt(id), ScalarBaseMult(d), ScalarBaseMult(e)})
	}
	zs := make([]*big.Int, 0, 3)
	for _, id := range ids {
		zs = append(zs, Sign(big.NewInt(id), sk[id], pk, hiding[id], binding[id], msg, cl))
	}
	r, z := Aggregate(pk, cl, msg, zs)
	check("聚合签名通过 Schnorr 验签", SchnorrVerify(r, z, pk, msg), "")
	check("换消息则验签失败", !SchnorrVerify(r, z, pk, []byte("other")), "")

	bad := make([]*big.Int, len(zs))
	copy(bad, zs)
	bad[1].Add(bad[1], one)
	bad[1].Mod(bad[1], Order())
	_, zb := Aggregate(pk, cl, msg, bad)
	check("篡改份额后聚合签名无效", !SchnorrVerify(r, zb, pk, msg), "")

	// 少于门限
	cl2 := cl[:2]
	zs2 := zs[:2]
	r2, z2b := Aggregate(pk, cl2, msg, zs2)
	check("不足门限时聚合签名无效", !SchnorrVerify(r2, z2b, pk, msg), "")
}

func testBindingAndNonce() {
	shares, pk, _ := setup(5, 3, 0x1234)
	sk := map[int64]*big.Int{}
	for _, sh := range shares {
		sk[sh[0].Int64()] = sh[1]
	}
	m1, m2 := []byte("msg one"), []byte("msg two")
	ids := []int64{1, 2, 3}
	cl := make([]Commitment, 0, 3)
	hiding := map[int64]*big.Int{}
	binding := map[int64]*big.Int{}
	for k, id := range ids {
		d := nonceGenerate(sk[id], fixedH(k))
		e := nonceGenerate(sk[id], fixedB(k))
		hiding[id], binding[id] = d, e
		cl = append(cl, Commitment{big.NewInt(id), ScalarBaseMult(d), ScalarBaseMult(e)})
	}
	b1 := ComputeBindingFactors(pk, cl, m1)
	b2 := ComputeBindingFactors(pk, cl, m2)
	check("绑定因子随消息改变", b1[0].Cmp(b2[0]) != 0, "")
	check("不同参与者绑定因子不同",
		b1[0].Cmp(b1[1]) != 0 && b1[1].Cmp(b1[2]) != 0, "")

	// 朴素方案 nonce 复用泄密
	id := int64(1)
	nonce := nonceGenerate(sk[id], fixedH(0))
	d := ScalarBaseMult(nonce)
	rN := NaiveGroupCommitment([]*big.Int{d})
	c1 := ComputeChallenge(rN, pk, m1)
	c2 := ComputeChallenge(rN, pk, m2)
	z1 := new(big.Int).Add(nonce, ScalarMul(sk[id], c1))
	z2 := new(big.Int).Add(nonce, ScalarMul(sk[id], c2))
	z1.Mod(z1, Order())
	z2.Mod(z2, Order())
	rec := RecoverSkFromReusedNonce(z1, z2, c1, c2)
	check("朴素方案：nonce 复用可解出私钥份额", rec.Cmp(sk[id]) == 0, "")

	// FROST 侧解不出来
	fz1 := Sign(big.NewInt(id), sk[id], pk, hiding[id], binding[id], m1, cl)
	fz2 := Sign(big.NewInt(id), sk[id], pk, hiding[id], binding[id], m2, cl)
	r1 := ComputeGroupCommitment(cl, ComputeBindingFactors(pk, cl, m1))
	r2 := ComputeGroupCommitment(cl, ComputeBindingFactors(pk, cl, m2))
	fc1 := ComputeChallenge(r1, pk, m1)
	fc2 := ComputeChallenge(r2, pk, m2)
	frec := RecoverSkFromReusedNonce(fz1, fz2, fc1, fc2)
	check("FROST：同样式子解不出私钥", frec.Cmp(sk[id]) != 0, "")
	check("FROST：两条消息的群承诺不同", r1.Cmp(r2) != 0, "")
}

func main() {
	fmt.Println("== FROST（RFC 9591）Go 侧复刻自检 ==")
	testGroup()
	testShamir()
	testFrost()
	testBindingAndNonce()
	fmt.Printf("\n断言 %d 条，失败 %d 条\n", total, failed)
	if failed > 0 {
		panic("有断言失败")
	}
}
