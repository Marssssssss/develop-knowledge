"""dart:ffi ABI 相关整数类型的自检(纯标准库,直接 python3 main.py 运行)。

断言目标:证明"C long 的宽度不是常数"这件事有可检验的形态 ——
  * 两张 ABI 映射表的条目数与交集;
  * long 为 32 位的 ABI 集合恰好是"全部 32 位 ABI + Windows 的全部 64 位 ABI";
  * 与 uintptr_t 宽度不一致的 ABI 恰好只有 {windowsArm64, windowsX64}(LLP64);
  * 用"long 一定是 8 字节"的错口径推结构体宽度,在 Windows 上会系统性偏大。
"""

import sys

from abi_model import (ABI_SPECIFIC_INTEGERS, INSTANTIABLE, LIBRARY_SUFFIX,
                       LONG_BITS, MARKER_ONLY, UINTPTR_BITS, common_abis,
                       differ_from_pointer, field_size_of, is_64bit_abi,
                       long_is_32bit, naive_struct_stride, only_in,
                       same_width_as_pointer)

PASS = 0
FAIL = 0
FAILED = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append("%s | %s" % (label, detail))


# ---------------- 1. 表结构 ----------------
check("Long 表 20 项", len(LONG_BITS) == 20, str(len(LONG_BITS)))
check("UintPtr 表 22 项", len(UINTPTR_BITS) == 22, str(len(UINTPTR_BITS)))
check("两张表交集 20 项", len(common_abis()) == 20, str(len(common_abis())))
check("Long 表缺 RISC-V 两项(2.19.5 快照口径)",
      only_in(UINTPTR_BITS, LONG_BITS) == ["androidRiscv64", "fuchsiaRiscv64"],
      str(only_in(UINTPTR_BITS, LONG_BITS)))
check("UintPtr 表无独有条目", only_in(LONG_BITS, UINTPTR_BITS) == [],
      str(only_in(LONG_BITS, UINTPTR_BITS)))

# ---------------- 2. long 的 32 位集合 ----------------
# 32 位 ABI(= 指针 32 位):androidArm/androidIA32/iosArm/linuxArm/linuxIA32/
# linuxRiscv32/windowsIA32;64 位 ABI 里 Windows 的三个仍然是 32 位 long
EXPECTED_32 = sorted(["androidArm", "androidIA32", "iosArm", "linuxArm",
                      "linuxIA32", "linuxRiscv32", "windowsIA32",
                      "windowsArm64", "windowsX64"])
check("long 为 32 位的 ABI 集合精确匹配", long_is_32bit() == EXPECTED_32,
      str(long_is_32bit()))
check("全部 32 位 ABI(long 与指针同为 4 字节)的 long 都是 32 位",
      all(LONG_BITS[a] == 32 for a in common_abis() if not is_64bit_abi(a)))
check("64 位 ABI 里 long 仍为 32 位的只有 Windows 的两项",
      sorted(a for a in long_is_32bit() if is_64bit_abi(a))
      == ["windowsArm64", "windowsX64"],
      str(sorted(a for a in long_is_32bit() if is_64bit_abi(a))))

# ---------------- 3. long 与 uintptr_t 的差异集合 = LLP64 ----------------
check("long 与 uintptr_t 宽度不一致的 ABI 恰好是 windowsArm64/windowsX64",
      differ_from_pointer() == ["windowsArm64", "windowsX64"],
      str(differ_from_pointer()))
check("LP64 代表:linuxX64 两者都是 64 位",
      field_size_of("Long", "linuxX64") == 8 and field_size_of("UintPtr", "linuxX64") == 8)
check("LP64 代表:macosArm64 两者都是 64 位",
      field_size_of("Long", "macosArm64") == 8
      and field_size_of("UintPtr", "macosArm64") == 8)
check("LLP64 代表:windowsX64 指针 8 字节但 long 仍 4 字节",
      field_size_of("UintPtr", "windowsX64") == 8
      and field_size_of("Long", "windowsX64") == 4)
check("ILP32 代表:iosArm 两者都是 4 字节",
      field_size_of("Long", "iosArm") == 4 and field_size_of("UintPtr", "iosArm") == 4)
check("androidArm64 指针与 long 同为 8 字节(LP64)",
      field_size_of("Long", "androidArm64") == 8
      and field_size_of("UintPtr", "androidArm64") == 8)

