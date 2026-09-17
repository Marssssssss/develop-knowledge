"""自检(SemVer 侧): 解析、优先级比较、递增规则的语义断言。

行为与例子全部来自 semver.org 规范原文与 FAQ(见 README「参考资料」)。
运行: python semver_check.py（OCI 侧断言由本文件一并调用）
"""

from __future__ import annotations

from harness import check, raises, report
from semver2 import SEMVER_RE, SemverError, Version, highest, sort_versions


def test_parse():
    print("[1] 解析与合法性")
    check("基本形式", str(Version.parse("1.2.3")) == "1.2.3")
    check("预发布", str(Version.parse("1.0.0-alpha.1")) == "1.0.0-alpha.1")
    check("构建元数据", str(Version.parse("1.0.0-beta+exp.sha.5114f85")) ==
          "1.0.0-beta+exp.sha.5114f85")
    check("官方例子 1.0.0-x-y-z.--(标识符可含连字符)",
          str(Version.parse("1.0.0-x-y-z.--")) == "1.0.0-x-y-z.--")
    check("官方例子 1.0.0-0.3.7", Version.parse("1.0.0-0.3.7").prerelease == ("0", "3", "7"))

    check("前导零非法: 01.2.3", raises(Version.parse, "01.2.3"))
    check("前导零非法: 1.02.3", raises(Version.parse, "1.02.3"))
    check("预发布里数字标识符前导零非法: 1.2.3-01", raises(Version.parse, "1.2.3-01"))
    check("缺段非法: 1.2", raises(Version.parse, "1.2"))
    check("多段非法: 1.2.3.4", raises(Version.parse, "1.2.3.4"))
    check("空标识符非法: 1.2.3-alpha..1", raises(Version.parse, "1.2.3-alpha..1"))
    check("空标识符非法: 1.2.3+build..x", raises(Version.parse, "1.2.3+build..x"))
    check("FAQ: v1.2.3 不是语义化版本", raises(Version.parse, "v1.2.3"))
    check("FAQ: =1.2.3 不是语义化版本", raises(Version.parse, "=1.2.3"))
    check("非字符串非法", raises(Version.parse, 123))
    check("官方正则能匹配 1.9.0 这类普通版本", bool(SEMVER_RE.match("1.9.0")))


def test_precedence():
    print("[2] 优先级比较")

    def lt(a, b):
        return Version.parse(a).compare(Version.parse(b)) < 0

    check("官方例子: 1.0.0 < 2.0.0 < 2.1.0 < 2.1.1",
          lt("1.0.0", "2.0.0") and lt("2.0.0", "2.1.0") and lt("2.1.0", "2.1.1"))
    check("官方例子: 1.0.0-alpha < 1.0.0(预发布低于正规版本)", lt("1.0.0-alpha", "1.0.0"))
    chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
             "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
    check("官方例子整链升序排列",
          [str(v) for v in sort_versions(list(reversed(chain)))] == chain)
    check("beta.2 < beta.11: 数字标识符按数值比, 不是字典序",
          lt("1.0.0-beta.2", "1.0.0-beta.11"))
    check("alpha.1 < alpha.beta: 数字标识符恒低于非数字标识符",
          lt("1.0.0-alpha.1", "1.0.0-alpha.beta"))
    check("alpha < alpha.1: 前缀相等时标识符多的优先级更高",
          lt("1.0.0-alpha", "1.0.0-alpha.1"))
    check("rc.1 < rc.1.1: 同上", lt("1.0.0-rc.1", "1.0.0-rc.1.1"))
    check("含字母的标识符按 ASCII 字典序: beta < rc", lt("1.0.0-beta", "1.0.0-rc"))
    check("ASCII 序里大写字母小于小写: 1.0.0-Alpha < 1.0.0-alpha",
          lt("1.0.0-Alpha", "1.0.0-alpha"))
    check("构建元数据不参与优先级: 1.0.0+a == 1.0.0+b",
          Version.parse("1.0.0+a").compare(Version.parse("1.0.0+b")) == 0)
    check("只差构建元数据时相等(相等运算符)",
          Version.parse("1.0.0+001") == Version.parse("1.0.0+20130313144700"))
    check("只差构建元数据时哈希也相同",
          hash(Version.parse("1.0.0+a")) == hash(Version.parse("1.0.0+b")))
    check("预发布 + 构建元数据仍低于正规版本: 1.0.0-alpha+x < 1.0.0",
          lt("1.0.0-alpha+x", "1.0.0"))
    check("1.9.0 < 1.10.0 数值递增", lt("1.9.0", "1.10.0"))
    check("9.0.0 > 10.0.0 为假(数值而非字符串比较)", not lt("10.0.0", "9.0.0"))


def test_bump():
    print("[3] 递增规则")
    v = Version.parse("1.2.3-alpha.1+build.7")
    check("major 递增把 minor/patch 归零并清掉预发布",
          str(v.bump("major")) == "2.0.0")
    check("minor 递增把 patch 归零", str(Version.parse("1.2.3").bump("minor")) == "1.3.0")
    check("patch 递增", str(Version.parse("1.2.3").bump("patch")) == "1.2.4")
    check("官方例子: 1.9.0 -> 1.10.0",
          str(Version.parse("1.9.0").bump("minor")) == "1.10.0")
    check("预发布递增(本规范未规定, 本 demo 约定为下一个 patch 的 -0)",
          str(Version.parse("1.2.3-rc.1").bump("prerelease")) == "1.2.4-0")
    check("未知递增类型报错", raises(v.bump, "build"))

    check("highest 默认含预发布", str(highest(["1.2.0", "1.3.0-rc.1"])) == "1.3.0-rc.1")
    check("highest 排除预发布后取稳定版",
          str(highest(["1.2.0", "1.3.0-rc.1"], include_prerelease=False)) == "1.2.0")
    check("全部是预发布且被排除时返回 None",
          highest(["1.3.0-rc.1"], include_prerelease=False) is None)
    check("排序对构建元数据不敏感(相等则保持稳定)",
          [str(x) for x in sort_versions(["1.0.0+b", "1.0.0+a"])] == ["1.0.0+b", "1.0.0+a"])
    check("非法版本在排序时立即报错", raises(sort_versions, ["1.0.0", "x"]))


def main():
    print("SemVer 2.0.0 + OCI 制品语义自检")
    test_parse()
    test_precedence()
    test_bump()

    from oci_check import (test_digest, test_promotion, test_registry,
                           test_registry_discovery)
    test_digest()
    test_registry()
    test_registry_discovery()
    test_promotion()
    report()


if __name__ == "__main__":
    main()
