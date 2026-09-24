"""demo 684 演示入口：objc4 类布局与脏内存。

运行：python main.py
"""

from rwbits_bits import ClassDataBits
from rwbits_const import (FAST_HAS_DEFAULT_RR, FAST_IS_SWIFT_STABLE, ILP32,
                          LP64_IPHONE, RW_INITIALIZED, RW_REALIZED, addr_of)
from rwbits_model import (ClassRo, ClassRw, Item, ListOrListList, extAlloc,
                          extAllocIfNeeded, set_demangled_name)


def main():
    print("== 1. class_data_bits_t：指针与 3 个 FAST 标志挤在同一个字里 ==")
    ro = ClassRo(flags=0, name="MyClass")
    rw = ClassRw(LP64_IPHONE, ro, flags=RW_REALIZED)
    bits = ClassDataBits(LP64_IPHONE, disc=0x1000)
    bits.bits = bits.ptra.sign(addr_of(ro, LP64_IPHONE)
                               | FAST_IS_SWIFT_STABLE | FAST_HAS_DEFAULT_RR)
    print("  RO 态  bits = 0x%016x" % bits.load())
    print("    has_rw_pointer = %s" % bits.has_rw_pointer())
    print("    safe_ro()      = %s" % bits.safe_ro().name)
    bits.setData(rw)
    print("  RW 态  bits = 0x%016x" % bits.load())
    print("    has_rw_pointer = %s" % bits.has_rw_pointer())
    print("    data()         = class_rw_t(flags=0x%x)" % bits.data().flags)
    print("    保留下来的 FAST 标志 = 0x%x" % (bits.ptra.auth(bits.load()) & 7))

    print()
    print("== 2. 32 位没有 FAST_IS_RW_POINTER，判据退化成 RW_REALIZED ==")
    ro32 = ClassRo(flags=0, name="MyClass32")
    for flags, label in ((RW_REALIZED, "带 RW_REALIZED"), (0, "不带")):
        rw32 = ClassRw(ILP32, ro32, flags=flags)
        b32 = ClassDataBits(ILP32, disc=0x2000)
        b32.setData(rw32)
        print("  %-12s -> has_rw_pointer = %-5s  safe_ro() = %s"
              % (label, b32.has_rw_pointer(),
                 type(b32.safe_ro()).__name__))

    print()
    print("== 3. extAlloc：脏内存只在需要改类时才分配 ==")
    m0, m1, m2 = Item("base0"), Item("base1"), Item("base2")
    p0, p1 = Item("prop0"), Item("prop1")
    ro3 = ClassRo(flags=0, name="Dirty",
                  baseMethods=ListOrListList("rel", [m0, m1, m2]),
                  baseProperties=ListOrListList("rel", [p0, p1]))
    rw3 = ClassRw(LP64_IPHONE, ro3)
    print("  分配前 ext() = %r" % rw3.ext())
    print("  methods() = %s" % [x.tag for x in rw3.methods().lists()])
    for deep in (False, True):
        rw = ClassRw(LP64_IPHONE, ro3)
        rwe = extAlloc(rw, rw.ro(), deep=deep)
        print("  deep=%-5s version=%d methods=%s properties=%s"
              % (deep, rwe.version,
                 [x.tag + ("'" if x.is_dup else "")
                  for x in rwe.methods.lists()],
                 [x.tag + ("'" if x.is_dup else "")
                  for x in rwe.properties.lists()]))

    print()
    print("== 4. rwe 分配后列表存储形态与后续 attach 的排布 ==")
    rw4 = ClassRw(LP64_IPHONE, ro3)
    e = extAllocIfNeeded(rw4)
    print("  ext() 幂等 = %s" % (extAllocIfNeeded(rw4) is e))
    e.methods.attachLists([Item("cat0")])
    print("  挂上一个分类后 methods = %s"
          % [x.tag for x in rw4.methods().lists()])
    print("  解修饰名 CAS 首次 = %s，再次 = %s"
          % (set_demangled_name(e, "MyClass", "_TtC7MyClass")[0],
             set_demangled_name(e, "Other", "_TtC7MyClass")[0]))
    rw4.changeFlags(RW_INITIALIZED, 0)
    print("  changeFlags 后 flags = 0x%x" % rw4.flags)


if __name__ == "__main__":
    main()
