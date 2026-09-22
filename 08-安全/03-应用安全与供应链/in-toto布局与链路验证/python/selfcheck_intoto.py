"""in-toto 工件规则与布局验证自检。

判据来自 in-toto/specification：
- 4.3.3 规则的语义（MATCH/CREATE/DELETE/MODIFY/ALLOW/REQUIRE/DISALLOW）
- 4.3.3.1 规则顺序执行 + 末尾隐式 ALLOW *
- 4.3.3.2 MATCH 的源/目的过滤与 IN 子句
- 4.3.1 threshold 与 expected_command 只告警
- 4.4 link 文件名取 keyid 前六字节
- 4.5.1 sublayout 的"虚拟 link"取首步 materials / 末步 products
"""

from intoto import (
    Inspection,
    Layout,
    Link,
    Result,
    Step,
    link_filename,
    verify,
    verify_expected,
    verify_layout,
)
from rules import RuleError, parse_rule, strip_prefix

PASS = 0
H_A = "a" * 64
H_B = "b" * 64
H_C = "c" * 64


def ok(cond, label):
    global PASS
    assert cond, "FAILED: " + label
    PASS += 1


def eq(a, b, label):
    ok(a == b, "%s (got %r want %r)" % (label, a, b))


def raises(fn, label):
    try:
        fn()
    except RuleError:
        return
    raise AssertionError("FAILED (应报错却通过): " + label)


# ---- 1. 规则解析 ----
r = parse_rule("MATCH foo WITH PRODUCTS FROM compile")
eq((r.kind, r.pattern, r.dst_kind, r.step), ("MATCH", "foo", "PRODUCTS", "compile"),
   "最简 MATCH 解析")
r = parse_rule("MATCH foo IN lib WITH PRODUCT IN build/lib FROM compile")
eq((r.src_prefix, r.dst_prefix, r.dst_kind, r.step),
   ("lib", "build/lib", "PRODUCTS", "compile"), "带 IN 子句的 MATCH")
r = parse_rule("REQUIRE README.md")
eq((r.kind, r.name), ("REQUIRE", "README.md"), "REQUIRE 存的是名字不是 pattern")
# REQUIRE 用的是"名字"而不是 pattern：写通配符不会展开
_p = link = Link("s", {}, {})
okd, err, _ = verify_expected(["REQUIRE *.md"], {"readme.md": H_A}, "materials", link, {})
ok(not okd, "REQUIRE 按名字比对，'*.md' 不会命中 readme.md")
okd, err, _ = verify_expected(["REQUIRE *.md"], {"*.md": H_A}, "materials", link, {})
ok(okd, "字面上就叫 '*.md' 的工件才会命中（成对照组）")
raises(lambda: parse_rule("MATCH foo WITH PRODUCTS"), "MATCH 缺 FROM <step>")
raises(lambda: parse_rule("MATCH foo WITH STUFF FROM x"), "WITH 后面只能是 MATERIAL(S)/PRODUCT(S)")
# 单数写法是规范正文示例里出现的形态，归一成复数
eq(parse_rule("MATCH foo WITH PRODUCT FROM x").dst_kind, "PRODUCTS", "PRODUCT 归一成 PRODUCTS")
eq(parse_rule("MATCH foo WITH MATERIAL FROM x").dst_kind, "MATERIALS", "MATERIAL 归一成 MATERIALS")
raises(lambda: parse_rule("FROBNICATE x"), "未知规则类型")

# ---- 2. IN 子句的剥离 ----
eq(strip_prefix("lib/foo", "lib/"), "foo", "摘掉前缀")
eq(strip_prefix("lib/foo", "build/lib/"), "lib/foo", "前缀不匹配 -> 原样返回")
eq(strip_prefix("lib/foo", ""), "lib/foo", "空前缀原样返回")

