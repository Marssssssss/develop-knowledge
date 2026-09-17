"""`reflect.Kind` 与 `reflect.Value.flag` 的常量表。

数值与顺序照抄 go1.24.0：
  * Kind 的 iota 块在 `src/reflect/type.go`
  * flag 常量块在 `src/reflect/value.go`
"""

# ---------------------------------------------------------------- Kind 表
# 顺序与 `Invalid Kind = iota` 开始的常量块一致，共 27 个（flagKindWidth 为 5）。
(INVALID, BOOL, INT, INT8, INT16, INT32, INT64, UINT, UINT8, UINT16, UINT32,
 UINT64, UINTPTR, FLOAT32, FLOAT64, COMPLEX64, COMPLEX128, ARRAY, CHAN, FUNC,
 INTERFACE, MAP, POINTER, SLICE, STRING, STRUCT, UNSAFEPOINTER) = range(27)

KIND_NAMES = ("Invalid", "Bool", "Int", "Int8", "Int16", "Int32", "Int64",
              "Uint", "Uint8", "Uint16", "Uint32", "Uint64", "Uintptr",
              "Float32", "Float64", "Complex64", "Complex128", "Array", "Chan",
              "Func", "Interface", "Map", "Pointer", "Slice", "String",
              "Struct", "UnsafePointer")

INT_KINDS = (INT, INT8, INT16, INT32, INT64)
UINT_KINDS = (UINT, UINT8, UINT16, UINT32, UINT64, UINTPTR)
FLOAT_KINDS = (FLOAT32, FLOAT64)

# Value 的 getter/setter 一律走"能装下该值的最大类型"
# （官方文档：the "getter" and "setter" methods of Value operate on the largest
#   type that can hold the value）
WIDEST_GETTER = {}
for _k in INT_KINDS:
    WIDEST_GETTER[_k] = ("Int", "int64")
for _k in UINT_KINDS:
    WIDEST_GETTER[_k] = ("Uint", "uint64")
for _k in FLOAT_KINDS:
    WIDEST_GETTER[_k] = ("Float", "float64")

# 各定长整型在 64 位平台上的位宽（int/uint/uintptr 取 64）
NARROW_BITS = {INT: 64, INT8: 8, INT16: 16, INT32: 32, INT64: 64,
               UINT: 64, UINT8: 8, UINT16: 16, UINT32: 32, UINT64: 64,
               UINTPTR: 64}

# ---------------------------------------------------------------- flag 位
FLAG_KIND_WIDTH = 5                     # 源码注释：there are 27 kinds
FLAG_KIND_MASK = (1 << FLAG_KIND_WIDTH) - 1
FLAG_STICKY_RO = 1 << 5                 # 经未导出且非嵌入字段得到 → 只读
FLAG_EMBED_RO = 1 << 6                  # 经未导出嵌入字段得到 → 只读
FLAG_INDIR = 1 << 7                     # ptr 指向数据
FLAG_ADDR = 1 << 8                      # CanAddr 为真
FLAG_METHOD = 1 << 9                    # 方法值
FLAG_RO = FLAG_STICKY_RO | FLAG_EMBED_RO


def ro_collapse(flag):
    """源码 `func (f flag) ro() flag`：任意 RO 都折叠成 flagStickyRO。

    只被 Elem() 的 Interface 分支用到（`x.flag |= v.flag.ro()`）。
    """
    return FLAG_STICKY_RO if flag & FLAG_RO else 0


def kind_of(flag):
    """源码 `func (f flag) kind() Kind`。"""
    return flag & FLAG_KIND_MASK


def truncate(kind, value):
    """按 Go 的窄整型补码语义截断（int8(300) == 44，int16(32768) == -32768）。

    与 Go 侧同一算法：在无符号空间按位掩码取低 b 位，再判符号位。
    直接用 `%` 会踩到 Go 的"余数保留被除数符号"（-1%256 == -1）。
    """
    if kind in INT_KINDS:
        b = NARROW_BITS[kind]
        m = 1 << b
        u = int(value) & (m - 1)
        return u - m if u >= m >> 1 else u
    if kind in UINT_KINDS:
        return int(value) & ((1 << NARROW_BITS[kind]) - 1)
    return value
