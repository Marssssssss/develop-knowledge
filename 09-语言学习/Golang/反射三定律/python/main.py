"""Go 反射三定律 —— 可执行断言集。

三条定律（Rob Pike, The Laws of Reflection, 2011）：

  1. Reflection goes from interface value to reflection object.
  2. Reflection goes from reflection object to interface value.
  3. To modify a reflection object, the value must be settable.

分组：A Kind/Type 基础 · B zero Value 不变量 · C 第一定律 · D 第二定律
      E 第三定律（可设置性）· F 结构体字段与只读位的继承 · G panic 条件表
"""

import sys

import kinds
from containers import Field, Goval, Store, StructType, new_struct
from kinds import (CHAN, FLOAT32, FLOAT64, FLAG_ADDR, FLAG_EMBED_RO, FLAG_INDIR,
                   FLAG_METHOD, FLAG_RO, FLAG_STICKY_RO, INT, INT8, INT16,
                   INTERFACE, KIND_NAMES, POINTER, SLICE, STRING, STRUCT,
                   UINT16, UINT8, UNSAFEPOINTER, WIDEST_GETTER, kind_of,
                   ro_collapse, truncate)
from value_model import ReflectPanic, RValue, addr_value, value_of

_fails = []
_total = 0


def check(cond, label, detail=""):
    global _total
    _total += 1
    if not cond:
        _fails.append(label + "  ::  " + str(detail))


def eq(got, want, label):
    check(got == want, label, "got=%r want=%r" % (got, want))


def panics(fn, needle, label):
    """断言必须 panic，且消息里含 needle。"""
    try:
        fn()
    except ReflectPanic as e:
        msg = str(e)
        check(needle in msg, label, "msg=%r 缺 %r" % (msg, needle))
        return
    check(False, label, "本应 panic 但没 panic")


# =============================================================== A 基础
def group_a():
    eq(len(KIND_NAMES), 27, "A1 Kind 共 27 个")
    check(27 <= (1 << 5), "A2 flagKindWidth=5 能放下 27 个 Kind", 27)
    eq(KIND_NAMES[0], "Invalid", "A3 Invalid 排第一")
    eq(KIND_NAMES[22], "Pointer", "A4 Pointer=22")
    eq(KIND_NAMES[25], "Struct", "A5 Struct=25")
    eq(KIND_NAMES[26], "UnsafePointer", "A6 UnsafePointer=26")

    # flag 位值（源码常量块）
    eq(FLAG_STICKY_RO, 1 << 5, "A7 flagStickyRO = 1<<5")
    eq(FLAG_EMBED_RO, 1 << 6, "A8 flagEmbedRO = 1<<6")
    eq(FLAG_INDIR, 1 << 7, "A9 flagIndir = 1<<7")
    eq(FLAG_ADDR, 1 << 8, "A10 flagAddr = 1<<8")
    eq(FLAG_METHOD, 1 << 9, "A11 flagMethod = 1<<9")
    eq(FLAG_RO, FLAG_STICKY_RO | FLAG_EMBED_RO, "A12 flagRO 是两者之并")

    # Kind 描述底层类型，Type 描述静态类型 → Kind 分不出 MyInt 与 int
    st = Store()
    v = value_of(st, Goval(INT, "MyInt", 7))
    eq(v.type_name(), "MyInt", "A13 Type() 给静态类型 MyInt")
    eq(v.kind_name(), "Int", "A14 Kind() 只给底层 Int")
    eq(kind_of(v.flag), INT, "A15 flag 低 5 位就是 Kind")
    eq(v.get_scalar(), (7, "Int", "int64"), "A16 有符号一律经 Int64 取值")

    # getter 用能装下该值的最大类型
    eq(WIDEST_GETTER[INT8], ("Int", "int64"), "A17 int8 → Int64")
    eq(WIDEST_GETTER[UINT16], ("Uint", "uint64"), "A18 uint16 → Uint64")
    eq(WIDEST_GETTER[FLOAT32], ("Float", "float64"), "A19 float32 → Float64")


