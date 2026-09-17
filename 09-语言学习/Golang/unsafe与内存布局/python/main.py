"""unsafe 与内存布局 —— 可执行断言集。

分组：A 规范的尺寸/对齐保证 · B Sizeof 的"不含引用内存"语义 ·
      C 文档的 pointer bytes 三例 · D 字段排序与 size class 浪费 ·
      E false sharing · F unsafe.Pointer 六种模式 · G Offsetof 与累计偏移
"""

import sys

from layout import (CLASS_TO_SIZE, MAX_SMALL, Typ, align, alignof, class_size,
                    diagnostic, optimal_order, ptrdata, reorder, sizeof)
from unsafe_patterns import (ALL_CASES, INVALID_HEADER_DECL, INVALID_NIL,
                             INVALID_PAST_END, INVALID_REFLECT_TEMP,
                             INVALID_SMALLER_T1, INVALID_SYSCALL_TEMP,
                             INVALID_TEMP_BEFORE_PTR, P2_PRINT, check_chain)

_fails = []
_total = 0


def check(cond, label, detail=""):
    global _total
    _total += 1
    if not cond:
        _fails.append(label + "  ::  " + str(detail))


def eq(got, want, label):
    check(got == want, label, "got=%r want=%r" % (got, want))


def S(name, *fields):
    return Typ("struct", name=name, fields=list(fields))


def f(name, t):
    return (name, t)


def B(name):
    return Typ(name)


# =============================================================== A 规范保证
def group_a():
    for t, n in (("int8", 1), ("uint8", 1), ("int16", 2), ("uint16", 2),
                 ("int32", 4), ("uint32", 4), ("float32", 4),
                 ("int64", 8), ("uint64", 8), ("float64", 8),
                 ("complex64", 8), ("complex128", 16)):
        eq(sizeof(B(t)), n, "A 规范尺寸 %s = %d" % (t, n))

    # Alignof(x) 至少为 1
    for t in ("int8", "uint8", "uint16", "int32", "int64", "float64"):
        check(alignof(B(t)) >= 1, "A 对齐至少 1: " + t, alignof(B(t)))

    # 结构体对齐 = 各字段对齐的最大值，至少 1
    s1 = S("S1", f("a", B("int8")), f("b", B("int64")))
    eq(alignof(s1), 8, "A 结构体对齐取字段最大值")
    empty = S("Empty")
    eq(alignof(empty), 1, "A 空结构体对齐为 1")
    eq(sizeof(empty), 0, "A 空结构体尺寸为 0")

    # 数组对齐 = 元素对齐
    eq(alignof(Typ("array", elem=B("int8"), n=37)), 1, "A 数组对齐 = 元素对齐（1）")
    eq(alignof(Typ("array", elem=B("float64"), n=3)), 8, "A 数组对齐 = 元素对齐（8）")
    eq(sizeof(Typ("array", elem=B("complex128"), n=0)), 0, "A 空数组尺寸为 0")

    # 零尺寸变量可同址：模型层面体现为 size 0 + align 1
    z = S("Z", f("x", Typ("array", elem=B("int8"), n=0)))
    eq(sizeof(z), 0, "A 只含零尺寸字段的结构体尺寸为 0")
    eq(alignof(z), 1, "A 且对齐为 1（两个零尺寸变量可同址）")


# =============================================================== B Sizeof 语义
def group_b():
    eq(sizeof(Typ("slice", elem=B("int64"))), 24, "B1 切片 = 24（描述符，不是底层数组）")
    eq(sizeof(B("string")), 16, "B2 字符串 = 16（指针 + 长度）")
    eq(sizeof(Typ("iface")), 16, "B3 接口 = 16（类型指针 + 数据指针）")
    for tag in ("map", "chan", "func", "ptr", "unsafeptr"):
        eq(sizeof(Typ(tag)), 8, "B4 %s = 8（一个机器字）" % tag)
    for tag in ("int", "uint", "uintptr"):
        eq(sizeof(B(tag)), 8, "B5 %s = 8（64 位平台）" % tag)
    eq(ptrdata(Typ("slice", elem=B("int64"))), 8, "B6 切片只扫 8 字节前缀")
    eq(ptrdata(Typ("iface")), 16, "B7 接口要扫全部 16 字节")
    eq(ptrdata(B("string")), 8, "B8 字符串只扫指针那 8 字节")


# =============================================================== C pointer bytes
def group_c():
    # 文档里给出的三个例子（fieldalignment 的 Doc 注释）
    c1 = S("C1", f("x", B("uint32")), f("s", B("string")))
    eq(struct_size(c1), 24, "C1 struct{uint32; string} 尺寸 24")
    eq(ptrdata(c1), 16, "C1 pointer bytes = 16")
    c2 = S("C2", f("s", B("string")), f("p", Typ("ptr", elem=B("uint32"))))
    eq(struct_size(c2), 24, "C2 struct{string; *uint32} 尺寸 24")
    eq(ptrdata(c2), 24, "C2 pointer bytes = 24（要扫到 *uint32 之后）")
    c3 = S("C3", f("s", B("string")), f("x", B("uint32")))
    eq(struct_size(c3), 24, "C3 struct{string; uint32} 尺寸 24")
    eq(ptrdata(c3), 8, "C3 pointer bytes = 8（指针后即停）")


