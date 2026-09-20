"""DEX 文件格式自检：逐条对照 AOSP《Dalvik 可执行文件格式》原文的判据。

运行：python selfcheck_dex.py
"""

import hashlib
import struct
import zlib

from dex_build import DexBuilder
from dex_leb128 import (  # noqa: F401
    uleb128_encode, uleb128_decode, sleb128_encode, sleb128_decode,
    uleb128p1_encode, uleb128p1_decode,
    mutf8_encode, mutf8_decode, mutf8_to_str, utf16_size, string_sort_key,
)
from dex_format import (  # noqa: F401
    DexFile, ENDIAN_CONSTANT, NO_INDEX, HEADER_SIZE_V40, HEADER_SIZE_V41,
    TYPE_HEADER_ITEM, TYPE_STRING_ID_ITEM, TYPE_TYPE_ID_ITEM, TYPE_PROTO_ID_ITEM,
    TYPE_METHOD_ID_ITEM, TYPE_CLASS_DEF_ITEM, TYPE_MAP_LIST, TYPE_CODE_ITEM,
    TYPE_STRING_DATA_ITEM, TYPE_CLASS_DATA_ITEM, TYPE_HIDDENAPI_CLASS_DATA_ITEM,
    TYPE_TYPE_LIST, code_item_padding, code_item_units,
)

PASS = [0]


def ok(cond, label):
    assert cond, "FAILED: " + label
    PASS[0] += 1


def eq(a, b, label):
    assert a == b, "FAILED: %s (期望 %r 实际 %r)" % (label, b, a)
    PASS[0] += 1


# ---------- 1. LEB128：官方「以下是这类格式的一些示例」四行表 ----------

OFFICIAL = [
    (b"\x00", 0, 0, -1),
    (b"\x01", 1, 1, 0),
    (b"\x7f", -1, 127, 126),
    (b"\x80\x7f", -128, 16256, 16255),
]
for enc, sleb, uleb, p1 in OFFICIAL:
    eq(sleb128_decode(enc)[0], sleb, "sleb128(%s)" % enc.hex())
    eq(uleb128_decode(enc)[0], uleb, "uleb128(%s)" % enc.hex())
    eq(uleb128p1_decode(enc)[0], p1 & 0xFFFFFFFF, "uleb128p1(%s)" % enc.hex())

# 官方：每个 LEB128 值由 1-5 字节组成；5 字节可表示完整 32 位
eq(len(uleb128_encode(0xFFFFFFFF)), 5, "uleb128(0xffffffff) 占满 5 字节")
eq(uleb128_decode(uleb128_encode(0xFFFFFFFF))[0], 0xFFFFFFFF, "uleb128 往返 0xffffffff")
eq(len(sleb128_encode(-1)), 1, "sleb128(-1) 单字节")
eq(sleb128_decode(sleb128_encode(-0x80000000))[0], -0x80000000, "sleb128 往返 -2^31")
for v in (0, 1, 127, 128, 16256, 0xFFFFFFFF, 0x7FFFFFFF):
    eq(uleb128_decode(uleb128_encode(v))[0], v, "uleb128 往返 %d" % v)
for v in (-1, -128, -129, 0, 63, 64, -0x80000000, 0x7FFFFFFF):
    eq(sleb128_decode(sleb128_encode(v))[0], v, "sleb128 往返 %d" % v)

# 官方：uleb128p1 让 -1（无符号 0xffffffff）编码成单字节，且没有其他负数能编码
eq(uleb128p1_encode(-1), b"\x00", "uleb128p1(-1) 是单字节 0x00")
eq(uleb128p1_encode(-2), b"\xff\xff\xff\xff\x0f",
   "uleb128p1(-2) 展开为 uleb128(0xffffffff) 的 5 字节，不是单字节")

# ---------- 2. MUTF-8 ----------

eq(mutf8_encode("\x00"), b"\xc0\x80\x00", "MUTF-8 把 U+0000 编成 c0 80 再加终止字节")
eq(utf16_size("\x00"), 1, "U+0000 的 utf16_size 是 1（不是字节数）")
eq(mutf8_encode("A"), b"A\x00", "ASCII 单字节")
eq(utf16_size("\U0001F600"), 2, "增补平面字符 utf16_size 是 2")
eq(len(mutf8_encode("\U0001F600")), 7, "增补平面字符 6 字节 + 1 终止字节")
eq(mutf8_to_str(mutf8_encode("\U0001F600")), "\U0001F600", "MUTF-8 往返增补平面字符")
eq(mutf8_to_str(mutf8_encode("aé中\U0001F600")), "aé中\U0001F600", "MUTF-8 往返混合串")
# 官方：MUTF-8 里 U+0000 可出现在串中且仍可作 C 风格 NUL 终止串处理
eq(mutf8_to_str(mutf8_encode("a\x00b")), "a\x00b", "串内 U+0000 不截断解码")

# ---------- 3. 常量与 header 尺寸 ----------