# =============================================================== B zero Value
def group_b():
    z = RValue.zero()
    eq(z.is_valid(), False, "B1 zero Value 无效")
    eq(z.flag, 0, "B2 zero Value 的 flag == 0")
    eq(z.kind, 0, "B3 zero Value 的 Kind 是 Invalid")
    eq(z.string(), "<invalid Value>", "B4 String() 给 <invalid Value>")
    eq(z.can_addr(), False, "B5 zero Value 不可取址")
    eq(z.can_set(), False, "B6 zero Value 不可设置")
    panics(lambda: z.elem(), "on zero Value", "B7 Elem() panic")
    panics(lambda: z.interface(), "on zero Value", "B8 Interface() panic")
    panics(lambda: z.is_nil(), "on zero Value", "B9 IsNil() panic")
    panics(lambda: z.type_name(), "on zero Value", "B10 Type() panic")
    panics(lambda: z.field(0), "on zero Value", "B11 Field() panic")
    # ValueOf(nil) 返回 zero Value（第一定律的入口边界）
    eq(value_of(Store(), None).is_valid(), False, "B12 ValueOf(nil) 得 zero Value")


# =============================================================== C 第一定律
def group_c():
    st = Store()
    xslot = st.alloc(3.4)                       # var x float64 = 3.4
    v = value_of(st, Goval(FLOAT64, "float64", st.load(xslot)))

    eq(v.is_valid(), True, "C1 拿到有效 Value")
    eq(v.type_name(), "float64", "C2 Type() = float64")
    eq(v.can_addr(), False, "C3 ValueOf 的结果不可取址")

    st.put(xslot, 99.0)                         # 之后改 x
    eq(v.get_scalar()[0], 3.4, "C4 ValueOf 拿的是拷贝，改 x 不影响 v")

    # 接口变量里存的永远是 (值, 具体类型)，不可能嵌套接口类型
    inner = Goval(INT, "int", 42)
    iface = Goval(INTERFACE, "interface {}", inner)   # var i any = 42
    ei = value_of(st, iface).elem()                   # 剥掉接口层
    eq(ei.kind_name(), "Int", "C5 接口里装的是具体类型 int")
    eq(ei.type_name(), "int", "C6 剥出后类型不是 interface{}")
    check(not isinstance(ei.type_name(), Goval), "C7 类型描述本身不是值", ei.typ)

    # 空接口能装任意值，TypeOf 给出具体类型
    for g in (Goval(INT, "int", 1), Goval(STRING, "string", "s"),
              Goval(STRUCT, "T", None)):
        tv = value_of(st, g)
        check(tv.type_name() != "interface {}", "C8 空接口不掩盖具体类型", tv.typ)
        eq(tv.type_name(), g.typ, "C9 TypeOf 给出具体类型名")


# =============================================================== D 第二定律
def group_d():
    st = Store()
    v = value_of(st, Goval(FLOAT64, "float64", 3.4))
    eq(v.interface(), 3.4, "D1 Interface() 是 ValueOf 的逆")
    check("3.4" not in v.string(), "D2 String() 不含值本身", v.string())
    eq(v.string(), "<float64 Value>", "D3 String() 的格式")

    outer = StructType("outer", [Field("A", FLOAT64, "float64")])
    ogv, oslot = new_struct(st, outer, [3.4])
    s = addr_value(st, ogv, oslot).elem()
    eq(s.field(0).interface(), 3.4, "D4 导出字段可以 Interface()")
    eq(type(s.field(0).interface()).__name__, "float", "D5 逆变换还原具体类型")


