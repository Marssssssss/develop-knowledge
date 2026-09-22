#!/usr/bin/env python3
"""demo574 自检：detect-secrets 与 gitleaks 两套密钥扫描器的判定语义。

    python selfcheck_secret.py
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from detect_secrets import (  # noqa: E402
    Base64HighEntropyString, HexHighEntropyString, HighEntropyStringsPlugin,
    KeywordDetector, DENYLIST,
)
from gitleaks import Allowlist, Rule, detect_rule, prefilter_hit, scan, shannon_entropy  # noqa: E402
from main import ds_base64_report, ds_hex_report, ds_keyword_report  # noqa: E402

PASS = 0
FAILS = []


def ok(cond, msg):
    global PASS
    if cond:
        PASS += 1
    else:
        FAILS.append(msg)
        print("FAIL: %s" % msg)


B64 = Base64HighEntropyString()
HEX = HexHighEntropyString()

# ==========================================================================
# A. detect-secrets 的熵口径：只遍历 charset
# ==========================================================================

# A1 charset 之外的字符完全不计入
ok(B64.calculate_shannon_entropy("!@#$%^") == 0.0,
   "A1 非 charset 字符的串熵应为 0, 实际 %s" % B64.calculate_shannon_entropy("!@#$%^"))

# A2 均匀分布时等于 log2(符号数)
ok(abs(B64.calculate_shannon_entropy("abcdefghijklmnop") - 4.0) < 1e-12,
   "A2 16 个符号均匀分布应为 4.0, 实际 %s" % B64.calculate_shannon_entropy("abcdefghijklmnop"))

# A3 单一字符重复 → 0
ok(B64.calculate_shannon_entropy("aaaa") == 0.0, "A3 重复字符熵为 0")

# A4 分母是 len(data)，但只统计 charset 内字符 → 得到**偏低**的熵
ok(abs(B64.calculate_shannon_entropy("a!bc") - 1.5) < 1e-12,
   "A4 'a!bc' 在 detect-secrets 口径下是 1.5, 实际 %s" % B64.calculate_shannon_entropy("a!bc"))
ok(abs(shannon_entropy("a!bc") - 2.0) < 1e-12,
   "A4b 同一串在 gitleaks 口径下是 2.0, 实际 %s" % shannon_entropy("a!bc"))

# A5 charset 限制使 detect-secrets 的熵恒不大于 gitleaks 的熵
rnd = random.Random(20260922)
bad = 0
for _ in range(200):
    s = "".join(rnd.choice("abcXYZ019+/=\\-_!@# ") for _ in range(rnd.randint(1, 24)))
    if B64.calculate_shannon_entropy(s) > shannon_entropy(s) + 1e-9:
        bad += 1
ok(bad == 0, "A5 detect-secrets 的熵应恒 <= gitleaks 的熵, 反例 %d 个" % bad)

# A6 过滤是**严格大于**：熵恰好等于阈值时不报（analyze_line 返回的是集合）
ok(Base64HighEntropyString(4.0).analyze_line('x = "0123456789abcdef"') == set(),
   "A6 熵恰等于阈值时不应上报")
ok(Base64HighEntropyString(3.99).analyze_line('x = "0123456789abcdef"')
   == {"0123456789abcdef"},
   "A6b 阈值低一点点就应上报")

# A7 limit 必须在 0.0 ~ 8.0
for bad_limit in (-0.1, 8.1):
    try:
        HighEntropyStringsPlugin("abc", bad_limit)
        ok(False, "A7 limit=%s 应抛 ValueError" % bad_limit)
    except ValueError:
        ok(True, "A7 limit=%s 抛 ValueError" % bad_limit)

# A8 base64 的 charset 里含反斜杠（源码写的是 '\\-_'）
ok("\\" in B64.charset, "A8 base64 charset 含反斜杠")
ok("-" in B64.charset and "_" in B64.charset and "=" in B64.charset,
   "A8b base64 charset 含 - _ =")

# ==========================================================================
# B. hex 插件的纯数字惩罚
# ==========================================================================

# B1 "0123456789" 基础熵 3.3219，惩罚 1.2/log2(10)=0.3612 → 2.9607 < 3.0 → 不报
ok(abs(HEX.calculate_shannon_entropy("0123456789") - 2.9607) < 1e-3,
   "B1 '0123456789' 的 hex 熵约 2.9607, 实际 %s" % HEX.calculate_shannon_entropy("0123456789"))
ok(ds_hex_report('hex = "0123456789"') == [],
   "B2 默认 hex 插件（limit 3.0）应放过 '0123456789'")

# B3 更长的纯数字串惩罚更小 → 越过阈值（源码注释：越长越可能是真阳性）
ok(abs(HEX.calculate_shannon_entropy("01234567890123456789") - 3.0443) < 1e-3,
   "B3 长串的熵约 3.0443, 实际 %s" % HEX.calculate_shannon_entropy("01234567890123456789"))
ok(ds_hex_report('hex = "01234567890123456789"') == ["01234567890123456789"],
   "B3b 长串应被上报")

# B4 含字母 → int() 失败 → 不惩罚
ok(HEX.calculate_shannon_entropy("0123456789abcdef") == 4.0,
   "B4 含字母不惩罚, 实际 %s" % HEX.calculate_shannon_entropy("0123456789abcdef"))

# B5 len == 1 时不惩罚
ok(HEX.calculate_shannon_entropy("5") == 0.0, "B5 单字符不惩罚")

# B6 惩罚可以让熵变成负数
ok(HEX.calculate_shannon_entropy("55") < 0,
   "B6 惩罚后熵可以为负, 实际 %s" % HEX.calculate_shannon_entropy("55"))

# B7 真实 AWS key 的 base64 熵低于默认阈值 4.5 → 系统性漏报
ok(B64.calculate_shannon_entropy("AKIAIOSFODNN7EXAMPLE") < 4.5,
   "B7 AKIAIOSFODNN7EXAMPLE 熵 3.68 < 4.5")
ok(ds_base64_report('aws = "AKIAIOSFODNN7EXAMPLE"') == [],
   "B7b 默认 base64 插件放过该 AWS key（这是熵阈值的系统性漏报面）")

# ==========================================================================
# C. detect-secrets 的 keyword 插件
# ==========================================================================

# C1 引号 + 等号
ok(ds_keyword_report('password = "hunter2"') == ["hunter2"], "C1 引号赋值命中")

# C2 filetype 决定用哪套正则：yaml 允许无引号冒号赋值，python 不行
ok(ds_keyword_report("password: hunter2", "python") == [],
   "C2 python 无引号冒号赋值不命中")
ok(ds_keyword_report("password: hunter2", "yaml") == ["hunter2"],
   "C2b yaml 无引号冒号赋值命中")

# C3 同理：go 的 := 与无引号等号在 python 下不命中
ok(ds_keyword_report("password = hunter2", "python") == [],
   "C3 python 无引号等号赋值不命中（QUOTES_REQUIRED）")
ok(ds_keyword_report("password = hunter2", "go") == ["hunter2"],
   "C3b go 的等号赋值不要求引号，命中")

# C4 denylist 带 \w* 后缀 → password_secure 也命中
ok(ds_keyword_report('my_password_secure = "v"') == ["v"], "C4 后缀 affix 命中")

# C5 反向比较也支持
ok(ds_keyword_report('if ("v" == my_password) {}') == ["v"], "C5 反向比较命中")

# C6 **没有词边界**：not_a_secret 里的 secret 会命中
ok(ds_keyword_report('not_a_secret = "x"') == ["x"],
   "C6 denylist 无词边界，not_a_secret 也会命中（官方行为）")

# C7 keyword_exclude 整行跳过
ok(KeywordDetector("password").analyze_line('password = "x"') == set(),
   "C7 keyword_exclude 命中时整行跳过")

# C8 大小写不敏感
ok(ds_keyword_report('PASSWORD = "x"') == ["x"], "C8 denylist 大小写不敏感")

# C9 其它 denylist 项
ok(ds_keyword_report('pwd = "x"') == ["x"], "C9 pwd 命中")
ok(ds_keyword_report('apikey = "x"') == ["x"], "C9b apikey 命中（api_?key 的 _? 是可选下划线）")

# C10 无关变量名不命中
ok(ds_keyword_report('foo = "x"') == [], "C10 无关变量名不命中")

# C11/C12 C++ 与 C 的专用正则
ok(ds_keyword_report('std::string secret("bar");', "cpp") == ["bar"], "C11 C++ assign 括号命中")
ok(ds_keyword_report('char my_password[25] = "bar";', "c") == ["bar"], "C12 C 的数组下标形式命中")

# ==========================================================================
# D. gitleaks 的熵与阈值
# ==========================================================================

# D1 完整香农熵（不受字符集限制）
ok(abs(shannon_entropy("a!bc") - 2.0) < 1e-12, "D1 gitleaks 的熵是完整香农熵")
ok(shannon_entropy("") == 0, "D2 空串的熵是 0")

# D3 阈值判定是 `entropy <= r.Entropy → skip`，即严格大于才保留
r_eq = Rule("tok", r"tok=([A-Za-z]+)", entropy=2.0)
ok(detect_rule(r_eq, "tok=abcd") == [], "D3 熵恰等于阈值时被跳过")
r_lt = Rule("tok", r"tok=([A-Za-z]+)", entropy=1.99)
ok(len(detect_rule(r_lt, "tok=abcd")) == 1, "D3b 阈值稍低则保留")

# D4 entropy == 0 表示关闭熵检查
r_off = Rule("tok", r"tok=([A-Za-z]+)", entropy=0.0)
ok(len(detect_rule(r_off, "tok=aaaa")) == 1, "D4 entropy=0 时低熵串也上报")

# ==========================================================================
# E. gitleaks 的关键词预筛
# ==========================================================================

frag_kw = {"akcp", "abc"}
r_kw = Rule("artifactory", r"(AKCp[A-Za-z0-9]{4})", keywords=["akcp"])
ok(prefilter_hit(r_kw, frag_kw), "E1 片段含 keyword 时应扫描")
ok(not prefilter_hit(r_kw, {"other"}), "E1b 片段不含 keyword 时跳过")
r_nokw = Rule("generic", r"(AKCp[A-Za-z0-9]{4})")
ok(prefilter_hit(r_nokw, {"other"}), "E2 无 keywords 的规则总是扫描")

# E3 端到端：预筛不命中则整条规则不出结果
ok(scan([r_kw], "nothing here AKCpabcd") == [],
   "E3 预筛不命中时不出结果（尽管正则其实能匹配）")
ok(len(scan([r_kw], "prefix akcp AKCpabcd")) == 1, "E3b 预筛命中后正常扫描")

# ==========================================================================
# F. gitleaks 的规则校验
# ==========================================================================

ok(Rule("", r"(x)").validate() is not None, "F1 空 id 应报错")
ok(Rule("  ", r"(x)").validate() is not None, "F1b 全空白 id 也应报错")
ok(Rule("r", "").validate() is not None, "F2 regex 与 path 都空应报错")
ok(Rule("r", r"(x)", secret_group=2).validate() is not None,
   "F3 secretGroup 超过捕获组数应报错")
ok(Rule("r", r"(x)", secret_group=1).validate() is None, "F3b 合法配置通过")
ok(Rule("r", "", path=r"\.toml$").validate() is None, "F4 只有 path 也合法")

# ==========================================================================
# G. gitleaks 的 allowlist
# ==========================================================================

# G1 stopword
r_sw = Rule("tok", r"tok=([a-z]+)", allowlists=[Allowlist(stopwords=["example"])])
ok(detect_rule(r_sw, "tok=example") == [], "G1 stopword 命中时跳过")
ok(len(detect_rule(r_sw, "tok=realone")) == 1, "G1b 未命中时保留")

# G2 path
r_path = Rule("tok", r"tok=([a-z]+)", allowlists=[Allowlist(paths=[r"\.md$"])])
ok(detect_rule(r_path, "tok=abc", path="docs/readme.md") == [], "G2 path 命中时跳过")
ok(len(detect_rule(r_path, "tok=abc", path="src/app.py")) == 1, "G2b 未命中时保留")

# G3 regexTarget 决定拿哪段去匹配 allowlist 正则
r_secret = Rule("tok", r"tok=([a-z]+)_END",
                allowlists=[Allowlist(regexes=[r"^abc$"], regex_target="secret")])
ok(detect_rule(r_secret, "tok=abc_END") == [], "G3 regexTarget=secret 时被 allow 掉")
r_match = Rule("tok", r"tok=([a-z]+)_END",
               allowlists=[Allowlist(regexes=[r"^abc$"], regex_target="match")])
ok(len(detect_rule(r_match, "tok=abc_END")) == 1,
   "G3b regexTarget=match 时 allowlist 正则匹配不到整段，保留")

# G4 非法的 regexTarget 应被 Validate 拦下
ok(Rule("tok", r"(x)", allowlists=[Allowlist(regex_target="bogus")]).validate() is not None,
   "G4 非法 regexTarget 应报错")

# ==========================================================================
# H. 两套口径的差异总结
# ==========================================================================

# H1 两套「熵函数」本身口径不同：取中间阈值会得到相反结论。
# 注意：正常管线里 detect-secrets 的候选串已被正则限定为 charset-only，
# 两者会相等；差异只在直接调用该函数（如 audit 展示熵值）时显现。
sample = "a!bc"
ds_val = B64.calculate_shannon_entropy(sample)     # 1.5（'!' 不在 charset 内）
gl_val = shannon_entropy(sample)                   # 2.0
mid = (ds_val + gl_val) / 2                        # 1.75
ok(ds_val < mid < gl_val, "H1 两套熵函数给出不同值: ds=%s gl=%s" % (ds_val, gl_val))
ok(ds_val <= mid, "H1b detect-secrets 口径下低于中间阈值")
ok(gl_val > mid, "H1c gitleaks 口径下高于中间阈值")

# H2 两者的「阈值过滤」方向一致：都是严格大于
ok(Base64HighEntropyString(2.0).analyze_line('x = "abcd"') == set(),
   "H2 detect-secrets 严格大于")
ok(detect_rule(Rule("t", r'x\s*=\s*"([a-z]+)"', entropy=2.0), 'x = "abcd"') == [],
   "H2b gitleaks 严格大于")
ok(Base64HighEntropyString(1.99).analyze_line('x = "abcd"') == {"abcd"},
   "H2c detect-secrets 阈值稍低即上报")
ok(len(detect_rule(Rule("t", r'x\s*=\s*"([a-z]+)"', entropy=1.99), 'x = "abcd"')) == 1,
   "H2d gitleaks 阈值稍低即上报")

print("PASS=%d" % PASS)
if FAILS:
    print("FAILED=%d" % len(FAILS))
    sys.exit(1)
print("ALL OK")
