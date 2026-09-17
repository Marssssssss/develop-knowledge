"""Go `reflect.Value` 的 flag 位模型（常量表见 `kinds.py`）。

判定条件照抄 go1.24.0 `src/reflect/value.go` 的 Elem / Field / CanSet /
mustBeAssignable / Interface / ro 实现。模型只覆盖"可设置性"这条主线。

关键设计：每个 Value 带一个 `slot`（模拟内存单元）。`ValueOf(x)` 把 x **复制**
进新 slot 且不设 flagAddr；`ValueOf(&x).Elem()` 指向 x 原有 slot 并设 flagAddr
—— 这正是第三定律成立与否的分水岭。
"""

from containers import Goval, Ptr, StructObj, new_struct
from kinds import (CHAN, FLOAT_KINDS, FLAG_ADDR, FLAG_EMBED_RO, FLAG_INDIR,
                   FLAG_METHOD, FLAG_RO, FLAG_STICKY_RO, FUNC, INT_KINDS,
                   INTERFACE, INVALID, KIND_NAMES, MAP, POINTER, SLICE, STRING,
                   STRUCT, UINT_KINDS, UNSAFEPOINTER, WIDEST_GETTER,
                   ro_collapse, truncate)


class ReflectPanic(Exception):
    """对应运行时的 panic("reflect: ...")。"""


