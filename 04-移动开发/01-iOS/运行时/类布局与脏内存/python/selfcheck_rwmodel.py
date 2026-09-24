"""demo 684 自检（后半：模型侧）。

每条断言对照 objc4 `runtime/objc-runtime-new.h` / `runtime/objc-runtime-new.mm`
/ `runtime/PointerUnion.h` 的原文手算期望值，由 selfcheck_rwbits.py 一并跑。
"""

from rwbits_bits import AuthFailure, ClassDataBits, Violation
from rwbits_check import ok, raises, section
from rwbits_const import (LP64_IPHONE, RO_META, RW_INITIALIZED, RW_REALIZED,
                          RW_REALIZING, addr_of)
from rwbits_lists import Item, ListArray, ListOrListList
from rwbits_model import (ClassRo, ClassRw, ClassRwExt, extAlloc,
                          extAllocIfNeeded, set_demangled_name)


# ============================================================
# 7. extAlloc：version 与深拷贝不对称
# ============================================================
ro_a = ClassRo(flags=0, name="Shared")
section("7 extAlloc")
ro_m = ClassRo(flags=RO_META, name="Meta")
rw_m = ClassRw(LP64_IPHONE, ro_m)
ok(extAlloc(rw_m, ro_m).version == 7, "元类的 rwe->version 是 7")
ro_n = ClassRo(flags=0, name="Plain")
rw_n = ClassRw(LP64_IPHONE, ro_n)
ok(extAlloc(rw_n, ro_n).version == 0, "非元类的 rwe->version 是 0")

m0, m1, m2 = Item("m0"), Item("m1"), Item("m2")
p0, p1 = Item("p0"), Item("p1")
q0, q1 = Item("q0"), Item("q1")


def make_ro(flags=0, deep_rel=True):
    return ClassRo(flags=flags, name="X",
                   baseMethods=ListOrListList("rel", [m0, m1, m2]) if deep_rel
                   else ListOrListList("list", m0),
                   baseProperties=ListOrListList("rel", [p0, p1]),
                   baseProtocols=ListOrListList("rel", [q0, q1]))


rw_sh = ClassRw(LP64_IPHONE, make_ro())
rwe_sh = extAlloc(rw_sh, rw_sh.ro(), deep=False)
ok(rwe_sh.methods.lists() == (m0, m1, m2), "浅拷贝相对列表保持原顺序")
ok(all(a is b for a, b in zip(rwe_sh.methods.lists(), (m0, m1, m2))),
   "浅拷贝不复制列表对象")

rw_dp = ClassRw(LP64_IPHONE, make_ro())
rwe_dp = extAlloc(rw_dp, rw_dp.ro(), deep=True)
ok([x.tag for x in rwe_dp.methods.lists()] == ["m2", "m1", "m0"],
   "深拷贝相对列表会逐个 attachLists —— 顺序被反转")
ok(rwe_sh.methods.kind == "rel" and rwe_dp.methods.kind == "array",
   "浅拷贝存的是相对列表，深拷后变成 array_t")
ok(all(x.is_dup for x in rwe_dp.methods.lists()), "深拷贝出来的每个列表都是副本")
ok(all(a is not b for a, b in zip(rwe_dp.methods.lists(), (m0, m1, m2))),
   "深拷贝出来的对象与原对象不是同一个")

ok(rwe_dp.properties.lists() == (p0, p1),
   "property 列表历史上不深拷贝 —— 顺序也不反转")
ok(all(a is b for a, b in zip(rwe_dp.properties.lists(), (p0, p1))),
   "property 列表深拷贝时仍是原对象")
ok(rwe_dp.protocols.lists() == (q0, q1) and
   all(a is b for a, b in zip(rwe_dp.protocols.lists(), (q0, q1))),
   "protocol 列表同样既不复制也不反转")

rw_lst = ClassRw(LP64_IPHONE, make_ro(deep_rel=False))
rwe_lst = extAlloc(rw_lst, rw_lst.ro(), deep=True)
ok(len(rwe_lst.methods.lists()) == 1 and rwe_lst.methods.lists()[0].is_dup,
   "单个 method_list_t 深拷时确实 duplicate")