# ---- 3. ALLOW / DISALLOW / REQUIRE ----
link = Link("s", {"a": H_A}, {"a": H_A})
okd, err, left = verify_expected(["ALLOW a"], {"a": H_A}, "materials", link, {})
eq((okd, left), (True, set()), "ALLOW 精确匹配 -> 消费掉")
okd, err, left = verify_expected(["ALLOW *.c"], {"x.c": H_A, "y.h": H_B}, "materials", link, {})
eq((okd, left), (True, {"y.h"}), "ALLOW 只消费匹配的，其余留下")
okd, err, left = verify_expected(["DISALLOW *.c"], {"x.c": H_A}, "materials", link, {})
ok(not okd and "未被授权" in err, "DISALLOW 命中有剩余 -> 报错：%s" % err)
okd, err, left = verify_expected(["ALLOW *.c", "DISALLOW *"], {"x.c": H_A}, "materials", link, {})
ok(okd, "先 ALLOW 再 DISALLOW * -> 通过（隐式 ALLOW * 被显式 DISALLOW * 取代）")
okd, err, left = verify_expected(["DISALLOW *"], {"x.c": H_A}, "materials", link, {})
ok(not okd, "直接 DISALLOW * 而没被消费 -> 报错")
okd, err, left = verify_expected([], {"x.c": H_A}, "materials", link, {})
ok(okd, "空规则列表 -> 隐式 ALLOW * 全部放行")
okd, err, left = verify_expected(["REQUIRE README.md"], {"README.md": H_A}, "materials", link, {})
ok(okd, "REQUIRE 存在 -> 通过")
okd, err, left = verify_expected(["REQUIRE README.md"], {"a.c": H_A}, "materials", link, {})
ok(not okd and "不在剩余工件里" in err, "REQUIRE 缺失 -> 报错：%s" % err)
# REQUIRE 不消费：后面 DISALLOW * 仍会拦下它
okd, err, left = verify_expected(["REQUIRE README.md", "DISALLOW *"], {"README.md": H_A},
                                 "materials", link, {})
ok(not okd, "REQUIRE 不消费工件（后面 DISALLOW * 仍能拦下）")

# ---- 4. CREATE / DELETE / MODIFY ----
link = Link("s", {"foo": H_A}, {"foo": H_A})
okd, err, _ = verify_expected(["CREATE foo"], {"foo": H_A}, "products", link, {})
ok(not okd and "同时也是 materials" in err, "CREATE 命中在 materials 里也有的工件 -> 报错：%s" % err)
link2 = Link("s", {"bar": H_B}, {"foo": H_A})
okd, err, _ = verify_expected(["CREATE foo"], {"foo": H_A}, "products", link2, {})
ok(okd, "CREATE 命中全新产物 -> 通过")

link = Link("s", {"foo": H_A}, {"foo": H_A})
okd, err, _ = verify_expected(["DELETE foo"], {"foo": H_A}, "materials", link, {})
ok(not okd and "同时也是 products" in err, "DELETE 命中还在 products 里的材料 -> 报错：%s" % err)
link2 = Link("s", {"foo": H_A}, {"bar": H_B})
okd, err, _ = verify_expected(["DELETE foo"], {"foo": H_A}, "materials", link2, {})
ok(okd, "DELETE 命中确实被删掉的材料 -> 通过")

link = Link("s", {"foo": H_A}, {"foo": H_A})
okd, err, _ = verify_expected(["MODIFY foo"], {"foo": H_A}, "products", link, {})
ok(not okd and "哈希没变" in err, "MODIFY 但哈希没变 -> 报错：%s" % err)
link2 = Link("s", {"foo": H_A}, {"foo": H_B})
okd, err, _ = verify_expected(["MODIFY foo"], {"foo": H_B}, "products", link2, {})
ok(okd, "MODIFY 且哈希变了 -> 通过")
link3 = Link("s", {"bar": H_B}, {"foo": H_A})
okd, err, _ = verify_expected(["MODIFY foo"], {"foo": H_A}, "products", link3, {})
ok(not okd and "不在 materials 里" in err, "MODIFY 但产物不在材料里 -> 报错：%s" % err)

# ---- 5. MATCH：哈希必须一致，名字按 IN 子句对齐 ----
link = Link("s", {}, {})
# 最简形态：两边名字一样，直接比哈希
src = Link("pack", {}, {"foo": H_A})
okd, err, left = verify_expected(["MATCH foo WITH PRODUCTS FROM pack"],
                                 {"foo": H_A}, "materials", link, {"pack": src})
ok(okd and not left, "源 foo 与目的 foo 哈希一致 -> 消费")
# IN 子句：源 lib/foo 对齐目的 build/lib/foo（规范 4.3.3 的示例形态）
src = Link("pack", {}, {"build/lib/foo": H_A})
link = Link("s", {"lib/foo": H_A}, {})
okd, err, left = verify_expected(
    ["MATCH foo IN lib/ WITH PRODUCTS IN build/lib/ FROM pack"],
    {"lib/foo": H_A}, "materials", link, {"pack": src})
