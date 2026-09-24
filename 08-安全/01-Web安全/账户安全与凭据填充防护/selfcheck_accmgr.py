"""账户安全与凭据填充防护 自检。

依据 NIST SP 800-63B（https://pages.nist.gov/800-63-4/sp800-63b.html 353757 B 实读）：
  3.1.1.2 单因素口令 SHALL ≥15；多因素下 MAY 更短但 SHALL ≥8；SHOULD 允许 ≥64；
       每个 Unicode 码点算一个字符；接受 Unicode 则哈希前做 NFC；
       SHALL NOT 施加组成规则 / 定期改密 / 未认证可读的 hint / KBA；
       SHALL 请求完整口令并校验完整口令（不截断）；
       黑名单对**整串**比对，不是子串；命中 SHALL 给出拒绝理由；
       SHALL 加盐哈希存储。
  3.1.2.1 look-up secret SHALL ≥6 位十进制数字。
  3.1.3   带外 secret SHALL 在 10 分钟内完成，且有效期内只接受一次。
  3.2.2   连续失败认证尝试的上限是 100（ agencies MAY 更低）。
  3.1.x   激活密钥连续失败重试 SHALL 不超过 10。
"""

import unicodedata

from accmgr import (ACTIVATION_MIN_LEN, ACTIVATION_RETRY_LIMIT, MIN_LEN_MFA,
                    MIN_LEN_SINGLE, OOB_VALIDITY_SECONDS, RECOMMENDED_MAX,
                    THROTTLE_UPPER_BOUND, ActivationSecret, OutOfBandSecret,
                    Verifier, check_lookup_secret, normalize_password,
                    password_length)

OK = 0
FAIL = []


