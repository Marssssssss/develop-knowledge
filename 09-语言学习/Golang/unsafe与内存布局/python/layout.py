"""Go 结构体内存布局模型。

算法照抄两处权威实现：
  * `unaligned/sizes` 的等价物 —— `golang.org/x/tools` 的 `fieldalignment` 分析器
    里内嵌的 `gcSizes`（`sizeof` / `alignof` / `ptrdata`），它复刻了 cmd/compile
    的布局规则，比 `go/types` 的 `Sizes` 多出 `ptrdata`（GC 扫描上界）；
  * 规范 `unsafe` 包文档的 `Sizeof` / `Alignof` / `Offsetof` 语义，以及
    《The Go Programming Language Specification》的 "Size and alignment
    guarantees" 一节。

`classSize` 复刻分析器里的 `cap(bytes.Clone(make([]byte, n)))`：取 Go 的
size class 上取整（表见 `runtime/sizeclasses.go`），超过 32 KB 视为走大对象分配。
"""

WORD = 8        # 64 位平台的字长（unsafe.Sizeof(unsafe.Pointer(nil))）
MAX_ALIGN = 8   # 最大对齐
MAX_SMALL = 32768   # runtime 的 maxSmallSize

# runtime/sizeclasses.go 的 class_to_size（索引即 size class，class 0 未使用）
CLASS_TO_SIZE = [
    0, 8, 16, 24, 32, 48, 64, 80, 96, 112,
    128, 144, 160, 176, 192, 208, 224, 240, 256, 288,
    320, 352, 384, 416, 448, 480, 512, 576, 640, 704,
    768, 896, 1024, 1152, 1280, 1408, 1536, 1792, 2048, 2304,
    2688, 3072, 3200, 3456, 4096, 4864, 5376, 6144, 6528, 6784,
    6912, 8192, 9472, 9728, 10240, 10880, 12288, 13568, 14336, 16384,
    18432, 19072, 20480, 21760, 24576, 27264, 28672, 32768,
]


class Typ:
    """极简类型描述。tag 取基本类型名，或 ptr/slice/map/chan/func/iface/array/struct。"""

    def __init__(self, tag, elem=None, n=0, fields=None, name=""):
        self.tag = tag
        self.elem = elem
        self.n = n
        self.fields = fields or []   # [(字段名, Typ)]
        self.name = name or tag

    def __repr__(self):
        return "<Typ %s>" % self.name


# ---------------------------------------------------------------- 基本表
# fieldalignment 的 basicSizes：只列出显式给出的项；Int/Uint/Uintptr 落到 catch-all
BASIC_SIZES = {
    "bool": 1, "int8": 1, "int16": 2, "int32": 4, "int64": 8,
    "uint8": 1, "uint16": 2, "uint32": 4, "uint64": 8,
    "float32": 4, "float64": 8, "complex64": 8, "complex128": 16,
}
PTR_LIKE = ("ptr", "map", "chan", "func", "unsafeptr")


def is_basic(t):
    return t.tag in BASIC_SIZES or t.tag in ("int", "uint", "uintptr", "string")


def sizeof(t):
    if t.tag in BASIC_SIZES:
        return BASIC_SIZES[t.tag]
    if t.tag == "string":
        return WORD * 2
    if t.tag in ("int", "uint", "uintptr") or t.tag in PTR_LIKE:
        return WORD            # catch-all：源码里落到 return s.wordSize
    if t.tag == "iface":
        return WORD * 2
    if t.tag == "slice":
        return WORD * 3
    if t.tag == "array":
        return t.n * sizeof(t.elem)
    if t.tag == "struct":
        return struct_layout(t)[0]
    raise ValueError("未知类型标签 " + t.tag)


def alignof(t):
    if t.tag == "array":
        return alignof(t.elem)
    if t.tag == "struct":
        m = 1
        for _n, ft in t.fields:
            m = max(m, alignof(ft))
        return m
    a = sizeof(t)
    if a < 1:
        return 1
    return min(a, MAX_ALIGN)


