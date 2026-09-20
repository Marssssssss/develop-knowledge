"""按 AOSP 官方 file layout 构造一个结构完整的 039 版 dex 文件。"""

import hashlib
import struct
import zlib

from dex_leb128 import (
    uleb128_encode, sleb128_encode, mutf8_encode, utf16_size, string_sort_key,
)
from dex_format import (
    DEX_FILE_MAGIC, ENDIAN_CONSTANT, NO_INDEX, HEADER_SIZE_V40,
    TYPE_HEADER_ITEM, TYPE_STRING_ID_ITEM, TYPE_TYPE_ID_ITEM, TYPE_PROTO_ID_ITEM,
    TYPE_METHOD_ID_ITEM, TYPE_CLASS_DEF_ITEM,
)

# ---------- 最小 DEX 构造 ----------

class DexBuilder(object):
    """按官方 file layout 拼一个结构完整的 039 版 dex。"""

    def __init__(self):
        self.strings = []
        self.types = []            # 描述符字符串
        self.protos = []           # (shorty, return_type, [param types])
        self.methods = []          # (class_type, name, proto_index)
        self.classes = []          # dict(class_idx, superclass_idx, access_flags,
                                   #      direct_methods=[(method_idx, flags, code)])
        self._data = bytearray()

    # --- 索引登记 ---
    def add_string(self, s):
        key = s
        if key not in self.strings:
            self.strings.append(key)
        return self.strings.index(key)

    def add_type(self, descriptor):
        idx = self.add_string(descriptor)
        if idx not in self.types:
            self.types.append(idx)
        return self.types.index(idx)

    def add_proto(self, shorty, return_type, params):
        proto = (self.add_string(shorty), self.add_type(return_type),
                 [self.add_type(p) for p in params])
        if proto not in self.protos:
            self.protos.append(proto)
        return self.protos.index(proto)

    def add_method(self, class_type, name, proto_index):
        item = (class_type, self.add_string(name), proto_index)
        if item not in self.methods:
            self.methods.append(item)
        return self.methods.index(item)

    def add_class(self, class_type, superclass_type, access_flags=0x0001,
                  direct_methods=()):
        self.classes.append({
            "class_idx": class_type,
            "superclass_idx": superclass_type if superclass_type is not None else NO_INDEX,
            "access_flags": access_flags,
            "direct_methods": list(direct_methods),
        })
        return len(self.classes) - 1

    # --- data 区段分配 ---
    def _alloc(self, blob, align=1):
        while len(self._data) % align:
            self._data.append(0x00)
        off = len(self._data)
        self._data += blob
        return off

    def build(self):
        # 官方要求 string_ids 按 UTF-16 码位值排序
        order = sorted(range(len(self.strings)),
                       key=lambda i: string_sort_key(self.strings[i]))
        remap = {old: new for new, old in enumerate(order)}
        strings = [self.strings[i] for i in order]
        types = [remap[t] for t in self.types]                       # 描述符的下标改指向新字符串表
        # type_ids 的下标次序不受字符串重排影响，故 protos/methods 里的 type 索引原样保留
        protos = [(remap[shorty], ret, params)
                  for (shorty, ret, params) in self.protos]
        methods = [(m[0], remap[m[1]], m[2]) for m in self.methods]

        header_size = HEADER_SIZE_V40
        map_items = []

        # 1) 先占好定长区段的位置
        string_ids_off = header_size
        type_ids_off = string_ids_off + 4 * len(strings)
        proto_ids_off = type_ids_off + 4 * len(types)
        method_ids_off = proto_ids_off + 12 * len(protos)
        class_defs_off = method_ids_off + 8 * len(methods)

        # 2) data 区段（相对 data 起点的偏移，稍后加上 data_off）
        data_off = class_defs_off + 32 * len(self.classes)
        data_off += (-data_off) % 4

        string_data_offs = []
        for s in strings:
            blob = uleb128_encode(utf16_size(s)) + mutf8_encode(s)
            string_data_offs.append(self._alloc(blob))

        type_list_offs = {}
        for i, (_, _, params) in enumerate(protos):
            if params:
                blob = struct.pack("<I", len(params)) + b"".join(
                    struct.pack("<H", t) for t in params)
                type_list_offs[i] = self._alloc(blob, align=4)
            else:
                type_list_offs[i] = 0

        code_offs = {}
        class_data_offs = []
        for cls in self.classes:
            entries = []
            for (m_idx, flags, insns) in cls["direct_methods"]:
                code_off = 0
                if insns:
                    registers = 1
                    blob = struct.pack("<HHHHI", registers, 0, 0, 0, 0)
                    blob += struct.pack("<I", len(insns))
                    blob += b"".join(struct.pack("<H", u) for u in insns)
                    if len(insns) % 2:          # tries_size==0 时无需 padding
                        pass
                    code_off = data_off + self._alloc(blob, align=4)
                # abstract / native 方法照样要登记，只是 code_off 为 0
                entries.append((m_idx, flags, code_off))
            # method_idx_diff 是「与列表中前一个元素的索引之差」，故列表必须按 method_idx 升序
            entries.sort(key=lambda e: e[0])
            blob = uleb128_encode(0)            # static_fields_size
            blob += uleb128_encode(0)           # instance_fields_size
            blob += uleb128_encode(len(entries))  # direct_methods_size
            blob += uleb128_encode(0)           # virtual_methods_size
            prev = 0
            for (m_idx, flags, code_off) in entries:
                blob += uleb128_encode(m_idx - prev)   # method_idx_diff 是差分
                blob += uleb128_encode(flags)
                blob += uleb128_encode(code_off)
                prev = m_idx
            class_data_offs.append(data_off + self._alloc(blob))

        # 3) 拼装定长区段
        string_ids = b"".join(struct.pack("<I", data_off + o) for o in string_data_offs)
        type_ids = b"".join(struct.pack("<I", t) for t in types)
        proto_ids = b""
        for i, (shorty, rt, params) in enumerate(protos):
            tl = type_list_offs.get(i, 0)
            proto_ids += struct.pack("<III", shorty, rt, (data_off + tl) if tl else 0)
        method_ids = b"".join(struct.pack("<HHI", m[0], m[1], m[2]) for m in methods)
        class_defs = b""
        for i, cls in enumerate(self.classes):
            class_defs += struct.pack("<IIIIIIII", cls["class_idx"], cls["access_flags"],
                                      cls["superclass_idx"], 0, NO_INDEX, 0,
                                      class_data_offs[i], 0)

        # 4) map_list（按 offset 升序且不重叠）
        def mi(t, count, off):
            return struct.pack("<HHII", t, 0, count, off)

        map_items.append((header_size, mi(TYPE_HEADER_ITEM, 1, 0)))
        if strings:
            map_items.append((string_ids_off, mi(TYPE_STRING_ID_ITEM, len(strings), string_ids_off)))
        if types:
            map_items.append((type_ids_off, mi(TYPE_TYPE_ID_ITEM, len(types), type_ids_off)))
        if protos:
            map_items.append((proto_ids_off, mi(TYPE_PROTO_ID_ITEM, len(protos), proto_ids_off)))
        if methods:
            map_items.append((method_ids_off, mi(TYPE_METHOD_ID_ITEM, len(methods), method_ids_off)))
        if self.classes:
            map_items.append((class_defs_off, mi(TYPE_CLASS_DEF_ITEM, len(self.classes), class_defs_off)))
        map_items.sort(key=lambda x: x[0])
        map_body = struct.pack("<I", len(map_items)) + b"".join(m for _, m in map_items)
        map_off = data_off + self._alloc(map_body, align=4)

        file_size = data_off + len(self._data)
        body = bytearray()
        body += b"\x00" * header_size
        body += string_ids + type_ids + proto_ids + method_ids + class_defs
        body += self._data

        header = bytearray(b"\x00" * header_size)
        header[0:8] = DEX_FILE_MAGIC
        struct.pack_into("<I", header, 8, 0)          # checksum 稍后填
        header[12:32] = b"\x00" * 20                  # signature 稍后填
        struct.pack_into("<I", header, 32, file_size)
        struct.pack_into("<I", header, 36, header_size)
        struct.pack_into("<I", header, 40, ENDIAN_CONSTANT)
        struct.pack_into("<I", header, 44, 0)         # link_size
        struct.pack_into("<I", header, 48, 0)         # link_off
        struct.pack_into("<I", header, 52, map_off)
        struct.pack_into("<I", header, 56, len(strings))
        struct.pack_into("<I", header, 60, string_ids_off)
        struct.pack_into("<I", header, 64, len(types))
        struct.pack_into("<I", header, 68, type_ids_off)
        struct.pack_into("<I", header, 72, len(protos))
        struct.pack_into("<I", header, 76, proto_ids_off)
        struct.pack_into("<I", header, 80, 0)         # field_ids_size
        struct.pack_into("<I", header, 84, 0)         # field_ids_off
        struct.pack_into("<I", header, 88, len(methods))
        struct.pack_into("<I", header, 92, method_ids_off)
        struct.pack_into("<I", header, 96, len(self.classes))
        struct.pack_into("<I", header, 100, class_defs_off)
        struct.pack_into("<I", header, 104, len(self._data))   # data_size
        struct.pack_into("<I", header, 108, data_off)          # data_off
        body[0:header_size] = header

        # 官方口径：signature 覆盖「除 magic、checksum 和 signature 之外」的内容；
        # checksum 覆盖「除 magic 和 checksum 之外」的内容 —— 即**包含** signature。
        # 因此必须先算 signature 写回，再算 checksum，否则两者互相破坏。
        body[12:32] = hashlib.sha1(bytes(body[32:])).digest()
        checksum = zlib.adler32(bytes(body[12:])) & 0xFFFFFFFF
        struct.pack_into("<I", body, 8, checksum)
        return bytes(body)