# =============================================================== E 第三定律
def group_e():
    st = Store()
    xslot = st.alloc(3.4)
    gv = Goval(FLOAT64, "float64", 3.4)

    vx = value_of(st, gv)                       # ValueOf(x)
    eq(vx.can_set(), False, "E1 ValueOf(x) 不可设置")
    panics(lambda: vx.set_float(7.1), "using unaddressable value",
           "E2 SetFloat 报 unaddressable")
    eq(st.load(xslot), 3.4, "E3 失败的 Set 没有副作用")

    p = addr_value(st, gv, xslot)               # ValueOf(&x)
    eq(p.type_name(), "*float64", "E4 Type() = *float64")
    eq(p.can_set(), False, "E5 指针 Value 自身不可设置")
    eq(p.can_addr(), False, "E6 指针 Value 自身不可取址")

    v = p.elem()                                # v := p.Elem()
    eq(v.can_set(), True, "E7 Elem() 后可设置")
    eq(v.can_addr(), True, "E8 Elem() 后可取址")
    v.set_float(7.1)
    eq(st.load(xslot), 7.1, "E9 改到了原变量 x 本身")
    eq(v.get_scalar()[0], 7.1, "E10 回读一致")


# =============================================================== F 结构体
def mk_struct(st):
    """outer{A int; b int; inner(embedded, unexported)}，inner{X int; y int}。"""
    inner = StructType("inner", [Field("X", INT, "int"), Field("y", INT, "int")])
    outer = StructType("outer", [
        Field("A", INT, "int"),
        Field("b", INT, "int"),
        Field("inner", STRUCT, "inner", struct_type=inner, embedded=True),
    ])
    ogv, oslot = new_struct(st, outer, [23, 7, None])
    igv, islot = new_struct(st, inner, [23, 7])
    st.put(st.load(oslot).slots[2], st.load(islot))
    return ogv, oslot, outer, inner


def group_f():
    st = Store()
    ogv, oslot, outer, inner = mk_struct(st)
    s = addr_value(st, ogv, oslot).elem()

    eq(s.can_set(), True, "F1 可取的地址的结构体 Value 可设置")
    eq(s.field(0).can_set(), True, "F2 导出字段可设置")
    eq(s.field(0).can_addr(), True, "F3 导出字段可取址")

    ub = s.field(1)
    eq(ub.is_ro(), True, "F4 未导出非嵌入字段 → 只读")
    check(ub.flag & FLAG_STICKY_RO != 0, "F5 置的是 flagStickyRO", hex(ub.flag))
    check(ub.flag & FLAG_EMBED_RO == 0, "F6 不置 flagEmbedRO", hex(ub.flag))
    eq(ub.can_addr(), True, "F7 只读但可取址（CanSet 不等于 CanAddr）")
    eq(ub.can_set(), False, "F8 只读即不可设置")
    panics(lambda: ub.interface(), "cannot return value obtained from unexported",
           "F9 未导出字段 Interface() panic")
    eq(ub.get_scalar(), (7, "Int", "int64"), "F10 未导出字段仍可用 Int() 读")

    emb = s.field(2)
    eq(emb.is_ro(), True, "F11 未导出嵌入字段 → 只读")
    check(emb.flag & FLAG_EMBED_RO != 0, "F12 置的是 flagEmbedRO", hex(emb.flag))
    check(emb.flag & FLAG_STICKY_RO == 0, "F13 不是 StickyRO", hex(emb.flag))
    eq(emb.can_set(), False, "F14 嵌入字段本身不可设置")

    ex = emb.field(0)
    eq(ex.is_ro(), False, "F15 嵌入字段的导出成员不带 RO")
    eq(ex.can_set(), True, "F16 因此可以设置（Field 掩码清掉 EmbedRO）")
    eq(emb.field(1).is_ro(), True, "F17 嵌入字段的未导出成员仍只读")
    check(emb.field(1).flag & FLAG_STICKY_RO != 0, "F18 置 StickyRO 而非 EmbedRO",
          hex(emb.field(1).flag))

    # 提升字段：FieldByName 走 FieldByIndex，与逐级 Field 等价
    eq(s.field_by_name("X").can_set(), True, "F19 提升的导出字段可设置")
    eq(s.field_by_name("y").can_set(), False, "F20 提升的未导出字段不可设置")
    eq(s.field_by_name("A").can_set(), True, "F21 直接字段可设置")
    eq(s.field_by_name("nope").is_valid(), False, "F22 找不到字段返回 zero Value")

    # 歧义：两个嵌入都有 X，同深度两名候选 → ok == false
    l = StructType("l", [Field("X", INT, "int")])
    r = StructType("r", [Field("X", INT, "int")])
    amb = StructType("amb", [
        Field("l", STRUCT, "l", struct_type=l, embedded=True),
        Field("r", STRUCT, "r", struct_type=r, embedded=True),
    ])
    eq(amb.field_by_name("X"), (None, None), "F23 同深度两名候选 → 歧义")

    # ro() 折叠
    eq(ro_collapse(FLAG_EMBED_RO), FLAG_STICKY_RO, "F24 ro() 折叠 EmbedRO")
    eq(ro_collapse(0), 0, "F25 非 RO 折叠为 0")

    # Field() 的掩码不含 flagMethod → 方法值的信息不继承
    s.flag |= FLAG_METHOD
    eq(s.field(0).flag & FLAG_METHOD, 0, "F26 Field() 清掉 flagMethod")