# 两种口径互为反例:同一个 ABI 名在两张表上答案不同
check("windowsArm64 在两张表上的答案不同(Int32 vs Uint64)",
      LONG_BITS["windowsArm64"] == 32 and UINTPTR_BITS["windowsArm64"] == 64)

# ---------------- 4. 错口径的代价 ----------------
fields = ["Long", "UintPtr", "Long", "Long"]
for abi in ("linuxX64", "macosX64", "androidArm64"):
    check("LP64 下假设 long=8 不产生偏差(%s)" % abi,
          naive_struct_stride(fields, abi, assume_long_is_8=True)
          == naive_struct_stride(fields, abi, assume_long_is_8=False),
          str(naive_struct_stride(fields, abi, assume_long_is_8=True)))
check("windowsX64 下假设 long=8 会多算 12 字节(3 个 long 各多 4 字节)",
      naive_struct_stride(fields, "windowsX64", assume_long_is_8=True)
      - naive_struct_stride(fields, "windowsX64", assume_long_is_8=False) == 12,
      "%d vs %d" % (naive_struct_stride(fields, "windowsX64", assume_long_is_8=True),
                    naive_struct_stride(fields, "windowsX64", assume_long_is_8=False)))
check("windowsX64 正确总宽 = 4+8+4+4 = 20",
      naive_struct_stride(fields, "windowsX64") == 20,
      str(naive_struct_stride(fields, "windowsX64")))
check("linuxX64 正确总宽 = 8+8+8+8 = 32",
      naive_struct_stride(fields, "linuxX64") == 32,
      str(naive_struct_stride(fields, "linuxX64")))
check("同一段 Dart 声明在两个 ABI 上宽度不同",
      naive_struct_stride(fields, "windowsX64") != naive_struct_stride(fields, "linuxX64"))

# ---------------- 5. 类型系统分类 ----------------
check("可实例化的基类型恰好 4 个", len(INSTANTIABLE) == 4, str(INSTANTIABLE))
check("Array/Pointer/Struct/Union 都在可实例化集合里",
      set(INSTANTIABLE) == {"Array", "Pointer", "Struct", "Union"})
check("Opaque/NativeFunction/Void 只能当标记",
      {"Opaque", "NativeFunction", "Void"} <= set(MARKER_ONLY))
check("定宽整数只能当标记", set(["Int8", "Int16", "Int32", "Int64"]) <= set(MARKER_ONLY))
check("AbiSpecificInteger 家族 14 个类型",
      len(ABI_SPECIFIC_INTEGERS) == 14, str(len(ABI_SPECIFIC_INTEGERS)))
check("Long/UintPtr 都属于 ABI 相关类型",
      {"Long", "UintPtr"} <= set(ABI_SPECIFIC_INTEGERS))
check("定宽整数不在 ABI 相关类型里",
      not ({"Int32", "Uint64"} & set(ABI_SPECIFIC_INTEGERS)))
check("可实例化集合与只作标记集合不相交",
      not (set(INSTANTIABLE) & set(MARKER_ONLY)))

# ---------------- 6. 动态库文件名按平台不同 ----------------
check("macOS → dylib", LIBRARY_SUFFIX["macos"] == "dylib")
check("Windows → dll", LIBRARY_SUFFIX["windows"] == "dll")
check("Linux → so", LIBRARY_SUFFIX["linux"] == "so")
check("三个平台后缀互不相同", len(set(LIBRARY_SUFFIX.values())) == 3)

# ---------------- 7. 交叉检查:两个 64 位 Linux/Windows 平台的整体态度 ----------------
windows_mismatch = [a for a in common_abis() if a.startswith("windows")
                    and LONG_BITS[a] != UINTPTR_BITS[a]]
linux_mismatch = [a for a in common_abis() if a.startswith("linux")
                  and LONG_BITS[a] != UINTPTR_BITS[a]]
check("Windows 有 2 个 ABI 出现 long/指针错位", len(windows_mismatch) == 2,
      str(windows_mismatch))
check("Linux 一个都没有", linux_mismatch == [], str(linux_mismatch))
check("macOS 一个都没有",
      [a for a in common_abis() if a.startswith("macos")
       and LONG_BITS[a] != UINTPTR_BITS[a]] == [])

print("=" * 62)
print("dart:ffi ABI 整数类型自检:通过 %d 项,失败 %d 项" % (PASS, FAIL))
if FAILED:
    for line in FAILED:
        print("  [FAIL] " + line)
    sys.exit(1)
print("全部通过")
