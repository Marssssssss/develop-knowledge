"""Hermes 值表示与 HBC 文件格式自检。

运行： python selfcheck_hermes.py
"""

from main import (
    MASK64, Tag, ETag, kNumDataBits, kDataMask, kTagWidth, kETagWidth,
    TAG_FIRST_RAW, POINTER_FIRST_RAW,
    hv_with_tag, hv_with_etag, get_tag, get_etag, is_double, is_pointer,
    get_pointer, double_to_bits, encode_number, encode_nan, is_nan,
    validate_pointer, encode_null, encode_undefined, encode_bool, encode_empty,
    encode_object, encode_string,
    MAGIC, DELTA_MAGIC, SHA1_NUM_BYTES, BYTECODE_ALIGNMENT, magic_code_units,
    HEADER_FIELDS, header_size, INVALID_OFFSET, INVALID_LENGTH,
    SmallStringTableEntry, SmallFuncHeader,
)

PASS = 0


def ok(cond, label, actual=None):
    global PASS
    assert cond, "FAIL: {} -> {!r}".format(label, actual)
    PASS += 1


# ==================================================== A. 位布局常量
ok(kNumDataBits == 48, "A1 数据位 = 64 - 16 = 48", kNumDataBits)
ok(kDataMask == (1 << 48) - 1, "A2 kDataMask = 2^48 - 1", hex(kDataMask))
ok(kTagWidth == 3 and kETagWidth == 4, "A3 基础标签 3 位判定宽度、扩展标签 4 位")
ok(TAG_FIRST_RAW == 0xFFF9000000000000, "A4 Tag::First 的 raw 阈值", hex(TAG_FIRST_RAW))
ok(POINTER_FIRST_RAW == 0xFFFD000000000000, "A5 Tag::FirstPointer 的 raw 阈值",
   hex(POINTER_FIRST_RAW))

# ==================================================== B. 标签数值
ok(Tag.First == -7, "B1 Tag::First = SignExtend32<8>(0xf9) = -7", Tag.First)
ok(Tag.Last == -1, "B2 Tag::Last = SignExtend32<8>(0xff) = -1", Tag.Last)
ok([Tag.EmptyInvalid, Tag.UndefinedNull, Tag.BoolSymbol, Tag.NativeValue,
    Tag.Str, Tag.BigInt, Tag.Object] == [-7, -6, -5, -4, -3, -2, -1],
   "B3 七个基础标签连续递增（Object 与 Last 同为 -1）")
ok(Tag.Object == Tag.Last, "B4 Tag::Object 就是最后一个标签")
ok(Tag.FirstPointer == Tag.Str, "B5 指针标签从 Str 开始")
ok(ETag.Undefined == Tag.UndefinedNull * 2 == -12, "B6 ETag::Undefined = 2 × UndefinedNull")
ok(ETag.Null == -11 and ETag.Bool == -10 and ETag.Symbol == -9,
   "B7 ETag::Null/Bool/Symbol = -11 / -10 / -9", (ETag.Null, ETag.Bool, ETag.Symbol))
ok(ETag.FirstPointer == ETag.Str1 == -6, "B8 ETag 的指针区从 Str1 开始")
ok(ETag.Object1 == -2 and ETag.Object2 == -1, "B9 对象有两个 ETag（单字节 GC 位复用）")

# ==================================================== C. 编码结果
u = encode_undefined()
ok(u == 0xFFFA000000000000, "C1 undefined", hex(u))
ok(get_etag(u) == ETag.Undefined, "C2 undefined 的 ETag", get_etag(u))
ok(get_tag(u) == Tag.UndefinedNull, "C3 undefined 的基础 Tag", get_tag(u))

n = encode_null()
ok(n == 0xFFFA800000000000, "C4 null（bit47 置 1）", hex(n))
ok(get_etag(n) == ETag.Null, "C5 null 的 ETag", get_etag(n))

t = encode_bool(True)
f = encode_bool(False)
ok(t == 0xFFFB000000000001 and f == 0xFFFB000000000000,
   "C6 bool 值放在数据位（true=1 / false=0）", (hex(t), hex(f)))
