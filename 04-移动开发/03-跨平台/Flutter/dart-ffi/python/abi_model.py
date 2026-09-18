"""dart:ffi 中 AbiSpecificInteger 家族的 ABI 映射表与宽度模型(纯标准库)。

数据来源(2026-09-18 实读 api.dart.dev):
  * UintPtr(C uintptr_t)—— 取自 AbiSpecificInteger 类页面上给出的完整
    @AbiSpecificIntegerMapping 示例(该页面的示例代码就是 UintPtr 的映射)。
  * Long(C long int)—— 取自 api.dart.dev Long 类页(2.19.5 快照)。

**口径标注(重要)**:两份表来自不同版本的页面快照,条目集合并不完全一致 ——
Long 的 2.19.5 快照里没有 androidRiscv64 / fuchsiaRiscv64 两项(RISC-V 是后加的
ABI),UintPtr 的当前页面里有。因此本模型所有断言都在**两张表的交集**上做,
不对差集做任何推断;交集之外的 ABI 只记录缺失,不编造宽度。

核心事实:同一个 Dart 标记类型在不同 ABI 上宽度不同,这正是 AbiSpecificInteger
存在的理由 —— C 的 `long` 在 LP64(Linux/macOS 64 位)下是 64 位,在
LLP64(Windows 64 位)下仍是 32 位;而 `uintptr_t`/`size_t` 在两者上都是 64 位。
把 Dart 的 `Long` 当成"一定是 8 字节"来写序列化代码,在 Windows 上必然错位。
"""

# Dart 标记类型 -> {Abi 名: 位宽}
LONG_BITS = {                     # C long int
    "androidArm": 32, "androidArm64": 64, "androidIA32": 32, "androidX64": 64,
    "fuchsiaArm64": 64, "fuchsiaX64": 64,
    "iosArm": 32, "iosArm64": 64, "iosX64": 64,
    "linuxArm": 32, "linuxArm64": 64, "linuxIA32": 32, "linuxX64": 64,
    "linuxRiscv32": 32, "linuxRiscv64": 64,
    "macosArm64": 64, "macosX64": 64,
    "windowsArm64": 32, "windowsIA32": 32, "windowsX64": 32,
}

UINTPTR_BITS = {                  # C uintptr_t
    "androidArm": 32, "androidArm64": 64, "androidIA32": 32, "androidX64": 64,
    "androidRiscv64": 64,
    "fuchsiaArm64": 64, "fuchsiaX64": 64, "fuchsiaRiscv64": 64,
    "iosArm": 32, "iosArm64": 64, "iosX64": 64,
    "linuxArm": 32, "linuxArm64": 64, "linuxIA32": 32, "linuxX64": 64,
    "linuxRiscv32": 32, "linuxRiscv64": 64,
    "macosArm64": 64, "macosX64": 64,
    "windowsArm64": 64, "windowsIA32": 32, "windowsX64": 64,
}

# dart:ffi 里"可以实例化"的类型(官方 c-interop 指南列出的四个)
INSTANTIABLE = ("Array", "Pointer", "Struct", "Union")

# 只能当类型签名标记、不能在 Dart 里构造的类型
MARKER_ONLY = ("Bool", "Double", "Float", "Int8", "Int16", "Int32", "Int64",
               "NativeFunction", "Opaque", "Uint8", "Uint16", "Uint32", "Uint64",
               "Void")

# 宽度随 ABI 变化的整数类型(都是 AbiSpecificInteger 的子类)
ABI_SPECIFIC_INTEGERS = ("Int", "IntPtr", "Long", "LongLong", "Short", "SignedChar",
                         "Size", "UintPtr", "UnsignedChar", "UnsignedInt",
                         "UnsignedLong", "UnsignedLongLong", "UnsignedShort", "WChar")

# 每个平台产出的动态库文件名(官方 c-interop 指南给的例子)
LIBRARY_SUFFIX = {"macos": "dylib", "windows": "dll", "linux": "so"}


def common_abis():
    """两张表的交集 —— 只有这些 ABI 的宽度是可比的。"""
    return sorted(set(LONG_BITS) & set(UINTPTR_BITS))


def only_in(table_a, table_b):
    """仅出现在 table_a 的 ABI(版本快照差异,不做推断)。"""
    return sorted(set(table_a) - set(table_b))


def long_is_32bit():
    return sorted(abi for abi, bits in LONG_BITS.items() if bits == 32)


def same_width_as_pointer():
    """long 与 uintptr_t 宽度一致的 ABI。"""
    return sorted(abi for abi in common_abis() if LONG_BITS[abi] == UINTPTR_BITS[abi])


def differ_from_pointer():
    """long 与 uintptr_t 宽度不一致的 ABI —— 就是 LLP64 家族。"""
    return sorted(abi for abi in common_abis() if LONG_BITS[abi] != UINTPTR_BITS[abi])


def is_64bit_abi(abi):
    """用指针宽度判定该 ABI 是不是 64 位(指针宽度在 LLP64/LP64 下都是 64)。"""
    return UINTPTR_BITS[abi] == 64


def field_size_of(marker, abi):
    """按 ABI 给出某个 Dart 标记类型在内存里的字节数。"""
    if marker == "Long":
        return LONG_BITS[abi] // 8
    if marker == "UintPtr":
        return UINTPTR_BITS[abi] // 8
    raise KeyError("本模型只收录了 Long 与 UintPtr 两个 ABI 相关类型")


def naive_struct_stride(fields, abi, assume_long_is_8=False):
    """把一组字段按"逐字段紧密排布、无尾部填充"的朴素模型算总字节数。

    只为演示"错的口径会错多少",不代表真实 C 结构体布局(真实布局还要考虑
    对齐与尾部填充,本 demo 不涉及)。
    """
    total = 0
    for marker in fields:
        if marker == "Long" and assume_long_is_8:
            total += 8
        else:
            total += field_size_of(marker, abi)
    return total