# =============================================================== G panic 表
def group_g():
    st = Store()
    vi = value_of(st, Goval(INT, "int", 5))
    panics(lambda: vi.elem(), "on Int Value", "G1 Elem() 非指针/接口 → ValueError")
    panics(lambda: vi.field(0), "on Int Value", "G2 Field() 非结构体 → ValueError")
    panics(lambda: vi.is_nil(), "on Int Value", "G3 IsNil() 非可空类型 → ValueError")
    panics(lambda: vi.addr(), "unaddressable value", "G4 Addr() 需可取址")

    nilp = RValue(POINTER, "*int", POINTER | FLAG_INDIR, st.alloc(None), st)
    eq(nilp.elem().is_valid(), False, "G5 nil 指针 Elem() 返回 zero Value")

    ogv, oslot, outer, inner = mk_struct(st)
    s = addr_value(st, ogv, oslot).elem()
    panics(lambda: s.field(99), "reflect: Field index out of range", "G6 Field 越界")

    vf = value_of(st, Goval(FLOAT64, "float64", 1.0))
    panics(lambda: vf.set_int(1), "using unaddressable value",
           "G7 不可设置时先报 unaddressable（判定顺序）")
    fslot = st.alloc(1.0)
    vfa = addr_value(st, Goval(FLOAT64, "float64", 1.0), fslot).elem()
    panics(lambda: vfa.set_int(1), "on Float64 Value", "G7b 可设置但 Kind 不符")

    eq(value_of(st, Goval(SLICE, "[]int", None)).is_nil(), True, "G8 nil 切片为真")
    eq(value_of(st, Goval(CHAN, "chan int", None)).is_nil(), True, "G9 nil chan 为真")
    nilp.flag |= FLAG_METHOD
    eq(nilp.is_nil(), False, "G10 方法值即使底层为 nil 也返回 false")
    nilp.flag &= ~FLAG_METHOD

    panics(lambda: value_of(st, Goval(UNSAFEPOINTER, "unsafe.Pointer", None))
           .set_string("x"), "using unaddressable value", "G11 先报 unaddressable")

    # 窄类型截断：setter 收最大类型，写入时按实际类型截断
    st2 = Store()
    slot8 = st2.alloc(0)
    v8 = addr_value(st2, Goval(INT8, "int8", 0), slot8).elem()
    v8.set_int(300)
    eq(st2.load(slot8), 44, "G12 int8(300) == 44")
    slotu = st2.alloc(0)
    vu = addr_value(st2, Goval(UINT8, "uint8", 0), slotu).elem()
    vu.set_int(300)
    eq(st2.load(slotu), 44, "G13 uint8(300) == 44")
    eq(truncate(INT8, -129), 127, "G14 int8 下溢回绕")
    eq(truncate(INT16, 32768), -32768, "G15 int16 上溢回绕")
    eq(truncate(INT, 5), 5, "G16 平台 int 不截断")
    eq(kinds.NARROW_BITS[INT16], 16, "G17 NARROW_BITS 表就位")


def main():
    for g in (group_a, group_b, group_c, group_d, group_e, group_f, group_g):
        g()
    print("断言：%d 项，失败 %d 项" % (_total, len(_fails)))
    for f in _fails:
        print("  FAIL", f)
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
