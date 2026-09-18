// sae_main.go —— WPA3-SAE（Dragonfly）自检（Go，纯标准库）
//
// 运行：go run ./go      （或 go build ./go && ./go）
//
// RFC 7664 不提供测试向量，所以断言分三类：
//
//	A. 可由规范直接推出的等式（PE 必须等于第一轮种子的平方、element·PE^scalar == PE^private）
//	B. 协议必须成立的性质（两端推出同一 ss/kck、错密码必然确认失败、反射攻击必须中止）
//	C. 与 Python / C 版共享的固定标量向量（比 sha256 摘要，避免在源码里塞 2048 位常量）
package main

import (
	"bytes"
	"encoding/hex"
	"errors"
	"fmt"
	"math/big"
	"os"
	"strings"
)

var checks, failures int

// check：沿用本仓库 Go demo 的约定签名（label, ok, detail），失败时打印实际值
func check(label string, ok bool, detail string) {
	checks++
	if !ok {
		failures++
		fmt.Printf("  FAIL %-50s %s\n", label, detail)
	}
}

// 与 Python / C 共用的固定标量（真实实现必须随机；这里的值是 128 位，远小于 q）
const (
	vecPW          = "password"
	vecPrivA       = "0102030405060708090a0b0c0d0e0f10"
	vecMaskA       = "1112131415161718191a1b1c1d1e1f20"
	vecPrivB       = "2122232425262728292a2b2c2d2e2f30"
	vecMaskB       = "3132333435363738393a3b3c3d3e3f40"
	vecScalarA     = "121416181a1c1e20222426282a2c2e30"
	vecScalarB     = "525456585a5c5e60626466686a6c6e70"
	vecPESHA       = "3fc647ccc7eb193dd5cb02744ab43ad28d059803187b9c31425f560ef3c49e2e"
	vecElemASHA    = "3a880829d0f928f5a97280169555bc06fce8ef0f58384c19a9ed2a9a8775191a"
	vecSSSHA       = "2b0064354b3485159da05a2d8c09fd75efe23e0b6e065c1662cd22eca6168eae"
	vecKCKSHA      = "eaa108559fa7ef14e65d4c0076d558374d5bf59cdeb596ac8ba542ae5ad6a0e2"
	vecConfirmA    = "60c111a2a5f1821ce0541eb45cddf321950354a0f3912a25d07977af7c2ad402"
	vecConfirmB    = "39a4888b1951f08081acc00c53952170e1fa7f1979d1a0bd44839eccaf95d28e"
	vecWrongPESHA  = "188b156513907cfb4a5b140b96be1b6e3c27e751fb602a8440a7907bb2f3a750"
)

// newCommitted 造一个 k=1（狩猎与啄食便宜）且已用固定标量提交的 peer
func newCommitted() *saePeer {
	pr := newPeer(vecPW, "alice", "bob", 1)
	if err := pr.commitWith(vecPrivA, vecMaskA); err != nil {
		panic(err)
	}
	return pr
}

func checkGroup() {
	check("p 是 2048 位", p.BitLen() == 2048, fmt.Sprint(p.BitLen()))
	check("p 与 RFC 3526 §3 原文一致", strings.ToUpper(p.Text(16)) == pHex, "")
	check("生成元 G = 2", g.Cmp(two) == 0, g.String())
	check("p ≡ 3 (mod 4)", new(big.Int).Mod(p, big.NewInt(4)).Int64() == 3, "")
	check("q = (p-1)/2", q.Cmp(new(big.Int).Rsh(pMinus1, 1)) == 0, "")
	check("(p-1)/q = 2（安全素数的标志）", expPeck.Cmp(two) == 0, expPeck.String())
	check("p 是素数", p.ProbablyPrime(20), "")
	check("q 也是素数（故 p 是安全素数）", q.ProbablyPrime(20), "")
	check("2^q = 1，即 G 的阶恰为 q", scalarOp(q, g).Cmp(one) == 0, "")
	check("G 是合法子群元素", isValidElement(g), "")
	check("p-1 不是合法元素（(-1)^q = -1）", !isValidElement(pMinus1), "")
	check("1 不是合法元素", !isValidElement(one), "")
	check("0 不是合法元素", !isValidElement(new(big.Int)), "")
	check("p 不是合法元素（必须严格小于 p-1）",
		!isValidElement(new(big.Int).Set(p)), "")

	nonres := new(big.Int)
	for x := int64(3); x < 100; x++ { // 找一个小非二次剩余作对照
		cand := big.NewInt(x)
		if !isValidElement(cand) {
			nonres = cand
			break
		}
	}
	check("存在不属于子群的样本", nonres.Sign() != 0, "")
	check("非二次剩余的平方回到子群", isValidElement(elementOp(nonres, nonres)), "")
	check("r · r^-1 mod p = 1",
		elementOp(big.NewInt(12345), inverse(big.NewInt(12345))).Cmp(one) == 0, "")
}

