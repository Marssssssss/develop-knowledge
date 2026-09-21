"""Hermes 的 64 位值表示（NaN-boxing）与 HBC 文件格式的可执行模型。

对应源码（facebook/hermes @main）：
  include/hermes/VM/HermesValue.h            —— 标签布局、构造、isDouble/isPointer
  include/hermes/BCGen/HBC/BytecodeFileFormat.h —— MAGIC、文件头、字符串表项、SmallFuncHeader
  include/hermes/Support/SHA1.h              —— SHA1_NUM_BYTES = 20

C++ 的 uint64 溢出/移位语义在 Python 里显式模拟（MASK64）。
"""

import struct

MASK64 = (1 << 64) - 1


def u64(x: int) -> int:
    return x & MASK64


def to_signed(raw: int) -> int:
    return raw - (1 << 64) if raw >= (1 << 63) else raw


# ------------------------------------------------------------ 标签枚举


class Tag:
    First = -7          # llvh::SignExtend32<8>(0xf9)
    EmptyInvalid = First
    UndefinedNull = -6
    BoolSymbol = -5
    NativeValue = -4
    FirstPointer = -3
    Str = FirstPointer
    BigInt = -2
    Object = -1
    Last = -1           # SignExtend32<8>(0xff)


class ETag:
    """扩展标签 = 4 个基础标签各向右扩一位（ETag = 2×Tag 或 2×Tag+1）。"""
    Empty = Tag.EmptyInvalid * 2        # -14
    Invalid = Tag.EmptyInvalid * 2 + 1  # -13
    Undefined = Tag.UndefinedNull * 2   # -12
    Null = Tag.UndefinedNull * 2 + 1    # -11
    Bool = Tag.BoolSymbol * 2           # -10
    Symbol = Tag.BoolSymbol * 2 + 1     # -9
    Native1 = Tag.NativeValue * 2       # -8
    Native2 = Tag.NativeValue * 2 + 1   # -7
    Str1 = Tag.Str * 2                  # -6
    Str2 = Tag.Str * 2 + 1              # -5
    BigInt1 = Tag.BigInt * 2            # -4
    BigInt2 = Tag.BigInt * 2 + 1        # -3
    Object1 = Tag.Object * 2            # -2
    Object2 = Tag.Object * 2 + 1        # -1
    FirstPointer = Str1


kNumTagExpBits = 16
kNumDataBits = 64 - kNumTagExpBits          # 48
kDataMask = (1 << kNumDataBits) - 1
kTagWidth = 3
kETagWidth = 4

TAG_FIRST_RAW = u64(u64(Tag.First) << kNumDataBits)        # 0xFFF9000000000000
POINTER_FIRST_RAW = u64(u64(Tag.FirstPointer) << kNumDataBits)  # 0xFFFD000000000000


# ------------------------------------------------------------ 构造与判定


def hv_from_raw(raw: int) -> int:
    return u64(raw)


def hv_with_tag(val: int, tag: int) -> int:
    """HermesValue(uint64_t val, Tag tag) : raw_(val | (tag << 48))"""
    return u64(u64(val) | u64(u64(tag) << kNumDataBits))


def hv_with_etag(val: int, etag: int) -> int:
    """HermesValue(uint64_t val, ETag etag) : raw_(val | (etag << 47))"""
    return u64(u64(val) | u64(u64(etag) << (kNumDataBits - 1)))


def get_tag(raw: int) -> int:
    return to_signed(raw) >> kNumDataBits


def get_etag(raw: int) -> int:
    return to_signed(raw) >> (kNumDataBits - 1)


def is_double(raw: int) -> bool:
    return raw < TAG_FIRST_RAW


def is_pointer(raw: int) -> bool:
    return raw >= POINTER_FIRST_RAW


def get_pointer(raw: int) -> int:
    assert is_pointer(raw)
    return raw & kDataMask


