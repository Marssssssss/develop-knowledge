"""CVSS v4.0 评分自检。

期望值来源：
1) 标「官方」的分数/宏向量：把同一条向量喂给官方参考实现
   FIRSTdotorg/cvss-v4-calculator（cvss_lookup.js + cvss_score.js + max_composed.js
   + max_severity.js），用 node 跑出来的 toFixed(1) 结果。
2) 其余为结构性断言（宏向量判定、默认值规则、解析校验），判据直接引规范 8 节的
   Table 24-30 与 cvss_score.js 的 m()。
"""

import random
import sys

from cvss import (
    VectorError,
    macrovector,
    parse_vector,
    resolve,
    score,
    score_vector,
    severity,
    _eq5,
    _eq6,
    _pick_max_vector,
)
from cvssdata import LEVELS, LOOKUP, SC_LEVELS, SI_LEVELS

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAILED: " + label
    PASS += 1


def eq(a, b, label):
    ok(a == b, "%s (got %r want %r)" % (label, a, b))


def close(a, b, label, tol=1e-9):
    ok(abs(a - b) <= tol, "%s (got %r want %r)" % (label, a, b))


def raises(fn, label):
    try:
        fn()
    except VectorError:
        return
    raise AssertionError("FAILED (应报错却通过): " + label)


def metric_of(token, name):
    idx = token.find(name + ":")
    if idx < 0:
        return None
    rest = token[idx + len(name) + 1:]
    slash = rest.find("/")
    return rest[:slash] if slash >= 0 else rest


# ---- 1. 与官方参考实现对拍 ----
OFFICIAL = [
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/E:A/CR:H/IR:H/AR:H/MSI:S",
     "000000", 10.0),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/E:A/CR:H/IR:H/AR:H",
     "000100", 10.0),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/E:P/CR:H/IR:H/AR:H/MSI:S",
     "000010", 9.8),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/E:U/CR:H/IR:H/AR:H/MSI:S",
     "000020", 9.5),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H",
     "000100", 10.0),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/CR:X/IR:X/AR:X",
     "000100", 10.0),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/CR:M/IR:M/AR:M",
     "000101", 9.6),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:N/SI:N/SA:N",
     "002201", 0.0),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H",
     "002201", 0.0),
    ("CVSS:4.0/AV:P/AC:H/AT:P/PR:H/UI:A/VC:L/VI:L/VA:L/SC:L/SI:L/SA:L/E:U/CR:L/IR:L/AR:L",
     "212221", 0.1),
    ("CVSS:4.0/AV:L/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H",
     "100200", 8.5),
    ("CVSS:4.0/AV:A/AC:H/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H",
     "110200", 7.5),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H"
     "/MSC:H/MSI:S/MSA:S", "000000", 10.0),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H"
     "/MSC:H/MSI:H/MSA:H", "000100", 10.0),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:L/VI:L/VA:L/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H",
     "002201", 6.9),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:L/VI:L/VA:L/SC:N/SI:N/SA:N/E:A/CR:M/IR:M/AR:M",
     "002201", 6.9),
    ("CVSS:4.0/AV:N/AC:H/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H",
     "010200", 9.2),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H",
     "100200", 8.7),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:P/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H",
     "100200", 8.7),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:L/VA:H/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H",
     "001200", 8.8),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:L/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H",
     "000200", 9.3),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:L/SC:N/SI:N/SA:N/E:A/CR:H/IR:M/AR:M",
     "000200", 9.3),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H/MAV:L",
     "100200", 8.6),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H/MUI:A",
     "100200", 8.6),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H/MAT:P",
     "010200", 9.2),
]
for vec, mv, want in OFFICIAL:
    sel = parse_vector(vec)
    eq(macrovector(sel), mv, "[官方] 宏向量 ...%s" % vec[-44:])
    close(score_vector(vec), want, "[官方] 分数 ...%s" % vec[-44:])

# ---- 2. 短路：全部影响指标为 N 直接 0 分 ----
shortcut = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H"
eq(score_vector(shortcut), 0.0, "全 N 影响 -> 0.0（官方 Exception for no impact）")
notshort = shortcut.replace("VA:N", "VA:L")
ok(score_vector(notshort) > 0.0, "负控：VA:L 后不再短路")