func checkPWE() {
	pe, iters := huntingAndPecking([]byte(vecPW), []byte("alice"), []byte("bob"), 40)
	check("PE 是合法子群元素", isValidElement(pe), "")
	check("PE > 1", pe.Cmp(one) > 0, "")
	check("狩猎与啄食至少跑满 k=40 轮", iters == 40, fmt.Sprint(iters))
	check("PE 摘要与 Python/C 一致", bigDigest(pe, pBytes) == vecPESHA,
		bigDigest(pe, pBytes))

	check("PE 与身份顺序无关",
		pe.Cmp(derivePWE([]byte(vecPW), []byte("bob"), []byte("alice"), 1)) == 0, "")
	other := derivePWE([]byte("wrong-password"), []byte("alice"), []byte("bob"), 40)
	check("不同密码 → 不同 PE", pe.Cmp(other) != 0, "")
	check("错密码 PE 摘要与 Python/C 一致", bigDigest(other, pBytes) == vecWrongPESHA,
		bigDigest(other, pBytes))

	// A 类：PE 必须等于「counter=1 的 base → KDF-(len(p)+64) → mod (p-1) + 1 → 啄食」
	buf := append([]byte("bob"), []byte("alice")...) // max(alice,bob) = "bob"
	buf = append(buf, []byte(vecPW)...)
	buf = append(buf, 1)
	seed := new(big.Int).Mod(kdf(pweLabel, sha256Sum(buf), (p.BitLen()+64+7)/8), pMinus1)
	seed.Add(seed, one)
	check("PE == seed^((p-1)/q) mod p", scalarOp(expPeck, seed).Cmp(pe) == 0, "")
	check("安全素数群下啄食就是平方（第一轮即命中）",
		new(big.Int).Exp(seed, two, p).Cmp(pe) == 0, "")
}

func checkCommit() {
	a := newPeer(vecPW, "alice", "bob", 40)
	err := a.commitWith(vecPrivA, vecMaskA)
	check("commit 成功", err == nil, fmt.Sprint(err))
	check("scalar = (private + mask) mod q，与 Python/C 一致", bigEqHex(a.scalar, vecScalarA),
		a.scalar.Text(16))
	check("scalar >= 2（规范要求）", a.scalar.Cmp(two) >= 0, "")
	check("element 是合法子群元素", isValidElement(a.element), "")
	check("element 摘要与 Python/C 一致", bigDigest(a.element, pBytes) == vecElemASHA,
		bigDigest(a.element, pBytes))
	// A 类等式：element · PE^scalar == PE^private
	check("element · PE^scalar == PE^private",
		elementOp(a.element, scalarOp(a.scalar, a.pe)).Cmp(scalarOp(a.priv, a.pe)) == 0, "")
	check("element != PE", a.element.Cmp(a.pe) != 0, "")
}

func checkExchange() {
	a := newPeer(vecPW, "alice", "bob", 40)
	b := newPeer(vecPW, "bob", "alice", 40)
	check("两端 PE 相同", a.pe.Cmp(b.pe) == 0, "")
	if err := a.commitWith(vecPrivA, vecMaskA); err != nil {
		panic(err)
	}
	if err := b.commitWith(vecPrivB, vecMaskB); err != nil {
		panic(err)
	}
	check("B 端 scalar 与 Python/C 一致", bigEqHex(b.scalar, vecScalarB), b.scalar.Text(16))
	check("两端 scalar 不同", a.scalar.Cmp(b.scalar) != 0, "")
	errA, errB := a.absorb(b), b.absorb(a) // 不用 && 短路，否则失败时后一个 peer 没密钥会误崩
	check("两端互相吸收提交", errA == nil && errB == nil, fmt.Sprint(errA, errB))
	check("两端 ss 相同", a.ss.Cmp(b.ss) == 0, "")
	check("ss 摘要与 Python/C 一致", bigDigest(a.ss, pBytes) == vecSSSHA,
		bigDigest(a.ss, pBytes))
	check("两端 kck 相同", a.kck.Cmp(b.kck) == 0, "")
	check("kck 摘要与 Python/C 一致", bigDigest(a.kck, pBytes) == vecKCKSHA,
		bigDigest(a.kck, pBytes))
	check("mk 与 kck 不同（KDF 输出被对半切开）", a.mk.Cmp(a.kck) != 0, "")
	check("两端 mk 相同", a.mk.Cmp(b.mk) == 0, "")

	ca, cb := a.selfConfirm(), b.selfConfirm()
	check("双方确认值不同（发送方标识不同）", !bytes.Equal(ca, cb), "")
	check("A 端确认值与 Python/C 一致", hex.EncodeToString(ca) == vecConfirmA,
		hex.EncodeToString(ca))
	check("B 端确认值与 Python/C 一致", hex.EncodeToString(cb) == vecConfirmB,
		hex.EncodeToString(cb))
	check("B 能验证 A 的确认值", b.verify(ca), "")
	check("A 能验证 B 的确认值", a.verify(cb), "")
	check("拿自己的确认值 verify 自己必然失败", !a.verify(ca) && !b.verify(cb), "")
}

