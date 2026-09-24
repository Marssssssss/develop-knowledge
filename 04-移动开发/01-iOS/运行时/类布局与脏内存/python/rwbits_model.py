"""class_rw_t / class_rw_ext_t / class_ro_t 与 list_array_tt。

对照 objc4 `runtime/objc-runtime-new.h` 2195-2360 行（class_rw_ext_t、
class_rw_t、methodAlternates）与 `runtime/objc-runtime-new.mm` 1581-1625 行
（class_rw_t::extAlloc），以及 `runtime/PointerUnion.h` 70-165 行。
"""

from rwbits_bits import Violation
from rwbits_const import (RO_META, PtrAuth, addr_of, deref)


from rwbits_lists import Item, ListArray, ListOrListList


class ClassRo:
    def __init__(self, flags=0, name="", baseMethods=None, baseProperties=None,
                 baseProtocols=None):
        self.flags = flags
        self.name = name
        self.baseMethods = baseMethods or ListOrListList("list", None)
        self.baseProperties = baseProperties or ListOrListList("list", None)
        self.baseProtocols = baseProtocols or ListOrListList("list", None)


class ClassRwExt:
    """脏内存侧：只有真正需要改类时才分配。"""

    def __init__(self):
        self.ro = None
        self.methods = ListArray()
        self.properties = ListArray()
        self.protocols = ListArray()
        self.demangledName = None
        self.version = 0


class RoOrRwExt:
    """`objc::PointerUnion<const class_ro_t, class_rw_ext_t, ...>`。

    标签是**最低位**：T1(const class_ro_t) 原样存，T2(class_rw_ext_t) 置位 1；
    `get<T>()` 是 auth 之后再 `& ~1`。
    """

    def __init__(self, raw, ptra):
        self.raw = raw
        self.ptra = ptra

    def tag(self):
        return self.raw & 1

    def is_ro(self):
        return self.tag() == 0

    def is_rwe(self):
        return self.tag() == 1

    def is_null(self):
        return self.raw == 0                    # 整个字为零才算空

    def addr(self):
        return self.ptra.auth(self.raw) & ~1     # 取之前先验签

    def get_ro(self):
        if not self.is_ro():
            raise Violation("not a class_ro_t")
        return deref(self.addr())

    def get_rwe(self):
        if not self.is_rwe():
            raise Violation("not a class_rw_ext_t")
        return deref(self.addr())

    def dyn_rwe(self):
        return self.get_rwe() if self.is_rwe() else None

    def truthy(self):
        """`operator bool()`：两种类型各自 dyn_cast，任一非空即为真。"""
        return self.addr() != 0