def double_to_bits(d: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", d))[0]


def bits_to_double(b: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", u64(b)))[0]


def encode_number(d: float) -> int:
    """encodeTrustedNumberValue：直接塞 double 的位模式。"""
    return u64(double_to_bits(d))


def encode_nan() -> int:
    return u64(double_to_bits(float("nan")))


def is_nan(raw: int) -> bool:
    """源码先掩掉符号位再与 quiet NaN 比。"""
    mask = (1 << 63) - 1
    return (raw & mask) == (encode_nan() & mask)


def validate_pointer(ptr: int) -> bool:
    """LLVM_PTR_SIZE == 8 时的断言：指针高位必须清空。"""
    return (ptr & ~kDataMask & MASK64) == 0


def encode_null() -> int:
    return hv_with_etag(0, ETag.Null)


def encode_undefined() -> int:
    return hv_with_etag(0, ETag.Undefined)


def encode_bool(v: bool) -> int:
    return hv_with_etag(1 if v else 0, ETag.Bool)


def encode_empty() -> int:
    return hv_with_etag(0, ETag.Empty)


def encode_object(ptr: int) -> int:
    assert validate_pointer(ptr)
    return hv_with_tag(ptr, Tag.Object)


def encode_string(ptr: int) -> int:
    assert validate_pointer(ptr)
    return hv_with_tag(ptr, Tag.Str)


# ------------------------------------------------------------ HBC 文件格式

MAGIC = 0x1F1903C103BC1FC6
DELTA_MAGIC = u64(~MAGIC)
SHA1_NUM_BYTES = 20
BYTECODE_ALIGNMENT = 4                      # alignof(uint32_t)


def magic_code_units() -> list:
    """MAGIC 是「Hermes」的古希腊语 Ἑρμῆ 的 UTF-16BE，截到 8 字节。"""
    b = MAGIC.to_bytes(8, "big")
    return [int.from_bytes(b[i:i + 2], "big") for i in range(0, 8, 2)]


# BytecodeFileHeader 的字段宽度（字节）
HEADER_FIELDS = [
    ("magic", 8), ("version", 4), ("sourceHash", SHA1_NUM_BYTES),
] + [(name, 4) for name in (
    "fileLength", "globalCodeIndex", "functionCount", "stringKindCount",
    "identifierCount", "stringCount", "overflowStringCount", "stringStorageSize",
    "bigIntCount", "bigIntStorageSize", "regExpCount", "regExpStorageSize",
    "arrayBufferSize", "objKeyBufferSize", "objValueBufferSize", "segmentID",
    "cjsModuleCount", "functionSourceCount", "debugInfoOffset",
)] + [("options", 1), ("padding", 19)]


def header_size() -> int:
    return sum(w for _, w in HEADER_FIELDS)


# SmallStringTableEntry：isUTF16:1 / offset:23 / length:8
INVALID_OFFSET = 1 << 23
INVALID_LENGTH = (1 << 8) - 1


class SmallStringTableEntry:
    def __init__(self, is_utf16=False, offset=0, length=0):
        self.is_utf16 = is_utf16
        self.offset = offset
        self.length = length

    def is_overflowed(self) -> bool:
        return self.length == INVALID_LENGTH


class SmallFuncHeader:
    """overflowed 时把 32 位的大头偏移拆成 offset(低 16) 与 infoOffset(高 16)。"""

    def __init__(self):
        self.offset = 0
        self.info_offset = 0
        self.overflowed = False

    def set_large_header_offset(self, value: int):
        self.overflowed = True
        self.offset = value & 0xFFFF
        self.info_offset = value >> 16

    def get_large_header_offset(self) -> int:
        assert self.overflowed
        return (self.info_offset << 16) | self.offset


def main():
    print("== HermesValue 标签布局 ==")
    print("  数据位 =", kNumDataBits, " 标签区间 = [0xfff9 .. 0xffff]")
    print("  Tag  :", {n: getattr(Tag, n) for n in
                       ("EmptyInvalid", "UndefinedNull", "BoolSymbol", "NativeValue",
                        "Str", "BigInt", "Object")})
    print("  ETag :", {n: getattr(ETag, n) for n in
                       ("Empty", "Undefined", "Null", "Bool", "Symbol", "Object1", "Object2")})
    print("  undefined =", hex(encode_undefined()))
    print("  null      =", hex(encode_null()))
    print("  true      =", hex(encode_bool(True)))
    print("  object@0x1234 =", hex(encode_object(0x1234)))

    print("== HBC 文件头 ==")
    print("  MAGIC =", hex(MAGIC), "->", "".join(chr(c) for c in magic_code_units()))
    print("  DELTA_MAGIC =", hex(DELTA_MAGIC))
    print("  header size =", header_size(), "bytes; %32 =", header_size() % 32)
    print("  SmallStringTableEntry: INVALID_OFFSET =", INVALID_OFFSET,
          " INVALID_LENGTH =", INVALID_LENGTH)


if __name__ == "__main__":
    main()