ok(get_etag(t) == ETag.Bool, "C7 bool 的 ETag", get_etag(t))

e = encode_empty()
ok(e == 0xFFF9000000000000 and get_etag(e) == ETag.Empty, "C8 empty", hex(e))

o = encode_object(0x1234)
ok(o == 0xFFFF000000001234, "C9 对象指针", hex(o))
ok(get_pointer(o) == 0x1234, "C10 getPointer 掩掉高 16 位", hex(get_pointer(o)))
ok(get_tag(o) == Tag.Object and get_etag(o) == ETag.Object1,
   "C11 用基础 Tag 编码的对象，读成 ETag 时落在偶数那一档（ETag = 2×Tag）",
   (get_tag(o), get_etag(o)))

s = encode_string(0xABCD)
ok(s == 0xFFFD00000000ABCD and get_tag(s) == Tag.Str, "C12 字符串指针", hex(s))
ok(get_etag(s) == ETag.Str1, "C13 字符串同理：Tag::Str(-3) 读成 ETag 是 -6", get_etag(s))

sym = hv_with_etag(0x2A, ETag.Symbol)
ok(get_etag(sym) == ETag.Symbol and (sym & ((1 << 47) - 1)) == 0x2A,
   "C13 SymbolID 存数据位（ETag 编码下可用数据位只有 47 位）")
ok(get_etag(hv_with_etag(1 << 47, ETag.Undefined)) == ETag.Null,
   "C14 ETag 编码时 val 必须 < 2^47：bit47 被标签占用，越过会把 undefined 变成 null")

# ==================================================== D. isDouble / isPointer
d = encode_number(1.5)
ok(is_double(d) and not is_pointer(d), "D1 double 位模式既是 double 也不是指针", hex(d))
ok(d == double_to_bits(1.5), "D2 数值按 IEEE754 原样存放，不做任何变换")

ok(is_double(TAG_FIRST_RAW) is False, "D3 边界：raw == Tag::First 阈值 已是标签而非 double")
ok(is_double(TAG_FIRST_RAW - 1) is True, "D4 阈值小 1 仍是 double（0xfff8 = 规范 quiet NaN）")
ok(is_double(0xFFF8000000000000) is True, "D5 负的 quiet NaN 也算 double")
ok(is_pointer(u) is False, "D6 undefined 不是指针")
ok(is_pointer(POINTER_FIRST_RAW) is True, "D7 边界：raw == FirstPointer 阈值 算指针")
ok(is_pointer(POINTER_FIRST_RAW - 1) is False, "D8 阈值小 1 不是指针")
ok(is_pointer(o) and is_pointer(s), "D9 对象与字符串都是指针")

# ==================================================== E. NaN 判定
ok(is_nan(0x7FF8000000000000) is True, "E1 正 quiet NaN 是 NaN")
ok(is_nan(0xFFF8000000000000) is True, "E2 负 quiet NaN 也是 NaN（先掩掉符号位）")
ok(is_nan(encode_number(1.5)) is False, "E3 普通 double 不是 NaN")
ok(is_nan(encode_nan()) is True, "E4 encodeNaNValue 的结果是 NaN")

# ==================================================== F. 指针编码上限
ok(validate_pointer(0x0000FFFFFFFFFFFF) is True, "F1 48 位指针合法")
ok(validate_pointer(0x0001000000000000) is False, "F2 超过 48 位被断言拦下")
ok((o & ~kDataMask & MASK64) == 0xFFFF000000000000,
   "F3 高 16 位被标签占满，取指针时必须掩掉")
ok(get_pointer(hv_with_tag(0x0000FFFFFFFFFFFF, Tag.Object)) == kDataMask,
   "F4 最大可编码指针 = 2^48 - 1")

# ==================================================== G. HBC 魔数与文件头
ok(MAGIC == 0x1F1903C103BC1FC6, "G1 MAGIC", hex(MAGIC))
ok(magic_code_units() == [0x1F19, 0x03C1, 0x03BC, 0x1FC6],
   "G2 MAGIC = 古希腊语 Ἑρμῆ 的四个 UTF-16BE 码元", [hex(c) for c in magic_code_units()])