eq(ENDIAN_CONSTANT, 0x12345678, "ENDIAN_CONSTANT")
eq(NO_INDEX, 0xFFFFFFFF, "NO_INDEX 是 0xffffffff（按有符号即 -1）")
eq(HEADER_SIZE_V40, 0x70, "v40 及以下 header_size 必须 0x70")
eq(HEADER_SIZE_V41, 0x78, "v41 及以上 header_size 必须 0x78")
eq(TYPE_HEADER_ITEM, 0x0000, "TYPE_HEADER_ITEM")
eq(TYPE_STRING_ID_ITEM, 0x0001, "TYPE_STRING_ID_ITEM")
eq(TYPE_TYPE_ID_ITEM, 0x0002, "TYPE_TYPE_ID_ITEM")
eq(TYPE_PROTO_ID_ITEM, 0x0003, "TYPE_PROTO_ID_ITEM")
eq(TYPE_METHOD_ID_ITEM, 0x0005, "TYPE_METHOD_ID_ITEM")
eq(TYPE_CLASS_DEF_ITEM, 0x0006, "TYPE_CLASS_DEF_ITEM")
eq(TYPE_MAP_LIST, 0x1000, "TYPE_MAP_LIST")
eq(TYPE_TYPE_LIST, 0x1001, "TYPE_TYPE_LIST")
eq(TYPE_CODE_ITEM, 0x2001, "TYPE_CODE_ITEM")
eq(TYPE_STRING_DATA_ITEM, 0x2002, "TYPE_STRING_DATA_ITEM")
eq(TYPE_CLASS_DATA_ITEM, 0x2000, "TYPE_CLASS_DATA_ITEM")
eq(TYPE_HIDDENAPI_CLASS_DATA_ITEM, 0xF000, "TYPE_HIDDENAPI_CLASS_DATA_ITEM")

# ---------- 4. 构造一个结构完整的 dex 并回读对拍 ----------

b = DexBuilder()
obj = b.add_type("Ljava/lang/Object;")
hello = b.add_type("LHello;")
proto_main = b.add_proto("V", "V", ["Ljava/lang/String;"])
proto_v = b.add_proto("V", "V", [])
m_ctor = b.add_method(hello, "<init>", proto_v)
m_main = b.add_method(hello, "main", proto_main)
m_extra = b.add_method(hello, "zzz", proto_v)

# 字节码：0x0e = return-void（10x）；0x1a 0x00 0x00 = const-string v0, string@0（21c）
INSN_RETURN_VOID = 0x000E
INSN_CONST_STRING = 0x001A
b.add_class(hello, obj, access_flags=0x0001,
            direct_methods=[(m_ctor, 0x10001, [INSN_RETURN_VOID]),
                            (m_extra, 0x0002, []),          # abstract/native → code_off 为 0
                            (m_main, 0x0009, [INSN_CONST_STRING | (0 << 8), 0x0000,
                                              INSN_RETURN_VOID])])
blob = b.build()
dex = DexFile(blob)

eq(blob[0:8], b"dex\n039\x00", "DEX_FILE_MAGIC = dex\\n039\\0")
eq(len(blob), dex.file_size, "file_size 等于实际文件长度")
eq(dex.header_size, 0x70, "039 版 header_size 是 0x70")
eq(dex.endian_tag, ENDIAN_CONSTANT, "endian_tag 是 ENDIAN_CONSTANT")
eq(dex.checksum, zlib.adler32(blob[12:]) & 0xFFFFFFFF,
   "checksum 是除 magic 与自身之外的 adler32")
eq(dex.signature, hashlib.sha1(blob[32:]).digest(),
   "signature 是除 magic/checksum/自身之外的 SHA-1")
eq(len(dex.signature), 20, "signature 是 20 字节")

# map_list：官方要求按初始偏移量排序、不得重叠、每个类型最多出现一次
offsets = [m["offset"] for m in dex.map]
eq(offsets, sorted(offsets), "map_list 条目按初始偏移量升序")
kinds = [m["type"] for m in dex.map]
eq(len(kinds), len(set(kinds)), "map_list 同一类型最多出现一次")
eq(kinds[0], TYPE_HEADER_ITEM, "map_list 首项是 header_item")
head_entry = [m for m in dex.map if m["type"] == TYPE_HEADER_ITEM][0]
eq(head_entry["offset"], 0, "header_item 的 map 偏移是 0")
eq(head_entry["size"], 1, "header_item 在 map 里只有一项")
# 不重叠检查
spans = []
for m in dex.map:
    if m["type"] in (TYPE_STRING_ID_ITEM, TYPE_TYPE_ID_ITEM, TYPE_PROTO_ID_ITEM,
                     TYPE_METHOD_ID_ITEM, TYPE_CLASS_DEF_ITEM):
        per = {TYPE_STRING_ID_ITEM: 4, TYPE_TYPE_ID_ITEM: 4, TYPE_PROTO_ID_ITEM: 12,
               TYPE_METHOD_ID_ITEM: 8, TYPE_CLASS_DEF_ITEM: 32}[m["type"]]
        spans.append((m["offset"], m["offset"] + per * m["size"]))
