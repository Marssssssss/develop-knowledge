package main

// 账户安全与凭据填充防护 自检（与 selfcheck_accmgr.py 同一套断言）。
// 依据 NIST SP 800-63B（https://pages.nist.gov/800-63-4/sp800-63b.html）实读。

import "fmt"

var okCount int
var failed []string

func ck(name string, cond bool, detail string) {
	if cond {
		okCount++
	} else {
		failed = append(failed, name+" "+detail)
	}
}

func eq(name string, got, want interface{}) {
	ck(name, fmt.Sprintf("%v", got) == fmt.Sprintf("%v", want),
		fmt.Sprintf("got=%v want=%v", got, want))
}

func rep15(c string) string {
	out := ""
	for i := 0; i < 15; i++ {
		out += c
	}
	return out
}

func main() {
	// 长度门槛
	v := NewVerifier(false, RecommendedMax, ThrottleUpperBound)
	eq("单因素最短 15", v.MinLen, MinLenSingle)
	ok, _ := v.ValidatePassword(rep15("a")[:14])
	ck("14 字符被拒", !ok, "")
	ok, _ = v.ValidatePassword(rep15("a"))
	ck("15 字符通过（无组成规则）", ok, "")
	vm := NewVerifier(true, RecommendedMax, ThrottleUpperBound)
	eq("多因素最短 8", vm.MinLen, MinLenMFA)
	ok, _ = vm.ValidatePassword("aaaaaaa")
	ck("多因素下 7 字符被拒", !ok, "")
	ok, _ = vm.ValidatePassword("aaaaaaaa")
	ck("多因素下 8 字符通过", ok, "")

	// 最长 64
	ok, _ = v.ValidatePassword(rep15("a") + rep15("a") + rep15("a") + rep15("a") + "aaaa")
	ck("64 字符通过", ok, "")
	_, reasons := v.ValidatePassword(rep15("a") + rep15("a") + rep15("a") + rep15("a") + "aaaaa")
	ck("65 字符超出上限", len(reasons) > 0 && reasons[0] == "exceeds_max", fmt.Sprint(reasons))

	// 组成规则不得施加
	ok, _ = v.ValidatePassword("abcdefghijklmno")
	ck("纯小写通过", ok, "")
	ok, _ = v.ValidatePassword("123456789012345")
	ck("纯数字通过", ok, "")
	ok, _ = v.ValidatePassword(rep15(" "))
	ck("纯空格通过", ok, "")

	// Unicode：码点计数
	eq("中文按码点计数", PasswordLength("密码"), 2)
	eq("emoji 按码点计数", PasswordLength("🔐"), 1)
	ok, _ = v.ValidatePassword(rep15("密"))
	ck("15 个中文字符通过", ok, "")
	cn14 := ""
	for i := 0; i < 14; i++ {
		cn14 += "密"
	}
	ok, _ = v.ValidatePassword(cn14)
	ck("14 个中文字符被拒（不是按字节算）", !ok, "")

	// NFC：分解式与合成式归一到同值
	decomposed := "é"
	composed := "é"
	eq("NFC 归一", NormalizePassword(decomposed), composed)
	eq("归一后长度相同", PasswordLength(decomposed), PasswordLength(composed))

	// 完整校验不截断
	long := ""
	for i := 0; i < 70; i++ {
		long += "a"
	}
	v2 := NewVerifier(false, 128, ThrottleUpperBound)
	v2.Store("alice", long)
	ck("70 字符完整校验通过", v2.Verify("alice", long), "")
	ck("少一个字符即失败", !v2.Verify("alice", long[:69]), "")

	// 加盐哈希
	s1 := v2.Store("bob", "correct-horse-battery")
	s2 := v2.Store("carol", "correct-horse-battery")
	ck("同口令不同用户盐不同", s1[0] != s2[0], "")
	ck("同口令不同用户摘要不同", s1[2] != s2[2], "")

	// 黑名单：整串比对
	bl := NewVerifier(false, RecommendedMax, ThrottleUpperBound)
	bl.Blocklist["password"] = true
	_, r1 := bl.ValidatePassword("password")
	ck("整串命中被拒", len(r1) > 0 && r1[0] == "blocklisted", fmt.Sprint(r1))
	_, r2 := bl.ValidatePassword("mypassword1")
	ck("不按子串拦截", len(r2) == 0, fmt.Sprint(r2))
	ctx := NewVerifier(false, RecommendedMax, ThrottleUpperBound)
	ctx.ContextWords["acme"] = true
	_, r3 := ctx.ValidatePassword("acme")
	ck("上下文词被拒", len(r3) > 0 && r3[0] == "context_specific", fmt.Sprint(r3))
	ok, _ = ctx.ValidatePassword("xkcd-horse-battery")
	ck("非上下文词不误伤", ok, "")

	// §3.2.2 限流
	t := NewVerifier(false, RecommendedMax, 100)
	t.Store("dave", "a-very-long-password-x")
	for i := 0; i < 99; i++ {
		t.Authenticate("dave", "wrong")
	}
	eq("99 次失败后未锁定", t.ConsecutiveFailures("dave"), 99)
	_, locked := t.Authenticate("dave", "wrong")
	ck("第 100 次锁定", locked, "")
	okk, locked := t.Authenticate("dave", "a-very-long-password-x")
	ck("锁定后正确口令也不放行", !okk && locked, "")
	t.ResetFailures("dave")
	okk, _ = t.Authenticate("dave", "a-very-long-password-x")
	ck("重置后可认证", okk, "")

	// 限流是每账户的
	t3 := NewVerifier(false, RecommendedMax, 3)
	t3.Store("u1", "password-number-one-x")
	t3.Store("u2", "password-number-two-x")
	for i := 0; i < 3; i++ {
		t3.Authenticate("u1", "bad")
	}
	_, l1 := t3.Authenticate("u1", "password-number-one-x")
	ck("u1 被锁", l1, "")
	ok2, _ := t3.Authenticate("u2", "password-number-two-x")
	ck("u2 不受影响", ok2, "")

	// look-up secret
	ck("5 位被拒", !CheckLookupSecret("12345"), "")
	ck("6 位通过", CheckLookupSecret("123456"), "")
	ck("含字母被拒", !CheckLookupSecret("12345a"), "")

	// 带外 secret
	oob := &OutOfBandSecret{Secret: "482913", IssuedAt: 0}
	ck("有效期内可用", oob.Accept("482913", 0), "")
	ck("第二次使用被拒（重放防护）", !oob.Accept("482913", 1), "")
	oob2 := &OutOfBandSecret{Secret: "482913", IssuedAt: 0}
	ck("恰好 600 秒仍有效", oob2.Accept("482913", OOBValiditySeconds), "")
	oob3 := &OutOfBandSecret{Secret: "482913", IssuedAt: 0}
	ck("超过 600 秒无效", !oob3.Accept("482913", OOBValiditySeconds+1), "")

	// 激活密钥
	act := &ActivationSecret{Secret: "1234"}
	ck("长度达标", act.ValidLength(), "")
	ck("短密钥不达标", !(&ActivationSecret{Secret: "123"}).ValidLength(), "")
	for i := 0; i < ActivationRetryLim-1; i++ {
		eq("第几次失败仍是 reject", act.TrySecret("wrong"), "reject")
	}
	eq("第 10 次失败即锁", act.TrySecret("wrong"), "locked")
	eq("锁定时计数是上限", act.Retries, ActivationRetryLim)
	eq("锁后正确密钥也无效", act.TrySecret("1234"), "locked")

	fmt.Println("OK =", okCount)
	if len(failed) > 0 {
		fmt.Println("FAILED =", len(failed))
		for _, s := range failed {
			fmt.Println("  -", s)
		}
	} else {
		fmt.Println("ALL OK")
	}
}