rw_lst2 = ClassRw(LP64_IPHONE, make_ro(deep_rel=False))
rwe_lst2 = extAlloc(rw_lst2, rw_lst2.ro(), deep=False)
ok(rwe_lst2.methods.lists()[0] is m0, "不深拷时直接沿用原 list")

# ============================================================
# 8. extAllocIfNeeded 幂等
# ============================================================
section("8 extAllocIfNeeded")
ro8 = ClassRo(flags=0, name="Z", baseMethods=ListOrListList("list", m0))
rw8 = ClassRw(LP64_IPHONE, ro8)
ok(rw8.ext() is None, "未分配前 ext() 为 None")
before = rw8.methods().lists()
e1 = extAllocIfNeeded(rw8)
e2 = extAllocIfNeeded(rw8)
ok(e1 is e2, "第二次 extAllocIfNeeded 返回同一个 rwe")
ok(rw8.ext() is e1, "ext() 能取回它")
ok(rw8.methods().lists() == before, "分配 rwe 后方法列表内容不变")
ok(rw8.ro() is ro8, "ro() 仍是原来的 class_ro_t")

# ============================================================
# 9. attachLists 排序
# ============================================================
section("9 list_array_tt")
la = ListArray()
la.attachLists([])
ok(la.kind == "null", "addedCount == 0 时直接 return")
la.attachLists([m0])
ok(la.kind == "list" and la.lists() == (m0,), "0 -> 1 直接存单个 list")
la.attachLists([m1])
ok(la.lists() == (m1, m0), "1 -> many：老的后移，新的排前")
la.attachLists([Item("c"), Item("d")])
ok(la.kind == "array" and len(la.lists()) == 4, "many -> many 变成 array_t")
ok(la.lists()[2] is m1 and la.lists()[3] is m0, "many -> many：老的整块后移 addedCount")
ok([x.tag for x in la.lists()] == ["c", "d", "m1", "m0"], "新的排在最前")
raises(Violation, lambda: la.attachLists([m1]), "同一个 list 挂两次触发 ASSERT")
lc = ListArray.from_rel([m0, m1, m2])
lc.attachLists([Item("x")])
ok([x.tag for x in lc.lists()] == ["x", "m0", "m1", "m2"],
   "rel -> many：新增在前，原有相对列表在后")
ld = ListArray.from_rel([m0, m1, m2])
raises(Violation, lambda: ld.attachListList([m1]),
       "attachListList 只能挂到空存储")
ld.copyListList(2)
ok(ld.kind == "array" and [x.tag for x in ld.lists()] == ["m0", "m1"],
   "copyListList(2) 只取前 2 个")
le = ListArray.from_rel([m0, m1, m2])
le.copyListList(1)
ok(le.kind == "list" and le.lists() == (m0,), "copyListList(1) 退化为单个 list")

# ============================================================
# 10. methodAlternates 三态
# ============================================================
section("10 methodAlternates")
ro10 = ClassRo(flags=0, name="W", baseMethods=ListOrListList("list", m0))
rw10 = ClassRw(LP64_IPHONE, ro10)
alt = rw10.methodAlternates()
ok(alt["array"] is None and alt["list"] is m0 and alt["relativeList"] is None,
   "RO 态单 list：list 分支命中")
ro10b = ClassRo(flags=0, name="W2",
                baseMethods=ListOrListList("rel", [m0, m1]))
rw10b = ClassRw(LP64_IPHONE, ro10b)
altb = rw10b.methodAlternates()
ok(altb["list"] is None and altb["relativeList"] == [m0, m1],
   "RO 态相对列表：relativeList 分支命中")
altc = rw8.methodAlternates()
ok(altc["array"] is e1.methods and altc["list"] is None
   and altc["relativeList"] is None, "有 rwe 时只有 array 分支命中")
ok(rw10.methods() is not rw10.methods(), "methods() 按值返回，每次都是新副本")
ok(rw10.methods().lists() == rw10.methods().lists(), "但两次内容一致")