func checkWrongPassword() {
	a := newPeer(vecPW, "alice", "bob", 40)
	b := newPeer("Password", "bob", "alice", 40) // 只差一个大小写
	a.commitWith(vecPrivA, vecMaskA)
	b.commitWith(vecPrivB, vecMaskB)
	check("大小写不同 → PE 不同", a.pe.Cmp(b.pe) != 0, "")
	a.absorb(b)
	b.absorb(a)
	check("错密码下 ss 必不相同", a.ss.Cmp(b.ss) != 0, "")
	check("错密码下确认必然失败", !b.verify(a.selfConfirm()), "")
}

func checkRejections() {
	// 1. 反射攻击：对端原样回送自己的 scalar 与 element
	self := newCommitted()
	echo := &saePeer{scalar: self.scalar, element: self.element}
	check("反射攻击必须被拒绝", errors.Is(self.absorb(echo), errReflection), "")

	// 2. 对端标量不在 (1, q) 内：0、1、q、q+5
	badScalars := []*big.Int{big.NewInt(0), big.NewInt(1), new(big.Int).Set(q),
		new(big.Int).Add(q, big.NewInt(5))}
	for i, v := range badScalars {
		self := newCommitted()
		bad := &saePeer{scalar: v, element: self.element}
		check(fmt.Sprintf("非法对端标量必须被拒绝 #%d", i),
			errors.Is(self.absorb(bad), errBadScalar), "")
	}

	// 3. 对端元素不在子群内：0、1、p-1、p（标量取另一组固定值以免撞上反射判定）
	badElements := []*big.Int{big.NewInt(0), big.NewInt(1), new(big.Int).Sub(p, one),
		new(big.Int).Set(p)}
	for i, v := range badElements {
		self := newCommitted()
		bad := &saePeer{scalar: mustBig(vecScalarB), element: v}
		check(fmt.Sprintf("非法对端元素必须被拒绝 #%d", i),
			errors.Is(self.absorb(bad), errBadElement), "")
	}

	// 4. 还没 commit 就想吸收对端提交
	fresh := newPeer(vecPW, "alice", "bob", 1)
	bad := &saePeer{scalar: big.NewInt(5), element: big.NewInt(5)}
	check("未 commit 就 absorb 必须报错", errors.Is(fresh.absorb(bad), errNoCommit), "")
	check("未 commit 就不能出确认值", fresh.selfConfirm() == nil, "")
	check("没有 kck 时 verify 也失败", !fresh.verify(make([]byte, 32)), "")
	// 对端字段缺失（scalar/element 为 nil）也不能崩
	check("对端字段缺失时按非法标量处理",
		errors.Is(newCommitted().absorb(&saePeer{}), errBadScalar), "")
}

func checkSensitive() {
	self := newCommitted()
	check("commit 后 mask 已销毁（置 nil）", self.mask == nil, "")
	check("private 仍落在 (1, q) 内", inRange(self.priv), "")
	// 定宽序列化不可绕过：值超出宽度时 FillBytes 会 panic 而不是悄悄截断
	panicked := false
	func() {
		defer func() {
			if recover() != nil {
				panicked = true
			}
		}()
		fillBytes(new(big.Int).Lsh(one, uint(8*pBytes)), pBytes)
	}()
	check("FillBytes 超宽即 panic", panicked, "")
}

func main() {
	fmt.Println("WPA3 SAE / Dragonfly 自检 —— RFC 7664 + RFC 3526 §3（Go 标准库）")
	checkGroup()
	checkPWE()
	checkCommit()
	checkExchange()
	checkWrongPassword()
	checkRejections()
	checkSensitive()
	fmt.Printf("sae_main: %d checks, %d failures\n", checks, failures)
	if failures > 0 {
		os.Exit(1)
	}
}