ok("".join(chr(c) for c in magic_code_units()) == "Ἑρμῆ",
   "G3 拼起来就是 Hermes 的古希腊语写法", "".join(chr(c) for c in magic_code_units()))
ok(DELTA_MAGIC == 0xE0E6FC3EFC43E039 and DELTA_MAGIC == (~MAGIC) & MASK64,
   "G4 DELTA_MAGIC = ~MAGIC（delta 形态，不可执行）", hex(DELTA_MAGIC))
ok(SHA1_NUM_BYTES == 20, "G5 SHA1_NUM_BYTES = 20")
ok(BYTECODE_ALIGNMENT == 4, "G6 BYTECODE_ALIGNMENT = alignof(uint32_t) = 4")
ok(header_size() == 128, "G7 BytecodeFileHeader 共 128 字节", header_size())
ok(header_size() % 32 == 0, "G8 static_assert：文件头是 32 的整数倍（cache 友好）")
ok(sum(1 for name, w in HEADER_FIELDS if w == 4) == 20,
   "G9 其中 20 个 uint32 字段（version + 19 个计数/偏移字段）",
   sum(1 for name, w in HEADER_FIELDS if w == 4))
ok(dict(HEADER_FIELDS)["padding"] == 19 and dict(HEADER_FIELDS)["options"] == 1,
   "G10 options 1 字节 + padding 19 字节，用来避免函数头跨 cache line")

# ==================================================== H. 字符串表项与函数头
ok(INVALID_OFFSET == 8388608 and INVALID_LENGTH == 255,
   "H1 SmallStringTableEntry 的溢出哨兵", (INVALID_OFFSET, INVALID_LENGTH))

ent = SmallStringTableEntry(is_utf16=False, offset=8388607, length=254)
ok(ent.is_overflowed() is False, "H2 offset 与 length 都在范围内 -> 不是溢出项")
ent2 = SmallStringTableEntry(offset=0, length=255)
ok(ent2.is_overflowed() is True, "H3 length == INVALID_LENGTH 即溢出（去查溢出表）")
ok(INVALID_OFFSET == (1 << 23) and INVALID_LENGTH == (1 << 8) - 1,
   "H4 位域宽度 1 + 23 + 8 = 32 位正好一个 uint32")

h = SmallFuncHeader()
h.set_large_header_offset(0xDEADBEEF)
ok(h.offset == 0xBEEF and h.info_offset == 0xDEAD,
   "H5 大函数头偏移拆成低 16 / 高 16 两位域", (hex(h.offset), hex(h.info_offset)))
ok(h.get_large_header_offset() == 0xDEADBEEF, "H6 往返可还原",
   hex(h.get_large_header_offset()))
h2 = SmallFuncHeader()
h2.set_large_header_offset(0xFFFFFFFF)
ok(h2.get_large_header_offset() == 0xFFFFFFFF, "H7 32 位偏移全覆盖")
ok(h.overflowed is True, "H8 setLargeHeaderOffset 会同时置 overflowed 标志")

# ==================================================== I. 类型分派
def classify(raw):
    """按源码的判定顺序：先 isDouble，再 isPointer，最后按 ETag。"""
    if is_double(raw):
        return "double"
    if is_pointer(raw):
        return {Tag.Str: "string", Tag.BigInt: "bigint", Tag.Object: "object"}[get_tag(raw)]
    et = get_etag(raw)
    for name, val in (("empty", ETag.Empty), ("undefined", ETag.Undefined),
                      ("null", ETag.Null), ("bool", ETag.Bool), ("symbol", ETag.Symbol)):
        if et == val:
            return name
    return "native/other"


ok(classify(encode_number(3.25)) == "double", "I1 double")
ok(classify(encode_undefined()) == "undefined", "I2 undefined")
ok(classify(encode_null()) == "null", "I3 null")
ok(classify(encode_bool(True)) == "bool", "I4 bool")
ok(classify(encode_empty()) == "empty", "I5 empty")
ok(classify(encode_object(0x1000)) == "object", "I6 object")
ok(classify(encode_string(0x1000)) == "string", "I7 string")

print("Hermes 值与 HBC 格式自检：{} 项断言全部通过".format(PASS))