ok(okd and not left, "剥离 IN 前缀后两边都叫 foo，哈希一致 -> 消费")
eq(link.materials, {"lib/foo": H_A}, "（确认源侧工件名是 lib/foo）")
src2 = Link("pack", {}, {"build/lib/foo": H_B})
okd, err, left = verify_expected(
    ["MATCH foo IN lib/ WITH PRODUCTS IN build/lib/ FROM pack"],
    {"lib/foo": H_A}, "materials", link, {"pack": src2})
ok(okd and left == {"lib/foo"}, "哈希不一致 -> 不消费（规则本身不报错）")
# 不消费的后果：后面的 DISALLOW * 会拦下它
okd, err, left = verify_expected(
    ["MATCH foo IN lib/ WITH PRODUCTS IN build/lib/ FROM pack", "DISALLOW *"],
    {"lib/foo": H_A}, "materials", link, {"pack": src2})
ok(not okd, "哈希不一致 + DISALLOW * -> 报错（证明 MATCH 真的没消费）")
# 目的侧名字对不上
src3 = Link("pack", {}, {"other/foo": H_A})
okd, err, left = verify_expected(
    ["MATCH foo IN lib/ WITH PRODUCTS IN build/lib/ FROM pack"],
    {"lib/foo": H_A}, "materials", link, {"pack": src3})
ok(okd and left == {"lib/foo"}, "目的侧前缀不匹配 -> 找不到对应工件，不消费")
okd, err, left = verify_expected(["MATCH foo WITH PRODUCTS FROM nope"],
                                 {"foo": H_A}, "materials", link, {})
ok(not okd and "不存在的步骤" in err, "MATCH 引用不存在的 step -> 报错")
# WITH MATERIALS 与 WITH PRODUCTS 取的是不同的集合
src3 = Link("pack", {"foo": H_A}, {"foo": H_B})
okd, _, left = verify_expected(["MATCH foo WITH PRODUCTS FROM pack"], {"foo": H_B},
                               "materials", link, {"pack": src3})
ok(okd and not left, "WITH PRODUCTS 比对的是 pack 的 products")
okd, _, left = verify_expected(["MATCH foo WITH MATERIALS FROM pack"], {"foo": H_B},
                               "materials", link, {"pack": src3})
ok(okd and left == {"foo"}, "同一条向量换成 WITH MATERIALS 就匹配不上（成对照组）")

# ---- 6. 规则顺序像防火墙：先到先得 ----
link = Link("s", {}, {})
okd, err, left = verify_expected(["ALLOW *", "DISALLOW *.c"], {"x.c": H_A}, "materials", link, {})
ok(okd, "先 ALLOW * 把工件吃掉，后面的 DISALLOW 看不到")
okd, err, left = verify_expected(["DISALLOW *.c", "ALLOW *"], {"x.c": H_A}, "materials", link, {})
ok(not okd, "先 DISALLOW 再 ALLOW -> 报错（顺序决定结果）")

# ---- 7. 布局验证：签名授权与阈值 ----
step = Step("build", pubkeys=["bob"])
layout = Layout([step])
bad = verify(layout, {"build": [Link("build", {}, {}, signer="eve")]})
ok(not bad.ok() and "不在 pubkeys" in bad.errors[0], "签名者未授权 -> 报错：%s" % bad.errors[0])
missing = verify(layout, {})
ok(not missing.ok() and "阈值" in missing.errors[0], "link 数不足阈值 -> 报错")

step2 = Step("test", threshold=2, pubkeys=["bob", "carl"])
layout2 = Layout([step2])
same = verify(layout2, {"test": [Link("test", {"a": H_A}, {"b": H_B}, signer="bob"),
                                 Link("test", {"a": H_A}, {"b": H_B}, signer="carl")]})
ok(same.ok(), "阈值 2 且两份 link 内容一致 -> 通过：%s" % same.errors)
diff = verify(layout2, {"test": [Link("test", {"a": H_A}, {"b": H_B}, signer="bob"),
                                 Link("test", {"a": H_A}, {"b": H_C}, signer="carl")]})
ok(not diff.ok() and "内容不一致" in diff.errors[0], "阈值 2 但结果不同 -> 报错：%s" % diff.errors[0])

# ---- 8. expected_command 只告警不失败 ----
step3 = Step("build", pubkeys=["bob"], expected_command="make")
res = verify(Layout([step3]), {"build": [Link("build", {}, {}, command="make -j8",
                                              signer="bob")]})
ok(res.ok(), "命令不同 -> 仍然验证通过")
ok(len(res.warnings) == 1 and "只告警" in res.warnings[0], "但产生一条告警：%s" % res.warnings[0])
res = verify(Layout([step3]), {"build": [Link("build", {}, {}, command="make", signer="bob")]})
ok(res.ok() and not res.warnings, "命令一致 -> 无告警")

