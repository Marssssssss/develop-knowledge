#!/usr/bin/env python3
"""demo572 自检：PURL 规范化、CycloneDX VEX 判定、SPDX 关系与跨格式关联。

    python selfcheck_sbom.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    Affect, Bom, Component, SpdxDoc, Vulnerability, cdx_to_spdx_relationships,
    cross_format_key, dep_closure, vers_matches, vex_status, _cmp,
)
from purl import build_purl, canonical_purl, parse_purl, pct_encode, purl_key  # noqa: E402

PASS = 0
FAILS = []


def ok(cond, msg):
    global PASS
    if cond:
        PASS += 1
    else:
        FAILS.append(msg)
        print("FAIL: %s" % msg)


# ==========================================================================
# P. PURL 规范化（purl-spec Clause 5 + how-to-build）
# ==========================================================================

# P1 type 大小写不敏感，规范形式小写
ok(canonical_purl("pkg:NPM/foo") == "pkg:npm/foo",
   "P1 pkg:NPM/foo -> pkg:npm/foo, 实际 %s" % canonical_purl("pkg:NPM/foo"))

# P2 qualifier 按 key=value 字符串字典序排序
ok(canonical_purl("pkg:npm/foo@1.0.0?b=2&a=1") == "pkg:npm/foo@1.0.0?a=1&b=2",
   "P2 qualifiers 应排序, 实际 %s" % canonical_purl("pkg:npm/foo@1.0.0?b=2&a=1"))

# P3 空值的 qualifier 等同于不存在
ok(canonical_purl("pkg:npm/foo?a=") == "pkg:npm/foo",
   "P3 空值 qualifier 应被丢弃, 实际 %s" % canonical_purl("pkg:npm/foo?a="))

# P4 qualifier 的 key 必须小写
ok(canonical_purl("pkg:npm/foo?A=1") == "pkg:npm/foo?a=1",
   "P4 key 应小写, 实际 %s" % canonical_purl("pkg:npm/foo?A=1"))

# P5 subpath 丢弃空段、. 与 ..
ok(canonical_purl("pkg:npm/foo#./a/../b/") == "pkg:npm/foo#a/b",
   "P5 subpath 应规范化, 实际 %s" % canonical_purl("pkg:npm/foo#./a/../b/"))

# P6 namespace/name 前后的 '/' 无意义
ok(canonical_purl("pkg:maven//org.apache/commons-io/") == "pkg:maven/org.apache/commons-io",
   "P6 多余斜杠应剥离, 实际 %s" % canonical_purl("pkg:maven//org.apache/commons-io/"))

# P7 scheme 与冒号后的若干 '/' 应被忽略
ok(canonical_purl("pkg://npm/foo") == "pkg:npm/foo",
   "P7 scheme 后斜杠应剥离, 实际 %s" % canonical_purl("pkg://npm/foo"))

# P8 version 是不透明字符串，不做归一化
ok(canonical_purl("pkg:pypi/Django@1.0") == "pkg:pypi/Django@1.0",
   "P8 version 不应被补成 1.0.0, 实际 %s" % canonical_purl("pkg:pypi/Django@1.0"))

# P9 空格必须编码成 %20
ok(pct_encode("my pkg") == "my" + "%20" + "pkg",
   "P9 空格应编码为 %" + "20, 实际 " + pct_encode("my pkg"))
ok(canonical_purl("pkg:npm/my pkg@1") == "pkg:npm/my" + "%20" + "pkg@1",
   "P9b 含空格的 name 重建后是 my%20pkg, 实际 " + canonical_purl("pkg:npm/my pkg@1"))

# P10 冒号无论何时都不编码
ok(pct_encode("a:b") == "a:b", "P10 冒号不编码, 实际 %s" % pct_encode("a:b"))

# P11 规范化幂等
for raw in ("pkg:NPM/Foo@1.0?b=2&a=1", "pkg:pypi/Django@1.0", "pkg://maven/org/artifact@2"):
    once = canonical_purl(raw)
    ok(canonical_purl(once) == once, "P11 幂等 %r -> %r" % (raw, canonical_purl(once)))

# P12 两种写法落到同一个跨格式主键
ok(purl_key("pkg:npm/foo@1.0.0?arch=x86") == purl_key("pkg:NPM/foo@1.0.0?arch=x86"),
   "P12 大写 type 应规范化到同一主键")

# P13 parse 与 build 往返
p = parse_purl("pkg:golang/google.golang.org/grpc@v1.60.0#a/b")
ok(build_purl(p) == "pkg:golang/google.golang.org/grpc@v1.60.0#a/b",
   "P13 往返稳定, 实际 %s" % build_purl(p))
ok(p.namespace == ["google.golang.org"] and p.name == "grpc" and p.version == "v1.60.0",
   "P13b 拆解正确, 实际 %r" % p)

# ==========================================================================
# M. 版本比较与 vers 区间
# ==========================================================================

# M1 版本号必须按数值段比较：字符串比较会给出相反结论
ok(_cmp("1.10", "1.9") == 1, "M1 1.10 > 1.9")
ok("1.10" < "1.9", "M1b 反证：字符串比较确实给出相反结论")

ok(vers_matches("vers:npm/>=1.0.0|<2.0.0", "1.9.9") is True, "M2 区间内为真")
ok(vers_matches("vers:npm/>=1.0.0|<2.0.0", "2.0.0") is False, "M3 上界是开区间")
ok(vers_matches("vers:npm/>=1.0.0|!=1.5.0", "1.5.0") is False, "M4 != 生效")
ok(vers_matches("vers:npm/>=1.0.0|!=1.5.0", "1.6.0") is True, "M4b != 之外仍为真")
ok(vers_matches("vers:npm/1.2.3", "1.2.3") is True, "M5 裸版本精确匹配")
ok(vers_matches("vers:npm/1.2.3", "1.2.4") is False, "M5b 裸版本只匹配自身")
try:
    vers_matches("1.2.3", "1.2.3")
    ok(False, "M6 非 vers 前缀应抛 ValueError")
except ValueError:
    ok(True, "M6 非 vers 前缀抛 ValueError")

# ==========================================================================
# V. CycloneDX VEX 判定
# ==========================================================================

libfoo = Component("c1", "library", "foo", "1.5.0", "pkg:npm/foo@1.5.0")

# V1 status 缺省为 affected
bom = Bom([libfoo], vulnerabilities=[
    Vulnerability("CVE-1", [Affect("c1", [{"range": "vers:npm/>=1.0.0|<2.0.0"}])]),
])
ok(vex_status(bom, "c1", "1.5.0") == "affected",
   "V1 status 缺省为 affected, 实际 %s" % vex_status(bom, "c1", "1.5.0"))

# V2 显式 unaffected
bom2 = Bom([libfoo], vulnerabilities=[
    Vulnerability("CVE-1", [Affect("c1", [{"version": "1.5.0", "status": "unaffected"}])],
                  state="not_affected", justification="code_not_reachable"),
])
ok(vex_status(bom2, "c1", "1.5.0") == "unaffected",
   "V2 显式 unaffected, 实际 %s" % vex_status(bom2, "c1", "1.5.0"))

# V3 没有任何匹配条目 → unknown
ok(vex_status(bom2, "c1", "9.9.9") == "unknown", "V3 无匹配返回 unknown")
ok(vex_status(bom2, "c-other", "1.5.0") == "unknown", "V3b ref 不命中返回 unknown")

# V4 首个匹配的条目胜出（顺序敏感）
bom3 = Bom([libfoo], vulnerabilities=[
    Vulnerability("CVE-1", [Affect("c1", [
        {"range": "vers:npm/>=1.0.0|<2.0.0", "status": "affected"},
        {"version": "1.5.0", "status": "unaffected"},
    ])]),
])
ok(vex_status(bom3, "c1", "1.5.0") == "affected",
   "V4 首个匹配胜出, 实际 %s" % vex_status(bom3, "c1", "1.5.0"))

# V5/V6/V7/V8 schema 校验
bad_state = Bom([libfoo], vulnerabilities=[Vulnerability("CVE-2", [], state="fixed")])
ok(any("state" in e for e in bad_state.validate()), "V5 非法 analysis.state 应被校验拦下")

good_state = Bom([libfoo], vulnerabilities=[
    Vulnerability("CVE-2", [], state="not_affected", justification="code_not_reachable")])
ok(good_state.validate() == [], "V6 not_affected + code_not_reachable 合法, 实际 %s" % good_state.validate())

bad_just = Bom([libfoo], vulnerabilities=[
    Vulnerability("CVE-3", [], state="not_affected", justification="unused")])
ok(any("justification" in e for e in bad_just.validate()), "V7 非法 justification 应被拦下")

bad_vers = Bom([libfoo], vulnerabilities=[
    Vulnerability("CVE-4", [Affect("c1", [{"version": "1.0", "range": "vers:npm/1.0"}])])])
ok(any("version 或 range" in e for e in bad_vers.validate()), "V8 version/range 必须恰好一个")

# V9 component 的 required 只有 type 与 name
ok(Bom([Component("c9", "library", "foo")]).validate() == [], "V9 只给 type+name 也合法")
ok(any("required" in e for e in Bom([Component("c10", "", "foo")]).validate()),
   "V9b 缺 type 应被拦下")

# ==========================================================================
# S. SPDX 关系模型
# ==========================================================================

doc = SpdxDoc("SPDXRef-DOCUMENT",
              {"SPDXRef-A": "pkg:npm/a@1", "SPDXRef-B": "pkg:npm/b@1"},
              [("SPDXRef-DOCUMENT", "DESCRIBES", "SPDXRef-A"),
               ("SPDXRef-A", "DEPENDS_ON", "SPDXRef-B")])
ok(doc.validate() == [], "S1 含 DESCRIBES 的文档合法, 实际 %s" % doc.validate())

doc2 = SpdxDoc("SPDXRef-DOCUMENT",
               {"SPDXRef-A": "pkg:npm/a@1", "SPDXRef-B": "pkg:npm/b@1"},
               [("SPDXRef-A", "DEPENDS_ON", "SPDXRef-B")])
ok(any("DESCRIBES" in e for e in doc2.validate()),
   "S2 多于一个 package 且无 DESCRIBES 应报错")

# S3 DEPENDENCY_OF 与 DEPENDS_ON 互逆
doc3 = SpdxDoc("SPDXRef-DOCUMENT", {"SPDXRef-A": "", "SPDXRef-B": ""},
               [("SPDXRef-A", "DEPENDENCY_OF", "SPDXRef-B")])
ok(doc3.depends_on() == [("SPDXRef-B", "SPDXRef-A")],
   "S3 A DEPENDENCY_OF B ⇔ B DEPENDS_ON A, 实际 %s" % doc3.depends_on())

# S4 SPDXID 前缀
ok(any("SPDXRef-" in e for e in SpdxDoc("DOC", {}, []).validate()),
   "S4 document SPDXID 必须以 SPDXRef- 开头")

# ==========================================================================
# X. 跨格式关联与依赖图
# ==========================================================================

ok(cross_format_key("pkg:npm/foo@1.0.0", "pkg:NPM/foo@1.0.0") is True,
   "X1 同一组件的两种写法可对齐")
ok(cross_format_key("pkg:npm/foo@1.0.0", "pkg:npm/foo@2.0.0") is False,
   "X1b 不同版本不应被当成同一组件")

# X2 CycloneDX dependencies -> SPDX DEPENDS_ON
graph = Bom([Component("a", "library", "a"), Component("b", "library", "b"),
             Component("c", "library", "c")],
            dependencies={"a": ["b"], "b": ["c"]})
ok(sorted(cdx_to_spdx_relationships(graph)) ==
   [("a", "DEPENDS_ON", "b"), ("b", "DEPENDS_ON", "c")],
   "X2 依赖图转 SPDX 边集, 实际 %s" % sorted(cdx_to_spdx_relationships(graph)))
ok(dep_closure(graph, "a") == ["b", "c"],
   "X2b 传递闭包, 实际 %s" % dep_closure(graph, "a"))

# X3 有环的图不会无限展开
cyc = Bom([Component("a", "library", "a"), Component("b", "library", "b")],
          dependencies={"a": ["b"], "b": ["a"]})
ok(sorted(dep_closure(cyc, "a")) == ["a", "b"],
   "X3 有环图的闭包应终止, 实际 %s" % dep_closure(cyc, "a"))

print("PASS=%d" % PASS)
if FAILS:
    print("FAILED=%d" % len(FAILS))
    sys.exit(1)
print("ALL OK")