class ClassRw:
    def __init__(self, arch, ro, flags=0, disc=0x1234):
        self.arch = arch
        self.flags = flags
        self.witness = 0
        self.firstSubclass = None
        self.nextSiblingClass = None
        self.ptra = PtrAuth(arch, disc)
        self.ro_or_rw_ext = 0
        self.cas_failures = 0
        self.cas_attempts = 0
        self.set_ro_or_rwe_ro(ro)

    # ---- ro_or_rw_ext ----
    def get_ro_or_rwe(self):
        return RoOrRwExt(self.ro_or_rw_ext, self.ptra)

    def set_ro_or_rwe_ro(self, ro):
        self.ro_or_rw_ext = self.ptra.sign(addr_of(ro, self.arch))

    def set_ro_or_rwe_rwe(self, rwe, ro):
        # 先把 rwe->ro 写好，再用 release 屏障存指针：
        # 源码注释说这一步是为了让无锁读者能看到 rwe->ro 的初始化
        rwe.ro = ro
        self.ro_or_rw_ext = self.ptra.sign(addr_of(rwe, self.arch) | 1)

    def ext(self):
        return self.get_ro_or_rwe().dyn_rwe()

    def ro(self):
        v = self.get_ro_or_rwe()
        if v.is_rwe():
            return v.get_rwe().ro
        return v.get_ro()

    def set_ro(self, ro):
        v = self.get_ro_or_rwe()
        if v.is_rwe():
            v.get_rwe().ro = ro
        else:
            self.set_ro_or_rwe_ro(ro)

    # ---- 列表三分支 ----
    def methodAlternates(self):
        v = self.get_ro_or_rwe()
        if v.is_rwe():
            return {"array": v.get_rwe().methods, "list": None,
                    "relativeList": None}
        base = v.get_ro().baseMethods
        return {"array": None, "list": base.dyn_list(),
                "relativeList": base.dyn_rel()}

    def methods(self):
        a = self.methodAlternates()
        if a["array"] is not None:
            return a["array"]
        if a["list"] is not None:
            return ListArray.from_list(a["list"])
        if a["relativeList"] is not None:
            return ListArray.from_rel(a["relativeList"])
        return ListArray()

    def properties(self):
        v = self.get_ro_or_rwe()
        if v.is_rwe():
            return v.get_rwe().properties
        base = v.get_ro().baseProperties
        if base.dyn_list() is not None:
            return ListArray.from_list(base.dyn_list())
        if base.dyn_rel() is not None:
            return ListArray.from_rel(base.dyn_rel())
        return ListArray()

    def protocols(self):
        v = self.get_ro_or_rwe()
        if v.is_rwe():
            return v.get_rwe().protocols
        base = v.get_ro().baseProtocols
        if base.dyn_list() is not None:
            return ListArray.from_list(base.dyn_list())
        if base.dyn_rel() is not None:
            return ListArray.from_rel(base.dyn_rel())
        return ListArray()

    # ---- flags ----
    def setFlags(self, s):
        self.flags = (self.flags | s) & 0xFFFFFFFF

    def clearFlags(self, c):
        self.flags = (self.flags & ~c) & 0xFFFFFFFF

    def _cas_flags(self, old, new):
        self.cas_attempts += 1
        if self.cas_failures > 0:
            self.cas_failures -= 1
            self.flags = (old ^ 1) & 0xFFFFFFFF     # 制造一次并发改写
            return False
        self.flags = new
        return True

    def changeFlags(self, set_f, clear_f):
        if set_f & clear_f:
            raise Violation("set and clear must not overlap")
        self.cas_attempts = 0
        while True:
            oldf = self.flags
            newf = (oldf | set_f) & ~clear_f & 0xFFFFFFFF
            if self._cas_flags(oldf, newf):
                return


def extAlloc(rw, ro, deep=False):
    """`class_rw_t::extAlloc`：把 ro 的只读内容搬进新分配的 rwe。"""
    rwe = ClassRwExt()
    rwe.version = 7 if (ro.flags & RO_META) else 0

    base = ro.baseMethods
    lst = base.dyn_list()
    if lst is not None:
        if deep:
            lst = lst.duplicate()
        rwe.methods.attachLists([lst])
    else:
        rel = base.dyn_rel()
        if rel is not None:
            if deep:
                for e in rel:                     # 逐个 duplicate，逐个 attach
                    rwe.methods.attachLists([e.duplicate()])
            else:
                rwe.methods.attachListList(rel)

    # 源码注释：property / protocol 列表「历史上从不深拷贝」，
    # 并说 "This is probably wrong and ought to be fixed some day"
    for dst_name, src in (("properties", ro.baseProperties),
                          ("protocols", ro.baseProtocols)):
        dst = getattr(rwe, dst_name)
        if src.dyn_list() is not None:
            dst.attachLists([src.dyn_list()])
        elif src.dyn_rel() is not None:
            dst.attachListList(src.dyn_rel())

    rw.set_ro_or_rwe_rwe(rwe, ro)
    return rwe


def extAllocIfNeeded(rw):
    v = rw.get_ro_or_rwe()
    if v.is_rwe():
        return v.get_rwe()
    return extAlloc(rw, v.get_ro())


def set_demangled_name(rwe, de, mangled):
    """`CompareAndSwap(nullptr, de ?: mangled, &rwe->demangledName)`。

    返回 (成功与否, 是否应当 free(de))。
    """
    want = de if de is not None else mangled
    if rwe.demangledName is not None:
        return False, de is not None      # 失败，de 非空则由调用方释放
    rwe.demangledName = want
    return True, False
