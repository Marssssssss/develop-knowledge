"""DEX 文件格式：按 AOSP 官方《Dalvik 可执行文件格式》实现的编码/解码与最小文件构造。

结构对照（官方文档章节）：
  LEB128 / uleb128p1  -> "LEB128"
  header_item         -> "项和相关结构 / header_item"
  string_id_item      -> string_ids 区段
  string_data_item    -> data 区段（MUTF-8）
  proto_id_item / type_list / class_def_item / class_data_item / code_item
参考：https://source.android.google.cn/docs/core/dalvik/dex-format
"""

import struct

from dex_leb128 import uleb128_decode, mutf8_decode, mutf8_to_str

# ---------- 常量（官方文档「位字段、字符串和常量定义」） ----------

DEX_FILE_MAGIC = b"dex\n039\x00"          # 039 版：0x64 0x65 0x78 0x0a 0x30 0x33 0x39 0x00
ENDIAN_CONSTANT = 0x12345678
REVERSE_ENDIAN_CONSTANT = 0x78563412
NO_INDEX = 0xFFFFFFFF                      # uint NO_INDEX == -1（按有符号解读）

HEADER_SIZE_V40 = 0x70                     # v40 及以下：112 字节
HEADER_SIZE_V41 = 0x78                     # v41 及以上：120 字节（新增 container_size/header_offset）

# map_list 类型代码（官方「类型代码」表）
TYPE_HEADER_ITEM = 0x0000
TYPE_STRING_ID_ITEM = 0x0001
TYPE_TYPE_ID_ITEM = 0x0002
TYPE_PROTO_ID_ITEM = 0x0003
TYPE_FIELD_ID_ITEM = 0x0004
TYPE_METHOD_ID_ITEM = 0x0005
TYPE_CLASS_DEF_ITEM = 0x0006
TYPE_CALL_SITE_ID_ITEM = 0x0007
TYPE_METHOD_HANDLE_ITEM = 0x0008
TYPE_MAP_LIST = 0x1000
TYPE_TYPE_LIST = 0x1001
TYPE_ANNOTATION_SET_REF_LIST = 0x1002
TYPE_ANNOTATION_SET_ITEM = 0x1003
TYPE_CLASS_DATA_ITEM = 0x2000
TYPE_CODE_ITEM = 0x2001
TYPE_STRING_DATA_ITEM = 0x2002
TYPE_DEBUG_INFO_ITEM = 0x2003
TYPE_ANNOTATION_ITEM = 0x2004
TYPE_ENCODED_ARRAY_ITEM = 0x2005
TYPE_ANNOTATIONS_DIRECTORY_ITEM = 0x2006
TYPE_HIDDENAPI_CLASS_DATA_ITEM = 0xF000

# 定长项的大小（官方类型代码表最后一列）
FIXED_ITEM_SIZE = {
    TYPE_HEADER_ITEM: 0x70,
    TYPE_STRING_ID_ITEM: 0x04,
    TYPE_TYPE_ID_ITEM: 0x04,
    TYPE_PROTO_ID_ITEM: 0x0C,
    TYPE_FIELD_ID_ITEM: 0x08,
    TYPE_METHOD_ID_ITEM: 0x08,
    TYPE_CLASS_DEF_ITEM: 0x20,
    TYPE_CALL_SITE_ID_ITEM: 0x04,
    TYPE_METHOD_HANDLE_ITEM: 0x08,
}



def code_item_padding(tries_size, insns_size):
    """官方：padding 只有在 tries_size 非零**且** insns_size 是奇数时才存在（2 字节）。"""
    return 1 if (tries_size != 0 and insns_size % 2 == 1) else 0


def code_item_units(tries_size, insns_size):
    """code_item 除 insns 之外占用的 16 位代码单元数（含可选 padding）。"""
    return 8 + insns_size + code_item_padding(tries_size, insns_size)


# ---------- 解析 ----------