class RValue:
    """`reflect.Value` 的替身：typ / ptr / flag 三字段。"""

    __slots__ = ("kind", "typ", "flag", "slot", "store", "struct_type")

    def __init__(self, kind, typ, flag, slot=None, store=None,
                 struct_type=None):
        self.kind = kind
        self.typ = typ
        self.flag = flag
        self.slot = slot
        self.store = store
        self.struct_type = struct_type

    @classmethod
    def zero(cls):
        """`Value{}`：flag == 0，也叫 zero Value。"""
        return cls(INVALID, "", 0)

    # -------------------------------------------------- 基本查询
    def is_valid(self):
        return self.flag != 0

    def kind_name(self):
        return KIND_NAMES[self.kind]

    def type_name(self):
        if self.flag == 0:
            raise ReflectPanic("reflect: call of reflect.Value.Type on zero Value")
        return self.typ

    def can_addr(self):
        return self.flag & FLAG_ADDR != 0

    def can_set(self):
        """源码：`return v.flag&(flagAddr|flagRO) == flagAddr`"""
        return self.flag & (FLAG_ADDR | FLAG_RO) == FLAG_ADDR

    def is_ro(self):
        return self.flag & FLAG_RO != 0

    def string(self):
        if self.flag == 0:
            return "<invalid Value>"
        return "<" + self.typ + " Value>"

    def value_error(self, method):
        """源码 `func (e *ValueError) Error() string` 的消息格式。"""
        if self.kind == INVALID:
            return "reflect: call of reflect.Value." + method + " on zero Value"
        return ("reflect: call of reflect.Value." + method + " on "
                + self.kind_name() + " Value")

    def must_be(self, expected, method):
        if self.kind != expected:
            raise ReflectPanic(self.value_error(method))

    def must_be_exported(self, method):
        if self.flag & FLAG_RO:
            raise ReflectPanic("reflect: reflect.Value." + method
                               + " using value obtained using unexported field")

    def must_be_assignable(self, method):
        """源码 `func (f flag) mustBeAssignable()` 的两个 panic 分支（顺序固定）。"""
        if self.flag & FLAG_RO:
            raise ReflectPanic("reflect: reflect.Value." + method
                               + " using value obtained using unexported field")
        if self.flag & FLAG_ADDR == 0:
            raise ReflectPanic("reflect: reflect.Value." + method
                               + " using unaddressable value")

    # -------------------------------------------------- 变换
    def elem(self):
        if self.kind == INTERFACE:
            inner = self.store.load(self.slot)
            if inner is None:
                return RValue.zero()
            x = inner.copy()
            fl = x.kind | FLAG_INDIR | ro_collapse(self.flag)
            return RValue(x.kind, x.typ, fl, self.store.alloc(x.value),
                          self.store, x.struct_type)
        if self.kind != POINTER:
            raise ReflectPanic(self.value_error("Elem"))
        p = self.store.load(self.slot) if self.slot is not None else None
        if p is None:
            return RValue.zero()
        inner = p.pointee
        fl = (self.flag & FLAG_RO) | FLAG_INDIR | FLAG_ADDR | inner.kind
        return RValue(inner.kind, inner.typ, fl, p.target, self.store,
                      inner.struct_type)

    def field(self, i):
        if self.kind != STRUCT:
            raise ReflectPanic(self.value_error("Field"))
        obj = self.store.load(self.slot)
        if i < 0 or i >= len(obj.slots):
            raise ReflectPanic("reflect: Field index out of range")
        f = self.struct_type.fields[i]
        # 源码：继承权限位但**清掉** flagEmbedRO，也不继承 flagMethod
        fl = (self.flag & (FLAG_STICKY_RO | FLAG_INDIR | FLAG_ADDR)) | f.kind
        if not f.exported:
            fl |= FLAG_EMBED_RO if f.embedded else FLAG_STICKY_RO
        return RValue(f.kind, f.typ, fl, obj.slots[i], self.store, f.struct_type)

    def field_by_index(self, index):
        if len(index) == 1:
            return self.field(index[0])
        v = self
        for i in index:
            v = v.field(i)
        return v

    def field_by_name(self, name):
        self.must_be(STRUCT, "FieldByName")
        idx, _f = self.struct_type.field_by_name(name)
        if idx is None:
            return RValue.zero()
        return self.field_by_index(idx)

    def interface(self, method="Interface"):
        """第二定律：把 (值, 具体类型) 重新装回 interface{}。"""
        if self.flag == 0:
            raise ReflectPanic(self.value_error(method))
        if self.flag & FLAG_RO:
            raise ReflectPanic("reflect.Value.Interface: cannot return value "
                               "obtained from unexported field or method")
        return self.store.load(self.slot)

    def addr(self):
        if not self.can_addr():
            raise ReflectPanic("reflect: reflect.Value.Addr using "
                               "unaddressable value")
        gv = Goval(self.kind, self.typ, self.store.load(self.slot),
                   self.struct_type)
        ps = self.store.alloc(Ptr(self.slot, gv))
        return RValue(POINTER, "*" + self.typ, POINTER | FLAG_INDIR, ps,
                      self.store)

    # -------------------------------------------------- getter / setter
    def get_scalar(self):
        """getter 走最大类型：Int→int64 / Uint→uint64 / Float→float64。"""
        if self.kind not in WIDEST_GETTER:
            raise ReflectPanic(self.value_error("Int"))
        name, target = WIDEST_GETTER[self.kind]
        raw = self.store.load(self.slot)
        return (int(raw) if name in ("Int", "Uint") else float(raw)), name, target

    def set_int(self, x, method="SetInt"):
        self.must_be_assignable(method)
        if self.kind not in INT_KINDS and self.kind not in UINT_KINDS:
            raise ReflectPanic(self.value_error(method))
        self.store.put(self.slot, truncate(self.kind, x))

    def set_float(self, x, method="SetFloat"):
        self.must_be_assignable(method)
        if self.kind not in FLOAT_KINDS:
            raise ReflectPanic(self.value_error(method))
        self.store.put(self.slot, float(x))

    def set_string(self, x, method="SetString"):
        self.must_be_assignable(method)
        if self.kind != STRING:
            raise ReflectPanic(self.value_error(method))
        self.store.put(self.slot, x)

    def is_nil(self):
        if self.flag & FLAG_METHOD:
            return False
        if self.kind in (CHAN, FUNC, MAP, POINTER, UNSAFEPOINTER):
            return self.store.load(self.slot) is None
        if self.kind in (INTERFACE, SLICE):
            return self.store.load(self.slot) is None
        raise ReflectPanic(self.value_error("IsNil"))


# ---------------------------------------------------------------- 入口函数
def value_of(store, gv):
    """`reflect.ValueOf`：把实参复制进新 slot，**不设** flagAddr（不可设置）。"""
    if gv is None:
        return RValue.zero()
    slot = store.alloc(gv.copy().value)
    return RValue(gv.kind, gv.typ, gv.kind | FLAG_INDIR, slot, store,
                  gv.struct_type)


def addr_value(store, gv, slot):
    """`reflect.ValueOf(&x)`：得到指向 slot 的 Ptr Value，它自身不可设置。"""
    ps = store.alloc(Ptr(slot, gv))
    return RValue(POINTER, "*" + gv.typ, POINTER | FLAG_INDIR, ps, store)