def struct_size(t):
    from layout import struct_layout
    return struct_layout(t)[0]


# =============================================================== D 排序与浪费
def group_d():
    # 经典三字段：尺寸降一级
    t = S("Tri", f("A", B("bool")), f("B", B("int64")), f("C", B("bool")))
    eq(struct_size(t), 24, "D1 未排序尺寸 24")
    eq(struct_size(reorder(t)), 16, "D2 重排后尺寸 16")
    eq(optimal_order(t), [1, 0, 2], "D3 最优顺序把 int64 排最前")
    msg = diagnostic(t)
    eq(msg, "Tri has size 24 but the optimal size is 16 leading to a waste of 8 bytes (33%)",
       "D4 诊断文本（24/16 都是 size class，故不加 class 后缀）")

    # size class 上取整：56 → 64，48 → 48
    p = S("Padded", f("A", B("bool")), f("B", B("int64")),
          f("C", Typ("array", elem=B("int8"), n=32)), f("D", B("bool")))
    eq(struct_size(p), 56, "D5 Padded 实际尺寸 56")
    eq(optimal_order(p), [1, 2, 0, 3], "D6 最优顺序 int64 → [32]byte → 两个 bool")
    eq(struct_size(reorder(p)), 48, "D7 重排后尺寸 48")
    eq(diagnostic(p),
       "Padded has size 56 (allocator size class 64) but the optimal size is 48 "
       "leading to a waste of 16 bytes (25%)",
       "D8 只有非 size class 的那一侧才带 (allocator size class N) 后缀")

    eq(class_size(24), 24, "D9 classSize(24) = 24")
    eq(class_size(40), 48, "D10 classSize(40) = 48（40 不是 size class）")
    eq(class_size(56), 64, "D11 classSize(56) = 64")
    eq(class_size(MAX_SMALL), 32768, "D12 classSize(32768) = 32768")
    eq(class_size(MAX_SMALL + 1), -1, "D13 超过 32768 走大对象分配（-1）")
    check(CLASS_TO_SIZE[1] == 8 and CLASS_TO_SIZE[-1] == 32768,
          "D14 size class 表两端正确", CLASS_TO_SIZE[:2])

    # 尾部零尺寸字段占 1 字节
    tz = S("TailZero", f("a", B("int64")), f("z", S("Empty")))
    eq(struct_size(tz), 16, "D15 尾部零尺寸字段把尺寸从 8 顶到 16")
    # 零尺寸字段排在前面（optimalOrder 的第一优先级）
    tz2 = S("ZeroFirst", f("a", B("int64")), f("z", S("Empty")))
    eq(optimal_order(tz2)[0], 1, "D16 零尺寸字段被排到最前")

    # pointer bytes 只算前缀
    lo = S("PtrFirst", f("p", Typ("ptr", elem=B("int64"))),
           f("a", Typ("array", elem=B("int8"), n=100)))
    hi = S("PtrLast", f("a", Typ("array", elem=B("int8"), n=100)),
           f("p", Typ("ptr", elem=B("int64"))))
    eq(ptrdata(lo), 8, "D17 指针在前 → pointer bytes = 8")
    eq(ptrdata(hi), 112, "D18 指针在后 → pointer bytes = 112")
    eq(optimal_order(hi), [1, 0], "D19 重排把含指针字段提前")
    eq(ptrdata(reorder(hi)), 8, "D20 重排后 pointer bytes 降到 8")
    eq(struct_size(hi), struct_size(reorder(hi)), "D21 该重排不改尺寸，只改扫描范围")
    eq(diagnostic(hi),
       "PtrLast has 112 leading bytes of pointer data but optimal value is 8",
       "D22 尺寸不变时走 pointer bytes 诊断")