class DexFile(object):

    def __init__(self, blob):
        self.blob = blob
        self._parse_header()
        self._parse_map()

    def _u32(self, off):
        return struct.unpack_from("<I", self.blob, off)[0]

    def _parse_header(self):
        assert self.blob[0:8] == DEX_FILE_MAGIC, "magic 不是 dex\\n039\\0"
        self.checksum = self._u32(8)
        self.signature = self.blob[12:32]
        self.file_size = self._u32(32)
        self.header_size = self._u32(36)
        self.endian_tag = self._u32(40)
        assert self.endian_tag in (ENDIAN_CONSTANT, REVERSE_ENDIAN_CONSTANT)
        self.map_off = self._u32(52)
        self.string_ids_size = self._u32(56)
        self.string_ids_off = self._u32(60)
        self.type_ids_size = self._u32(64)
        self.type_ids_off = self._u32(68)
        self.proto_ids_size = self._u32(72)
        self.proto_ids_off = self._u32(76)
        self.field_ids_size = self._u32(80)
        self.method_ids_size = self._u32(88)
        self.method_ids_off = self._u32(92)
        self.class_defs_size = self._u32(96)
        self.class_defs_off = self._u32(100)
        self.data_size = self._u32(104)
        self.data_off = self._u32(108)

    def _parse_map(self):
        size = self._u32(self.map_off)
        self.map = []
        for i in range(size):
            off = self.map_off + 4 + i * 12
            t, _unused, count, item_off = struct.unpack_from("<HHII", self.blob, off)
            self.map.append({"type": t, "size": count, "offset": item_off})

    # --- 字符串 ---
    def string_at(self, i):
        data_off = self._u32(self.string_ids_off + 4 * i)
        utf16_len, pos = uleb128_decode(self.blob, data_off)
        raw = bytearray()
        while self.blob[pos] != 0x00:
            raw.append(self.blob[pos])
            pos += 1
        assert utf16_len == len(mutf8_decode(bytes(raw))), "utf16_size 与解码长度不符"
        return mutf8_to_str(bytes(raw)), utf16_len

    def type_at(self, i):
        return self.string_at(self._u32(self.type_ids_off + 4 * i))[0]

    def proto_at(self, i):
        base = self.proto_ids_off + 12 * i
        shorty, ret, params_off = struct.unpack_from("<III", self.blob, base)
        params = []
        if params_off:
            n = self._u32(params_off)
            for k in range(n):
                params.append(self._u32(params_off + 4) if False else
                              struct.unpack_from("<H", self.blob, params_off + 4 + 2 * k)[0])
        return {"shorty": self.string_at(shorty)[0],
                "return_type": self.type_at(ret),
                "params": [self.type_at(t) for t in params]}

    def method_at(self, i):
        base = self.method_ids_off + 8 * i
        cls, name, proto = struct.unpack_from("<HHI", self.blob, base)
        return {"class": self.type_at(cls), "name": self.string_at(name)[0],
                "proto": self.proto_at(proto)}

    def class_def_at(self, i):
        base = self.class_defs_off + 32 * i
        (cls, flags, sup, ifaces, src, anno, cdata, svals) = struct.unpack_from("<IIIIIIII", self.blob, base)
        return {"class": self.type_at(cls), "access_flags": flags,
                "superclass": NO_INDEX if sup == NO_INDEX else self.type_at(sup),
                "interfaces_off": ifaces, "source_file_idx": src,
                "annotations_off": anno, "class_data_off": cdata,
                "static_values_off": svals}

    def class_data_at(self, off):
        pos = off
        static_n, pos = uleb128_decode(self.blob, pos)
        inst_n, pos = uleb128_decode(self.blob, pos)
        direct_n, pos = uleb128_decode(self.blob, pos)
        virtual_n, pos = uleb128_decode(self.blob, pos)
        methods = []
        prev = 0
        for _ in range(direct_n):
            diff, pos = uleb128_decode(self.blob, pos)
            flags, pos = uleb128_decode(self.blob, pos)
            code_off, pos = uleb128_decode(self.blob, pos)
            prev += diff                    # method_idx_diff 是相对前一个元素的差值
            methods.append({"method_idx": prev, "access_flags": flags, "code_off": code_off})
        return {"static_fields_size": static_n, "instance_fields_size": inst_n,
                "direct_methods_size": direct_n, "virtual_methods_size": virtual_n,
                "direct_methods": methods}

    def code_at(self, off):
        (regs, ins, outs, tries) = struct.unpack_from("<HHHH", self.blob, off)
        debug = self._u32(off + 8)
        insns_size = self._u32(off + 12)
        pos = off + 16
        insns = [struct.unpack_from("<H", self.blob, pos + 2 * k)[0] for k in range(insns_size)]
        return {"registers_size": regs, "ins_size": ins, "outs_size": outs,
                "tries_size": tries, "debug_info_off": debug,
                "insns_size": insns_size, "insns": insns}