# ============================================================
# 11. demangledName 的 CAS
# ============================================================
section("11 demangledName")
rwe11 = ClassRwExt()
ok(set_demangled_name(rwe11, "Demangled", "Mangled") == (True, False),
   "首次写入成功")
ok(rwe11.demangledName == "Demangled", "写入的是解修饰后的名字")
ok(set_demangled_name(rwe11, "Other", "Mangled") == (False, True),
   "再次写入失败，且调用方要 free(de)")
ok(rwe11.demangledName == "Demangled", "失败时名字不变")
rwe12 = ClassRwExt()
set_demangled_name(rwe12, None, "Mangled")
ok(rwe12.demangledName == "Mangled", "de 为空时用 demangledName 兜底为原名")

# ============================================================
# 12. changeFlags / setFlags / clearFlags
# ============================================================
section("12 flags 原子操作")
rw12 = ClassRw(LP64_IPHONE, ro_a, flags=RW_REALIZING)
raises(Violation, lambda: rw12.changeFlags(RW_INITIALIZED, RW_INITIALIZED),
       "set 与 clear 重叠触发 ASSERT")
rw12.changeFlags(RW_INITIALIZED, RW_REALIZING)
ok(rw12.flags == RW_INITIALIZED, "changeFlags 结果 = (old|set) & ~clear")
rw13 = ClassRw(LP64_IPHONE, ro_a, flags=0)
rw13.cas_failures = 1
rw13.changeFlags(RW_INITIALIZED, 0)
ok(rw13.cas_attempts == 2, "CAS 失败一次后重试，共 2 次")
ok(rw13.flags == (RW_INITIALIZED | 1),
   "重试时用新的 old 重算，把并发写进来的 bit0 一起保留")
rw14 = ClassRw(LP64_IPHONE, ro_a, flags=0b1010)
rw14.setFlags(0b0101)
ok(rw14.flags == 0b1111, "setFlags 是原子 fetch_or")
rw14.clearFlags(0b0011)
ok(rw14.flags == 0b1100, "clearFlags 是原子 fetch_and(~clear)")

# ============================================================
# 13. copyRWFrom / copyROFrom：换判别子重新签名
# ============================================================
section("13 copy 与重签名")
rw13b = ClassRw(LP64_IPHONE, ro_a, flags=RW_REALIZED)
b13a = ClassDataBits(LP64_IPHONE, disc=0x7771)
b13b = ClassDataBits(LP64_IPHONE, disc=0x7772)
b13a.setData(rw13b)
raw13 = b13a.ptra.auth(b13a.load())
b13b.copyRWFrom(b13a)
ok(b13b.ptra.auth(b13b.load()) == raw13, "copyRWFrom 搬的是同一个裸指针")
ok(b13b.load() != b13a.load(), "但判别子不同，签出来的字不同")
raises(AuthFailure, lambda: b13a.ptra.auth(b13b.load()),
       "用旧判别子去解新字会验签失败")
ro13 = ClassRo(flags=0, name="C1")
b13b.bits = b13b.ptra.sign(addr_of(ro13, LP64_IPHONE))
b13b.copyROFrom(b13a, authenticate=False)
ok(b13b.load() == b13a.load(), "copyROFrom 不验签时原样搬运")
b13b.bits = b13b.ptra.sign(addr_of(ro13, LP64_IPHONE))   # 复位到一个 flags 为 0 的 ro
b13b.copyROFrom(b13a, authenticate=True)
ok(b13b.ptra.auth(b13b.load()) == raw13, "验签时用新判别子重新签名")
ok(b13b.load() != b13a.load(), "重签后与源字不同")
ro13r = ClassRo(flags=RW_REALIZED, name="C2")
b13b.bits = b13b.ptra.sign(addr_of(ro13r, LP64_IPHONE))
raises(Violation, lambda: b13b.copyROFrom(b13a, True),
       "copyROFrom 要求目标的 RW_REALIZED 未置位")
