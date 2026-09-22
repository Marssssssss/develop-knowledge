"""CVSS v4.0 演示：同一条漏洞在不同部署环境下的分数是怎么变的。

重点不是打分本身，而是说明 v4.0 与 v3.x 的根本差别：
分数不是闭式公式算出来的，而是「先看落在哪个等价类（MacroVector），再在类内插值」。
"""

from cvss import format_vector, macrovector, parse_vector, score_vector, severity
from cvssdata import LOOKUP

DEMOS = [
    ("网络可达、无需权限、全量影响",
     "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H"),
    ("同上，但威胁成熟度 E:U（还没被实际利用）",
     "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/E:U"),
    ("同上，但环境里后续系统完整性被打到 Safety（MSI:S）",
     "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/E:A/MSI:S"),
    ("只影响脆弱系统本身（无后续系统影响）",
     "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
    ("需要物理接触 + 高权限 + 需要用户交互",
     "CVSS:4.0/AV:P/AC:H/AT:P/PR:H/UI:A/VC:L/VI:L/VA:L/SC:L/SI:L/SA:L/E:U/CR:L/IR:L/AR:L"),
]


def show(title, vector):
    sel = parse_vector(vector)
    mv = macrovector(sel)
    s = score_vector(vector)
    print("  %s" % title)
    print("    宏向量 %s  查表值 %-4s  最终 %-4s  %s"
          % (mv, LOOKUP[mv], s, severity(s)))
    print("    %s" % format_vector(sel))


def demo():
    print("CVSS v4.0：宏向量查表 + 类内插值")
    for title, vector in DEMOS:
        show(title, vector)

    print()
    print("同一条漏洞，逐个降低「安全要求」CR/IR/AR 对分数的影响")
    base = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/CR:%s/IR:%s/AR:%s"
    for req in ("HHH", "MMM", "LLL"):
        v = base % tuple(req)
        print("  CR/IR/AR = %s -> %-4s (宏向量 %s)"
              % (req, score_vector(v), macrovector(parse_vector(v))))

    print()
    print("反例：当 VC/VI/VA 都不是 H（EQ3=2）时，CR/IR/AR 完全不起作用")
    low = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:L/VI:L/VA:L/SC:N/SI:N/SA:N/CR:%s/IR:%s/AR:%s"
    for req in ("HHH", "MMM", "LLL"):
        v = low % tuple(req)
        print("  CR/IR/AR = %s -> %-4s" % (req, score_vector(v)))

    print()
    print("环境修正能把「没有后续影响」的漏洞抬到 10.0")
    v = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/MSC:H/MSI:S/MSA:S"
    print("  基础 SC/SI/SA 全 N，加上 MSC:H/MSI:S/MSA:S 后 -> %s (宏向量 %s)"
          % (score_vector(v), macrovector(parse_vector(v))))


if __name__ == "__main__":
    demo()