spans.sort()
for i in range(1, len(spans)):
    ok(spans[i - 1][1] <= spans[i][0], "定长区段 %d 与 %d 不重叠" % (i - 1, i))

# 字符串表按 UTF-16 码位排序
names = [dex.string_at(i)[0] for i in range(dex.string_ids_size)]
keys = [string_sort_key(s) for s in names]
eq(keys, sorted(keys), "string_ids 按 UTF-16 码位值排序")
ok("LHello;" in names and "main" in names, "字符串表含 LHello; 与 main")
i_main = names.index("main")
eq(dex.string_at(i_main)[1], len("main"), "utf16_size 等于 UTF-16 码元数")

# 类型与原型
types = [dex.type_at(i) for i in range(dex.type_ids_size)]
ok("LHello;" in types and "Ljava/lang/Object;" in types, "type_ids 含两个类描述符")
pm = [dex.proto_at(i) for i in range(dex.proto_ids_size)]
eq({x["return_type"] for x in pm}, {"V"}, "两个 proto 的返回类型都是 V")
with_params = [x for x in pm if x["params"]]
eq(len(with_params), 1, "只有一个 proto 带参数")
eq(with_params[0]["params"], ["Ljava/lang/String;"], "proto 的参数 type_list 回读")
eq([x for x in pm if not x["params"]][0]["params"], [], "无参数 proto 的 parameters_off 为 0")

# 方法与类
meths = [dex.method_at(i) for i in range(dex.method_ids_size)]
by_name = {m["name"]: m for m in meths}
ok("main" in by_name and by_name["main"]["class"] == "LHello;", "method_id 回读")
eq(dex.class_defs_size, 1, "只有一个 class_def")
cd = dex.class_def_at(0)
eq(cd["class"], "LHello;", "class_def.class_idx 解析")
eq(cd["superclass"], "Ljava/lang/Object;", "class_def.superclass_idx 解析")
eq(cd["source_file_idx"], NO_INDEX, "无源文件信息时 source_file_idx 是 NO_INDEX")
eq(cd["interfaces_off"], 0, "无接口时 interfaces_off 为 0")
eq(cd["static_values_off"], 0, "无静态初值时 static_values_off 为 0")

# class_data_item：method_idx_diff 是相对前一个元素的差值（官方 encoded_method）
cdata = dex.class_data_at(cd["class_data_off"])
eq(cdata["static_fields_size"], 0, "static_fields_size")
eq(cdata["instance_fields_size"], 0, "instance_fields_size")
eq(cdata["virtual_methods_size"], 0, "virtual_methods_size")
eq(cdata["direct_methods_size"], 3, "direct_methods_size 是 3")
idx = [m["method_idx"] for m in cdata["direct_methods"]]
eq(idx, sorted(idx), "direct_methods 的 method_idx 递增（差分累积）")
ok(idx[1] > idx[0], "第二个方法索引大于第一个（差值非 0，差分生效）")
eq(set(idx), {m_ctor, m_extra, m_main}, "差分还原出的 method_idx 与原始集合一致")
# abstract/native 的 code_off 为 0（官方：如果此方法是 abstract 或 native，则该值为 0）
zero_code = [m for m in cdata["direct_methods"] if m["code_off"] == 0]
eq(len(zero_code), 1, "native/abstract 方法的 code_off 是 0")

# code_item
code = dex.code_at(cdata["direct_methods"][0]["code_off"])
eq(code["registers_size"], 1, "code_item.registers_size")
eq(code["tries_size"], 0, "code_item.tries_size")
eq(code["debug_info_off"], 0, "无调试信息时 debug_info_off 是 0")
eq(code["insns"], [INSN_RETURN_VOID], "code_item.insns 回读")
eq(code["insns_size"], 1, "insns_size 以 16 位代码单元计")
# 官方 padding 规则：两个条件同时成立才有 2 字节填充
eq(code_item_padding(0, 1), 0, "tries_size 为 0 时无 padding")
eq(code_item_padding(0, 3), 0, "tries_size 为 0 时 insns 奇数也无 padding")
eq(code_item_padding(1, 2), 0, "tries_size 非零但 insns 偶数时无 padding")
eq(code_item_padding(1, 3), 1, "tries_size 非零且 insns 奇数时有 2 字节 padding")
eq(code_item_units(0, 1), 9, "code_item 头部 8 单元 + 1 单元指令")
eq(code_item_units(1, 3), 12, "含 padding 时 code_item 共 12 单元")

# 按 method_idx 定位 main（构造时被打乱顺序，class_data 已按升序排好）
entry_main = [m for m in cdata["direct_methods"] if m["method_idx"] == m_main][0]
code2 = dex.code_at(entry_main["code_off"])
eq(code2["insns"], [INSN_CONST_STRING, 0x0000, INSN_RETURN_VOID], "多指令 code_item 回读")
eq(code2["insns_size"], 3, "insns_size 是 16 位代码单元数（const-string 占 3 个单元）")

print("PASS %d 项断言全部通过" % PASS[0])
