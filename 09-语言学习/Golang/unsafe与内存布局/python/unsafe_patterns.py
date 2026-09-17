"""unsafe.Pointer 的六种合法转换模式 —— 可执行的合法性校验器。

规则来自 `unsafe` 包文档：文档明确列出 **(1)~(6) 六种合法模式**，并给出若干
`// INVALID:` 反例。本模块把一条"转换链"表示成步骤序列，逐条检查它是否踩中
文档点名的非法形式。

链的起点是一个已分配对象 `Object(size)`；步骤语义：

    ptr_to_unsafe(t1)           *T1 → unsafe.Pointer
    unsafe_to_ptr(t2)           unsafe.Pointer → *T2（T2 不得大于 T1）
    unsafe_to_uintptr()         unsafe.Pointer → uintptr
    uintptr_to_unsafe()         uintptr → unsafe.Pointer
    add_offset(n)               unsafe.Pointer(uintptr(p) + n)（同一表达式内）
    and_not(n)                  &^ 取整到对齐
    store_temp()                把当前值存进变量（切断"同一表达式"约束）
    syscall_arg()               作为汇编实现的系统调用实参
    print_only()                仅用于打印（模式 2 的正当用途）
    header_declared()           直接声明 reflect.StringHeader/SliceHeader 变量
    header_of_real(x)           指向真实 string/slice 的 header 指针
    reflect_ptr_field()         取 reflect.Value.Pointer()/UnsafeAddr() 的返回值
"""


class Violation(Exception):
    pass


def check_chain(object_size, steps, t1_size=None):
    """返回违规原因列表；空列表表示这条链合法。"""
    bad = []
    origin = None          # 当前 uintptr 的"同一表达式"来源是否有效
    stored = False         # 中途是否存进过变量
    offset = 0
    has_ptr = False
    for step in steps:
        kind = step[0]
        if kind == "ptr_to_unsafe":
            has_ptr = True
            origin = "expr"
            offset = 0
        elif kind == "unsafe_to_ptr":
            t2 = step[1]
            if t1_size is not None and t2 > t1_size:
                bad.append("模式 1 要求 T2 不大于 T1（T2=%d > T1=%d）" % (t2, t1_size))
            if not has_ptr:
                bad.append("unsafe.Pointer 必须先由某个指针转来")
        elif kind == "unsafe_to_uintptr":
            had = has_ptr
            has_ptr = False
            stored = False
            origin = "expr" if had else None
        elif kind == "add_offset":
            if origin is None:
                bad.append("uintptr 不是来自同一表达式内的 Pointer 转换")
            offset += step[1]
            if object_size is not None and not (0 <= offset < object_size):
                bad.append("结果必须仍指向原对象内部（offset=%d, size=%d）"
                           % (offset, object_size))
        elif kind == "and_not":
            offset &= ~step[1]
        elif kind == "store_temp":
            if origin == "expr":
                stored = True
                origin = "temp"
        elif kind == "uintptr_to_unsafe":
            if origin == "temp" or stored:
                bad.append("uintptr 存进过变量再转回 Pointer")
            if step[1] is None:      # 参数 None 表示基址是 nil
                bad.append("Pointer 必须指向已分配对象，不能是 nil")
            origin = None
            stored = False
            has_ptr = True
        elif kind == "syscall_arg":
            if stored:
                bad.append("系统调用实参必须就地转换，不能先存变量")
            origin = None
        elif kind == "print_only":
            origin = None
        elif kind == "header_declared":
            bad.append("不得声明 reflect.SliceHeader/StringHeader 普通变量")
        elif kind == "header_of_real":
            pass
        elif kind == "reflect_ptr_field":
            origin = "expr"
        else:
            raise ValueError("未知步骤 " + kind)
    return bad


# ---------------------------------------------------------------- 六个正例
P1_FLOAT64BITS = [("ptr_to_unsafe",), ("unsafe_to_ptr", 8)]
P2_PRINT = [("ptr_to_unsafe",), ("unsafe_to_uintptr",), ("print_only",)]
P3_FIELD_ADDR = [
    ("ptr_to_unsafe",), ("unsafe_to_uintptr",), ("add_offset", 8),
    ("uintptr_to_unsafe", "ok"),
]
P3_ROUND = [
    ("ptr_to_unsafe",), ("unsafe_to_uintptr",), ("add_offset", 100),
    ("and_not", 63), ("uintptr_to_unsafe", "ok"),
]
P4_SYSCALL = [
    ("ptr_to_unsafe",), ("unsafe_to_uintptr",), ("syscall_arg",),
]
P5_REFLECT = [("reflect_ptr_field",), ("uintptr_to_unsafe", "ok")]
P6_HEADER = [("header_of_real",), ("unsafe_to_uintptr",)]

# ---------------------------------------------------------------- 文档反例
INVALID_TEMP_BEFORE_PTR = [
    ("ptr_to_unsafe",), ("unsafe_to_uintptr",), ("store_temp",),
    ("add_offset", 8), ("uintptr_to_unsafe", "ok"),
]
INVALID_PAST_END = [
    ("ptr_to_unsafe",), ("unsafe_to_uintptr",), ("add_offset", 24),
    ("uintptr_to_unsafe", "ok"),
]
INVALID_NIL = [
    ("ptr_to_unsafe",), ("unsafe_to_uintptr",), ("add_offset", 8),
    ("uintptr_to_unsafe", None),
]
INVALID_SYSCALL_TEMP = [
    ("ptr_to_unsafe",), ("unsafe_to_uintptr",), ("store_temp",),
    ("syscall_arg",),
]
INVALID_REFLECT_TEMP = [
    ("reflect_ptr_field",), ("store_temp",), ("uintptr_to_unsafe", "ok"),
]
INVALID_HEADER_DECL = [("header_declared",), ("unsafe_to_uintptr",)]
INVALID_SMALLER_T1 = [("ptr_to_unsafe",), ("unsafe_to_ptr", 16)]

ALL_CASES = [
    ("P1 math.Float64bits", 8, P1_FLOAT64BITS, 8),
    ("P2 打印地址", 8, P2_PRINT, 8),
    ("P3 &s.f", 24, P3_FIELD_ADDR, 8),
    ("P3 &^ 取整", 256, P3_ROUND, 8),
    ("P4 Syscall 实参", 8, P4_SYSCALL, 8),
    ("P5 reflect.Value.Pointer", 8, P5_REFLECT, 8),
    ("P6 真实 header", 16, P6_HEADER, 8),
]