def ck(name, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAIL.append("%s %s" % (name, detail))


def eq(name, got, want):
    ck(name, got == want, "got=%r want=%r" % (got, want))


# ---------- 长度门槛 ----------
v = Verifier()
eq("单因素最短 15 生效", v.min_len, MIN_LEN_SINGLE)
ok, why = v.validate_password("a" * 14)
ck("14 字符被拒", not ok and "too_short" in why)
ok, why = v.validate_password("a" * 15)
ck("15 字符通过（且不含数字/大写也通过）", ok, str(why))
vm = Verifier(mfa=True)
eq("多因素最短 8 生效", vm.min_len, MIN_LEN_MFA)
ck("多因素下 7 字符被拒", not vm.validate_password("a" * 7)[0])
ck("多因素下 8 字符通过", vm.validate_password("a" * 8)[0])
ck("多因素下 14 字符也通过", vm.validate_password("a" * 14)[0])
ck("单因素下 8 字符仍被拒", not v.validate_password("a" * 8)[0])

# ---------- 最长 64（SHOULD 允许至少 64） ----------
ck("64 字符通过", v.validate_password("a" * RECOMMENDED_MAX)[0])
ck("65 字符超出上限", "exceeds_max" in v.validate_password("a" * 65)[1])

# ---------- 组成规则：不得施加 ----------
ck("纯小写 15 位通过（无组成规则）", v.validate_password("abcdefghijklmno")[0])
ck("纯数字 15 位通过", v.validate_password("123456789012345")[0])
ck("纯空格 15 位通过（可打印 ASCII 含空格）", v.validate_password(" " * 15)[0])
ck("含符号通过", v.validate_password("!@#$%^&*()!@#$%^")[0])

# ---------- Unicode：码点计数 + NFC ----------
eq("中文按码点计数", password_length("密码"), 2)
eq("emoji 按码点计数", password_length("🔐"), 1)
ck("15 个中文字符通过", v.validate_password("密" * 15)[0])
ck("14 个中文字符被拒（不是按字节算）",
   not v.validate_password("密" * 14)[0])
# NFC：分解形式与合成形式必须归一到同一个串
decomposed = "e" + unicodedata.lookup("COMBINING ACUTE ACCENT")
composed = unicodedata.lookup("LATIN SMALL LETTER E WITH ACUTE")
eq("NFC 把分解式归一为合成式", normalize_password(decomposed), composed)
ck("两种写法长度相同", password_length(decomposed) == password_length(composed))
eq("合成式长度是 1", password_length(composed), 1)

# ---------- 完整校验，不截断 ----------
long_pw = "a" * 70
v2 = Verifier(max_len=128)
v2.store("alice", long_pw)
ck("70 字符口令完整校验通过", v2.verify("alice", long_pw))
ck("少一个字符即失败（没有被截断比较）", not v2.verify("alice", long_pw[:-1]))
ck("多一个字符即失败", not v2.verify("alice", long_pw + "b"))

# ---------- 加盐哈希存储 ----------
s1 = v2.store("bob", "correct-horse-battery")
s2 = v2.store("carol", "correct-horse-battery")
ck("同一口令不同用户盐不同", s1[0] != s2[0])
ck("同一口令不同用户摘要不同", s1[2] != s2[2])
ck("存储带成本因子", s1[1] == v2.cost and s1[1] > 0)

# ---------- 黑名单：整串比对，不是子串 ----------
bl = Verifier(blocklist=["password", "12345678"])
ck("整串命中被拒", "blocklisted" in bl.validate_password("password")[1])
ck("包含黑名单词但整串不同 → 不按子串拦",
   "blocklisted" not in bl.validate_password("mypassword1")[1])
ck("黑名单译注：漏网之鱼要靠语料本身收录",
   "blocklisted" in bl.validate_password("12345678")[1])
ctx = Verifier(context_words=["acme", "alice", "acme-alice"])
ck("上下文词（服务名）被拒",
   "context_specific" in ctx.validate_password("acme")[1])
ck("上下文词（用户名）被拒",
   "context_specific" in ctx.validate_password("alice")[1])
ck("上下文派生词被拒",
   "context_specific" in ctx.validate_password("acme-alice")[1])
ck("非上下文词不误伤", ctx.validate_password("xkcd-horse-battery")[0])

# ---------- §3.2.2 限流 ----------
t = Verifier(throttle_limit=100)
t.store("dave", "a-very-long-password-x")
eq("初始无失败", t.consecutive_failures("dave"), 0)
for _i in range(99):
    t.authenticate("dave", "wrong")
eq("99 次失败后仍未锁定", t.consecutive_failures("dave"), 99)
okk, locked = t.authenticate("dave", "wrong")
eq("第 100 次失败后锁定次数到上限", t.consecutive_failures("dave"), 100)
ck("达到上限即锁", locked)
okk, locked = t.authenticate("dave", "a-very-long-password-x")
ck("锁定后即便口令正确也不放行", not okk and locked)
t.reset_failures("dave")
ck("重置后可正常认证", t.authenticate("dave", "a-very-long-password-x")[0])
eq("成功后失败计数归零", t.consecutive_failures("dave"), 0)

# 中间成功会重置连续计数（这是「连续」的含义）
t2 = Verifier(throttle_limit=5)
t2.store("eve", "another-long-password-1")
for _i in range(4):
    t2.authenticate("eve", "bad")
t2.authenticate("eve", "another-long-password-1")
eq("成功清零后重新计数", t2.consecutive_failures("eve"), 0)
for _i in range(5):
    t2.authenticate("eve", "bad")
ck("再次累计到上限才锁", t2.consecutive_failures("eve") == 5)

# 限流是「每账户」的，不是全局的
t3 = Verifier(throttle_limit=3)
t3.store("u1", "password-number-one-x")
t3.store("u2", "password-number-two-x")
for _i in range(3):
    t3.authenticate("u1", "bad")
ck("u1 被锁", t3.authenticate("u1", "password-number-one-x")[1])
ck("u2 不受影响", t3.authenticate("u2", "password-number-two-x")[0])

# ---------- look-up secret ----------
ck("5 位数字被拒", not check_lookup_secret("12345"))
ck("6 位数字通过", check_lookup_secret("123456"))
ck("6 位含字母被拒", not check_lookup_secret("12345a"))
ck("更长的数字串通过", check_lookup_secret("1234567890"))

# ---------- 带外 secret：10 分钟 + 单次有效 ----------
oob = OutOfBandSecret("482913", issued_at=0)
ck("有效期内可用", oob.accept("482913", now=0))
ck("第二次使用被拒（重放防护）", not oob.accept("482913", now=1))
oob2 = OutOfBandSecret("482913", issued_at=0)
ck("恰好 600 秒仍有效", oob2.accept("482913", now=OOB_VALIDITY_SECONDS))
oob3 = OutOfBandSecret("482913", issued_at=0)
ck("超过 600 秒无效", not oob3.accept("482913", now=OOB_VALIDITY_SECONDS + 1))
oob4 = OutOfBandSecret("482913", issued_at=0)
ck("错误 secret 被拒", not oob4.accept("999999", now=1))
ck("错误尝试不消耗单次额度", oob4.accept("482913", now=1))

# ---------- 激活密钥重试上限 ----------
act = ActivationSecret("1234")
eq("密钥长度达标", act.valid_length(), True)
eq("短密钥不达标", ActivationSecret("123").valid_length(), False)
# 「连续失败不超过 10 次」= 前 9 次是 reject，第 10 次把计数顶到上限并锁死
for _i in range(ACTIVATION_RETRY_LIMIT - 1):
    eq("第 %d 次失败仍是 reject" % (_i + 1), act.try_secret("wrong"), "reject")
eq("第 10 次失败即锁（上限 10）", act.try_secret("wrong"), "locked")
eq("锁定时计数正好是上限", act.retries, ACTIVATION_RETRY_LIMIT)
eq("锁后正确密钥也无效", act.try_secret("1234"), "locked")
act2 = ActivationSecret("1234")
eq("正确密钥通过", act2.try_secret("1234"), "ok")
eq("成功后重试计数归零", act2.retries, 0)

print("OK =", OK)
if FAIL:
    print("FAILED =", len(FAIL))
    for f in FAIL:
        print("  -", f)
else:
    print("ALL OK")