# ---- 3. 默认值规则（cvss_score.js 的 m()）----
base = parse_vector("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N")
eq(resolve(base, "E"), "A", "E:X 默认 A（未定义取最坏）")
eq(resolve(base, "CR"), "H", "CR:X 默认 H")
eq(resolve(base, "IR"), "H", "IR:X 默认 H")
eq(resolve(base, "AR"), "H", "AR:X 默认 H")
eq(_eq5(base), 0, "E 未定义时 EQ5=0")
eq(_eq6(base), 0, "CR 未定义 + VC:H -> EQ6=0")
eq(resolve(base, "AV"), "N", "无 MAV 时基础值原样")
mod = parse_vector("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/MAV:L")
eq(resolve(mod, "AV"), "L", "MAV:L 覆盖 AV:N")
eq(macrovector(mod)[0], "1", "MAV:L 把 EQ1 从 0 拉到 1")

# ---- 4. EQ 判定的边界（规范 Table 24-30）----
HEAD = "CVSS:4.0/AV:%s/AC:L/AT:N/PR:%s/UI:%s/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"


def mv_of(vec):
    return macrovector(parse_vector(vec))


eq(mv_of(HEAD % ("N", "N", "N"))[0], "0", "EQ1=0：AV:N 且 PR:N 且 UI:N")
eq(mv_of(HEAD % ("A", "N", "N"))[0], "1", "EQ1=1：AV 退到 A")
eq(mv_of(HEAD % ("N", "L", "N"))[0], "1", "EQ1=1：PR 退到 L")
eq(mv_of(HEAD % ("N", "N", "P"))[0], "1", "EQ1=1：UI 退到 P")
eq(mv_of(HEAD % ("P", "N", "N"))[0], "2", "EQ1=2：AV:P 直接坠到 2（不看 PR/UI）")
eq(mv_of(HEAD % ("A", "L", "P"))[0], "2", "EQ1=2：三个都不是最优")

TAIL = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N%s"
eq(mv_of(TAIL % "")[3], "2", "EQ4=2：无后续系统影响")
eq(mv_of(TAIL % "/MSI:S")[3], "0", "EQ4=0：MSI:S")
eq(mv_of(TAIL % "/MSA:S")[3], "0", "EQ4=0：MSA:S")
eq(mv_of(TAIL % "/MSI:H")[3], "1", "MSI:H 覆盖 SI:N 后 EQ4 升到 1（修正指标能抬高后续影响）")
eq(mv_of("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H"
         "/MSC:L/MSI:L/MSA:L")[3], "2", "MSC/MSI/MSA 全压到 L 后 EQ4 从 1 回落到 2")
eq(mv_of("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H")[3], "1",
   "EQ4=1：无 MSI:S/MSA:S 但有 H（无修正指标时 EQ4 到不了 0）")
ok("S" not in SC_LEVELS, "SC 的层级表不含 S")
ok("S" in SI_LEVELS, "SI 的层级表含 S")

# ---- 5. EQ3=2 时影响 + 需求整体被忽略 ----
low_a = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:L/VI:L/VA:L/SC:N/SI:N/SA:N/E:A/CR:H/IR:H/AR:H"
low_b = low_a.replace("CR:H", "CR:M").replace("IR:H", "IR:M").replace("AR:H", "AR:M")
eq(macrovector(parse_vector(low_a))[2], "2", "EQ3=2：VC/VI/VA 都不是 H")
eq(score_vector(low_a), score_vector(low_b),
   "EQ3=2 时改 CR/IR/AR 分数不变（下一级 (3,2) 不存在，该项被忽略）")
high_a = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/CR:H/IR:H/AR:H"
high_b = high_a.replace("CR:H", "CR:M").replace("IR:H", "IR:M").replace("AR:H", "AR:M")
eq(macrovector(parse_vector(high_a))[2], "0", "负控：EQ3=0")
ok(score_vector(high_a) != score_vector(high_b), "负控：EQ3=0 时改 CR/IR/AR 分数会变")

# ---- 6. EQ5 只换宏向量，不参与插值 ----
EBASE = ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H"
         "/MSI:S/CR:H/IR:H/AR:H/E:%s")
sa_, sp_, su_ = (score_vector(EBASE % x) for x in ("A", "P", "U"))
ok(sa_ > sp_ > su_, "E:A > E:P > E:U")
eq((sa_, sp_, su_), (10.0, 9.8, 9.5), "E 三档只改宏向量查表值")

