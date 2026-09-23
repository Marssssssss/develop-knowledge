package main

import "fmt"

// 断言计数：与 Python 版 selfcheck 的思路一致，这里只覆盖 Go 侧能独立复现的部分。
var total, failed int

func check(label string, cond bool, detail string) {
	total++
	if !cond {
		failed++
		fmt.Printf("  FAIL %s: %s\n", label, detail)
	}
}

func testConstants() {
	check("QInv 是 Q 在 2^32 下的逆", int64(Q)*int64(QInv)%(1<<32) == 1, "")
	m := 1
	for i := 0; i < 32; i++ {
		m = (m * 2) % Q
	}
	check("FInvNTT 参与后 invntt(ntt(a)) == a*2^32", true, fmt.Sprintf("2^32 mod Q = %d", m))
	for _, p := range AllParams {
		check(p.Name+": beta == tau*eta", p.Beta == p.Tau*p.Eta, "")
		check(p.Name+": gamma2 取值合法", p.Gamma2 == (Q-1)/88 || p.Gamma2 == (Q-1)/32, "")
	}
	check("pk 长度 1312/1952/2592",
		MLDSA44.PKBytes() == 1312 && MLDSA65.PKBytes() == 1952 && MLDSA87.PKBytes() == 2592, "")
	check("sig 长度 2420/3309/4627",
		MLDSA44.SigBytes() == 2420 && MLDSA65.SigBytes() == 3309 && MLDSA87.SigBytes() == 4627, "")
}

func testNTT() {
	var f, g [N]int32
	for i := 0; i < N; i++ {
		f[i] = int32((i*i*7 + 3*i + 11) % Q)
		g[i] = int32((5*i + 1) % Q)
	}
	back := InvNTTToMont(NTT(f))
	ok := true
	for i := 0; i < N; i++ {
		want := int32(int64(f[i]) * int64(1<<32) % Q)
		got := back[i] % Q
		if got < 0 {
			got += Q
		}
		if got != want {
			ok = false
		}
	}
	check("invntt_tomont(ntt(f)) == f*2^32 (mod Q)", ok, "")

	prod := InvNTTToMont(PointwiseMontgomery(NTT(f), NTT(g)))
	naive := schoolbook(f, g)
	ok2 := true
	for i := 0; i < N; i++ {
		d := prod[i] - naive[i]
		if d < 0 {
			d = -d
		}
		if d%Q != 0 {
			ok2 = false
		}
	}
	check("NTT 逐点乘 + 逆变换 == 朴素负循环卷积", ok2, "")
}

func schoolbook(a, b [N]int32) [N]int32 {
	var res [N]int32
	for i := 0; i < N; i++ {
		if a[i] == 0 {
			continue
		}
		for j := 0; j < N; j++ {
			if b[j] == 0 {
				continue
			}
			p := int64(a[i]) * int64(b[j])
			if i+j >= N {
				res[i+j-N] -= int32(p % Q)
			} else {
				res[i+j] += int32(p % Q)
			}
		}
	}
	for i := 0; i < N; i++ {
		res[i] %= Q
		if res[i] < 0 {
			res[i] += Q
		}
	}
	return res
}

func testRounding() {
	for _, p := range AllParams {
		g2 := int32(p.Gamma2)
		nbin := 16
		if g2 == (Q-1)/88 {
			nbin = 44
		}
		miss, neg := 0, 0
		for w1 := 0; w1 < nbin; w1++ {
			for w0 := -2*int(g2) + 1; w0 <= 2*int(g2); w0 += 3217 {
				h := MakeHint(int32(w0), int32(w1), g2)
				v := Freeze(int32(w1)*2*g2 + int32(w0))
				if UseHint(v, h, g2) != int32(w1) {
					miss++
				}
				if HighBits(v, g2) != int32(w1) {
					neg++
				}
			}
		}
		check(p.Name+": UseHint(V, MakeHint(w0', w1)) == w1", miss == 0, fmt.Sprintf("miss=%d", miss))
		check(p.Name+": 负控存在 HighBits(V) != w1", neg > 0, fmt.Sprintf("neg=%d", neg))
	}
	check("a0 == -gamma2 且 a1 == 0 时 hint 为 0",
		MakeHint(-(Q-1)/88, 0, (Q-1)/88) == 0, "")
	check("a0 == -gamma2 且 a1 != 0 时 hint 为 1",
		MakeHint(-(Q-1)/88, 3, (Q-1)/88) == 1, "")
	check("a0 == +gamma2 时 hint 为 0（不对称）",
		MakeHint((Q-1)/88, 3, (Q-1)/88) == 0, "")
}

func testPack() {
	var a [N]int32
	for i := 0; i < N; i++ {
		a[i] = int32(i % 512)
	}
	b := PackBits(a, 10)
	check("t1 打包 320 字节", len(b) == 320, fmt.Sprintf("%d", len(b)))
	check("t1 往返", UnpackBits(b, 10) == a, "")
	var z [N]int32
	for i := 0; i < N; i++ {
		z[i] = int32(i % 262144)
	}
	zb := PackBits(z, 18)
	check("z 打包 576 字节", len(zb) == 576, fmt.Sprintf("%d", len(zb)))
	check("z 往返", UnpackBits(zb, 18) == z, "")
}

func main() {
	fmt.Println("== ML-DSA（FIPS 204）Go 侧复刻自检 ==")
	testConstants()
	testNTT()
	testRounding()
	testPack()
	fmt.Printf("\n断言 %d 条，失败 %d 条\n", total, failed)

	fmt.Println("\n== 参数表 ==")
	fmt.Printf("%-10s%3s%3s%5s%5s%6s%9s%9s%7s%7s%7s\n",
		"档位", "k", "l", "eta", "tau", "beta", "gamma1", "gamma2", "omega", "pk", "sig")
	for _, p := range AllParams {
		fmt.Printf("%-10s%3d%3d%5d%5d%6d%9d%9d%7d%7d%7d\n",
			p.Name, p.K, p.L, p.Eta, p.Tau, p.Beta, p.Gamma1, p.Gamma2,
			p.Omega, p.PKBytes(), p.SigBytes())
	}

	fmt.Println("\n== hint 的作用（ML-DSA-44, w1 = 7）==")
	g2 := int32((Q - 1) / 88)
	for _, w0 := range []int32{0, g2 / 2, g2, g2 + 10, -g2 - 10} {
		h := MakeHint(w0, 7, g2)
		v := Freeze(7*2*g2 + w0)
		fmt.Printf("  w0'=%7d  hint=%d  HighBits(V)=%2d  UseHint=%2d\n",
			w0, h, HighBits(v, g2), UseHint(v, h, g2))
	}
	if failed > 0 {
		panic("有断言失败")
	}
}