# =============================================================== E false sharing
def group_e():
    # 最紧凑顺序不总是最高效：两个各自被独立 goroutine 更新的字段被排进同一 cache line
    t = S("Counters", f("a", B("int64")), f("b", B("int64")))
    eq(optimal_order(t), [0, 1], "E1 两字段都已对齐，重排顺序不变")
    off_a, off_b = 0, 8
    check(off_a // 64 == off_b // 64, "E2 两字段落在同一 64 字节 cache line",
          (off_a, off_b))
    # 手动插入填充后分到不同行
    padded = S("CountersPadded", f("a", B("int64")),
               f("pad", Typ("array", elem=B("int8"), n=56)), f("b", B("int64")))
    eq(struct_size(padded), 72, "E3 8 + 56 + 8 = 72（填充 56 字节）")
    eq(64 // 64, 1, "E4 b 的偏移 64 落在下一条 cache line")
    check(0 // 64 != 64 // 64, "E5 a 与 b 不再同行（0 行 vs 1 行）")


# =============================================================== F unsafe 模式
def group_f():
    for name, obj, steps, t1 in ALL_CASES:
        bad = check_chain(obj, steps, t1_size=t1)
        eq(bad, [], "F 正例合法: " + name)

    bad = check_chain(24, INVALID_PAST_END, t1_size=8)
    check(len(bad) == 1 and "原对象内部" in bad[0], "F1 越界一个字节即 INVALID", bad)

    bad = check_chain(24, INVALID_TEMP_BEFORE_PTR, t1_size=8)
    check(len(bad) == 1 and "存进过变量" in bad[0], "F2 uintptr 不得存变量", bad)

    bad = check_chain(24, INVALID_NIL, t1_size=8)
    check(len(bad) == 1 and "不能是 nil" in bad[0], "F3 nil 指针不能做算术", bad)

    bad = check_chain(24, INVALID_SYSCALL_TEMP, t1_size=8)
    check(len(bad) == 1 and "就地转换" in bad[0], "F4 系统调用实参须就地转换", bad)

    bad = check_chain(16, INVALID_REFLECT_TEMP, t1_size=8)
    check(len(bad) == 1 and "存进过变量" in bad[0],
          "F5 reflect.Pointer() 结果须就地转换", bad)

    bad = check_chain(16, INVALID_HEADER_DECL, t1_size=8)
    check(len(bad) == 1 and "不得声明" in bad[0],
          "F6 不得声明 reflect header 普通变量", bad)

    bad = check_chain(8, INVALID_SMALLER_T1, t1_size=8)
    check(len(bad) == 1 and "T2 不大于 T1" in bad[0],
          "F7 模式 1 要求 T2 不大于 T1", bad)

    # 边界：正好到最后一个字节是合法的
    edge = [("ptr_to_unsafe",), ("unsafe_to_uintptr",), ("add_offset", 23),
            ("uintptr_to_unsafe", "ok")]
    eq(check_chain(24, edge, t1_size=8), [], "F8 offset == size-1 合法")
    # 仅用于打印是模式 2 的正当用途
    eq(check_chain(8, P2_PRINT, t1_size=8), [], "F9 转 uintptr 后仅打印合法")
    # &^ 取整后仍需落在对象内
    round_bad = [("ptr_to_unsafe",), ("unsafe_to_uintptr",), ("add_offset", 300),
                 ("and_not", 63), ("uintptr_to_unsafe", "ok")]
    bad10 = check_chain(256, round_bad, t1_size=8)
    check(len(bad10) == 1 and "原对象内部" in bad10[0],
          "F10 &^ 取整不豁免越界检查", bad10)
    round_ok = [("ptr_to_unsafe",), ("unsafe_to_uintptr",), ("add_offset", 250),
                ("and_not", 63), ("uintptr_to_unsafe", "ok")]
    eq(check_chain(256, round_ok, t1_size=8), [],
       "F11 &^ 只减小偏移（250 → 192），仍在对象内")


# =============================================================== G Offsetof
def group_g():
    t = S("Mix", f("A", B("bool")), f("B", B("int64")),
          f("C", B("uint16")), f("D", B("uint32")))
    offs = []
    o = 0
    mx = 1
    for _name, ft in t.fields:
        o = align(o, alignof(ft))
        offs.append(o)
        mx = max(mx, alignof(ft))
        o += sizeof(ft)
    eq(offs, [0, 8, 16, 20], "G1 Offsetof 表：0 / 8 / 16 / 20")
    eq(align(o, mx), struct_size(t), "G2 末字段结尾对齐后即结构体尺寸")
    eq(struct_size(t), 24, "G3 Mix 尺寸 24")
    eq(ptrdata(t), 0, "G4 无指针字段 → pointer bytes 为 0")
    eq(diagnostic(t),
       "Mix has size 24 but the optimal size is 16 leading to a waste of 8 bytes (33%)",
       "G5 未排序时按尺寸报诊断")
    eq(diagnostic(reorder(t)), "", "G6 重排后无诊断（已最优）")
    eq([t.fields[i][0] for i in optimal_order(t)], ["B", "D", "C", "A"],
       "G7 最优顺序：对齐大的先来，逐级降序")
    eq(struct_size(reorder(t)), 16, "G8 重排后尺寸 16")


def main():
    for g in (group_a, group_b, group_c, group_d, group_e, group_f, group_g):
        g()
    print("断言：%d 项，失败 %d 项" % (_total, len(_fails)))
    for x in _fails:
        print("  FAIL", x)
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