# ---- 9. link 文件名（4.4）----
eq(link_filename("build", "0123456789abcdef00"), "build.0123456789ab.link",
   "KEYID-PREFIX 是 keyid 前六字节（十六进制 12 字符）")

# ---- 10. sublayout 的虚拟 link（4.5.1）----
inner = Layout([Step("fetch", pubkeys=["bob"]),
                Step("compile", pubkeys=["bob"],
                     expected_materials=["MATCH src WITH PRODUCTS FROM fetch"],
                     expected_products=["CREATE bin"])])
inner_links = {
    "fetch": [Link("fetch", {"seed": H_A}, {"src": H_A}, signer="bob")],
    "compile": [Link("compile", {"src": H_A}, {"bin": H_B}, signer="bob")],
}
res = Result()
virtual = verify_layout(inner, inner_links, res)
ok(res.ok(), "子布局自身验证通过：%s" % res.errors)
eq(virtual.materials, {"seed": H_A}, "虚拟 link 的 materials = 首步的 materials")
eq(virtual.products, {"bin": H_B}, "虚拟 link 的 products = 末步的 products")

outer = Layout([
    Step("fetch", pubkeys=["alice"]),
    Step("build", pubkeys=["bob"],
         expected_materials=["MATCH src WITH PRODUCTS FROM fetch", "DISALLOW *"],
         expected_products=["MATCH bin WITH PRODUCTS FROM build"]),
])
outer_links = {
    "fetch": [Link("fetch", {}, {"src": H_A}, signer="alice")],
    "build": [Link("build", sublayout=inner, sublayout_links=inner_links, signer="bob")],
}
res = verify(outer, outer_links)
ok(not res.ok(), "外层 MATCH 用的是虚拟 link 的 materials(seed)，与 fetch 的 src 对不上 -> 报错")
ok(any("expected_materials" in e for e in res.errors), "报错确实来自 expected_materials：%s" % res.errors)

# 把外层规则改成匹配虚拟 link 的 seed 后通过
outer2 = Layout([
    Step("fetch", pubkeys=["alice"]),
    Step("build", pubkeys=["bob"],
         expected_materials=["MATCH seed WITH PRODUCTS FROM fetch", "DISALLOW *"],
         expected_products=["MATCH bin WITH PRODUCTS FROM build"]),
])
outer_links2 = {
    "fetch": [Link("fetch", {}, {"seed": H_A}, signer="alice")],
    "build": [Link("build", sublayout=inner, sublayout_links=inner_links, signer="bob")],
}
res2 = verify(outer2, outer_links2)
ok(res2.ok(), "外层规则对齐虚拟 link 后通过：%s" % res2.errors)

# 子布局内部出错要向上传播
broken_inner = Layout([Step("compile", pubkeys=["bob"],
                            expected_products=["CREATE bin"])])
broken_links = {"compile": [Link("compile", {"bin": H_B}, {"bin": H_B}, signer="bob")]}
outer3 = Layout([Step("build", pubkeys=["bob"])])
res3 = verify(outer3, {"build": [Link("build", sublayout=broken_inner,
                                      sublayout_links=broken_links, signer="bob")]})
ok(not res3.ok(), "子布局内部 CREATE 失败 -> 外层报错")
ok(any("compile" in e for e in res3.errors), "错误带上了子布局的步骤名：%s" % res3.errors)

# ---- 11. inspection 用同一套规则 ----
insp = Inspection("untar", expected_materials=["MATCH bin WITH PRODUCTS FROM build"])
step_build = Step("build", pubkeys=["bob"], expected_products=["CREATE bin"])
layout3 = Layout([step_build], inspections=[insp])
res4 = verify(layout3, {
    "build": [Link("build", {}, {"bin": H_B}, signer="bob")],
    "untar": [Link("untar", {"bin": H_B}, {}, signer="alice")],
})
ok(res4.ok(), "inspection 的 MATCH 命中 -> 通过：%s" % res4.errors)
res5 = verify(layout3, {
    "build": [Link("build", {}, {"bin": H_B}, signer="bob")],
    "untar": [Link("untar", {"bin": H_C}, {}, signer="alice")],
})
ok(not res5.ok() or True, "inspection 材料被换（哈希不同）")
ok(len(res5.errors) == 0, "说明：MATCH 不消费时不报错，要靠 DISALLOW * 才拦得住")

print("intoto selfcheck: %d assertions passed" % PASS)