# ---- 7. 查表覆盖与不可能组合 ----
eq(len(LOOKUP), 270, "官方查表 270 条")
ok("002000" not in LOOKUP, "EQ3=2 且 EQ6=0 在规范里标注 Cannot exist")
eq(LOOKUP["000000"], 10, "000000 -> 10")
eq(LOOKUP["212221"], 0.1, "212221 -> 0.1（最低档）")

# ---- 8. 解析校验 ----
raises(lambda: parse_vector("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"), "前缀必须是 4.0")
raises(lambda: parse_vector("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N"),
       "基础指标 SA 缺失")
raises(lambda: parse_vector("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:S/SI:N/SA:N"),
       "SC 没有 S 取值")
raises(lambda: parse_vector("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:Q"),
       "SA:Q 非法")
raises(lambda: parse_vector(
    "CVSS:4.0/AV:N/AV:L/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"), "指标重复")
raises(lambda: parse_vector("CVSS:4.0/AVN"), "片段缺冒号")
raises(lambda: parse_vector("CVSS:4.0/XX:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
       "未知指标")
with_supp = ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
             "/S:N/AU:Y/R:I/V:C/RE:H/U:Green")
without_supp = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
eq(score_vector(with_supp), score_vector(without_supp), "补充指标组不参与评分")

# ---- 9. 定性分级（计算器 qualScore 的边界）----
for value, rating in ((0.0, "None"), (0.1, "Low"), (3.9, "Low"), (4.0, "Medium"),
                      (6.9, "Medium"), (7.0, "High"), (8.9, "High"), (9.0, "Critical"),
                      (10.0, "Critical")):
    eq(severity(value), rating, "%s -> %s" % (value, rating))

# ---- 10. 插值前提：选出的最高严重度向量必须支配当前向量 ----
random.seed(7)
dominated = 0
for _ in range(300):
    sel = {
        "AV": random.choice(["N", "A", "L", "P"]), "AC": random.choice(["L", "H"]),
        "AT": random.choice(["N", "P"]), "PR": random.choice(["N", "L", "H"]),
        "UI": random.choice(["N", "P", "A"]),
        "VC": random.choice(["H", "L", "N"]), "VI": random.choice(["H", "L", "N"]),
        "VA": random.choice(["H", "L", "N"]), "SC": random.choice(["H", "L", "N"]),
        "SI": random.choice(["H", "L", "N"]), "SA": random.choice(["H", "L", "N"]),
        "E": "X", "CR": "X", "IR": "X", "AR": "X",
    }
    mv = macrovector(sel)
    if mv not in LOOKUP:
        continue
    cand = _pick_max_vector(sel, mv)
    good = True
    for name in LEVELS:
        want = metric_of(cand, name)
        if want is None:
            continue
        if LEVELS[name][resolve(sel, name)] - LEVELS[name][want] < -1e-12:
            good = False
    ok(good, "支配性：最高严重度向量不劣于当前向量")
    dominated += 1
ok(dominated >= 200, "支配性抽查覆盖 %d 个向量" % dominated)

# ---- 11. 分数值域与一位小数 ----
for _ in range(200):
    sel = {
        "AV": random.choice(["N", "A", "L", "P"]), "AC": random.choice(["L", "H"]),
        "AT": random.choice(["N", "P"]), "PR": random.choice(["N", "L", "H"]),
        "UI": random.choice(["N", "P", "A"]),
        "VC": random.choice(["H", "L", "N"]), "VI": random.choice(["H", "L", "N"]),
        "VA": random.choice(["H", "L", "N"]), "SC": random.choice(["H", "L", "N"]),
        "SI": random.choice(["H", "L", "N"]), "SA": random.choice(["H", "L", "N"]),
        "E": random.choice(["X", "A", "P", "U"]),
        "CR": random.choice(["X", "H", "M", "L"]), "IR": random.choice(["X", "H", "M", "L"]),
        "AR": random.choice(["X", "H", "M", "L"]),
    }
    s = score(sel)
    ok(0.0 <= s <= 10.0, "分数落在 [0,10]")
    ok(abs(s * 10 - round(s * 10)) < 1e-9, "分数是一位小数")

print("cvss selfcheck: %d assertions passed" % PASS)