def ptrdata(t):
    """GC 需要扫描的**前缀**字节数（最后一个含指针字段的结束偏移）。"""
    if t.tag in ("string", "unsafeptr"):
        return WORD
    if t.tag in ("ptr", "chan", "map", "func", "slice"):
        return WORD
    if t.tag == "iface":
        return 2 * WORD
    if t.tag in BASIC_SIZES or t.tag in ("int", "uint", "uintptr"):
        return 0
    if t.tag == "array":
        if t.n == 0:
            return 0
        a = ptrdata(t.elem)
        if a == 0:
            return 0
        return (t.n - 1) * sizeof(t.elem) + a
    if t.tag == "struct":
        _sz, _p, o = _struct_walk(t)
        return o
    raise ValueError("未知类型标签 " + t.tag)


def align(x, a):
    """返回最小的 y >= x 且 y % a == 0（源码里的 align 函数）。"""
    y = x + a - 1
    return y - y % a


def _struct_walk(t):
    """返回 (size, maxAlign, ptrdata)。逐字段照抄 gcSizes.sizeof / ptrdata。"""
    nf = len(t.fields)
    if nf == 0:
        return 0, 1, 0
    o = 0
    mx = 1
    p = 0
    for i, (_name, ft) in enumerate(t.fields):
        a, sz = alignof(ft), sizeof(ft)
        mx = max(mx, a)
        if i == nf - 1 and sz == 0 and o != 0:
            sz = 1          # 尾部零尺寸字段占 1 字节，避免和下一对象同址
        o = align(o, a)
        fp = ptrdata(ft)
        if fp != 0:
            p = o + fp
        o += sz
    return align(o, mx), mx, p


def struct_layout(t):
    size, mx, p = _struct_walk(t)
    return size, p


def class_size(size):
    """对应分析器的 cap(bytes.Clone(make([]byte, size)))。"""
    if size > MAX_SMALL:
        return -1
    for c in CLASS_TO_SIZE[1:]:
        if size <= c:
            return c
    return -1


def optimal_order(t):
    """fieldalignment 的 optimalOrder：返回排序后的字段下标序列。

    优先级（逐条比较）：
      1. 零尺寸字段排最前；
      2. 对齐要求大的排前面；
      3. 含指针的排在无指针的前面；
      4. 都含指针时，**尾随非指针字节少的**排前面；
      5. 最后按尺寸降序。
    """
    def key(item):
        idx, (_name, ft) = item
        sz, al, pd = sizeof(ft), alignof(ft), ptrdata(ft)
        zero = 0 if sz == 0 else 1   # 升序排序下，零尺寸要排最前
        tail = sz - pd
        return (zero, -al, 1 if pd == 0 else 0,
                tail if pd != 0 else 0, -sz, idx)

    order = sorted(enumerate(t.fields), key=key)
    return [i for i, _f in order]


def reorder(t):
    """按 optimal_order 重排字段，返回新的 Typ。"""
    return Typ("struct", name=t.name,
               fields=[t.fields[i] for i in optimal_order(t)])


def diagnostic(t):
    """复刻分析器的诊断文本（判别顺序：先尺寸，后 pointer bytes）。"""
    opt = reorder(t)
    actual_size, actual_ptrs = struct_layout(t)
    optimal_size, optimal_ptrs = struct_layout(opt)
    if actual_size != optimal_size:
        ac, oc = class_size(actual_size), class_size(optimal_size)
        msg = "%s has size %d" % (t.name, actual_size)
        if ac == -1:
            ac = actual_size
            msg += " (uses global allocator)"
        elif ac != actual_size:
            msg += " (allocator size class %d)" % ac
        msg += " but the optimal size is %d" % optimal_size
        if oc == -1:
            oc = optimal_size
        elif oc != optimal_size:
            msg += " (allocator size class %d)" % oc
        waste = ac - oc
        if waste > 0:
            msg += " leading to a waste of %d bytes (%d%%)" % (waste, waste * 100 // ac)
        return msg
    if actual_ptrs != optimal_ptrs:
        return ("%s has %d leading bytes of pointer data but optimal value is %d"
                % (t.name, actual_ptrs, optimal_ptrs))
    return ""
