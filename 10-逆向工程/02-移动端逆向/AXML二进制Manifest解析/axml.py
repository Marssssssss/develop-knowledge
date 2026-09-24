# -*- coding: utf-8 -*-
"""AXML(Android 二进制 XML)最小实现:构造 + 解析同一套常量对拍。

口径(实读源):AOSP frameworks/base libs/androidfw/include/androidfw/ResourceTypes.h
  (LineageOS lineage-19.1 镜像,与 AOSP 同源):
  chunk 类型常量、ResChunk_header、ResStringPool_header(SORTED/UTF8 标志)、
  ResXMLTree_attrExt(idIndex 等三索引 1-based)、ResXMLTree_attribute、Res_value 类型枚举
"""

import struct

RES_STRING_POOL_TYPE = 0x0001
RES_XML_TYPE = 0x0003
RES_XML_START_NAMESPACE = 0x0100
RES_XML_END_NAMESPACE = 0x0101
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103
RES_XML_RESOURCE_MAP = 0x0180

SORTED_FLAG = 1 << 0
UTF8_FLAG = 1 << 8

TYPE_REFERENCE = 0x01
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_BOOLEAN = 0x12


def chunk(ctype, header_size, body):
    """size = 头(8 字节 ResChunk_header) + body;headerSize 字段可大于 8(如字符串池 28)。"""
    return struct.pack("<HHI", ctype, header_size, 8 + len(body)) + body


def utf16_str(s):
    """AXML 字符串编码:u16 长度(高位=1 时再读一个 u16 拼接) + UTF-16LE + NUL(u16)。"""
    enc = s.encode("utf-16-le")
    n = len(s)
    if n > 0x7FFF:
        head = struct.pack("<H", (n >> 16) | 0x8000) + struct.pack("<H", n & 0xFFFF)
    else:
        head = struct.pack("<H", n)
    return head + enc + b"\x00\x00"


def build_string_pool(strings, utf8=False):
    """28 字节头 + 索引数组 + 字符串数据;stringsStart 是『从 chunk 头起』的偏移。"""
    count = len(strings)
    data = b""
    offsets = []
    for s in strings:
        offsets.append(len(data))
        if utf8:
            b = s.encode("utf-8")
            data += struct.pack("<H", len(s)) + bytes([len(b)]) + b + b"\x00"
        else:
            data += utf16_str(s)
    idx = b"".join(struct.pack("<I", o) for o in offsets)
    strings_start = 28 + 4 * count
    body = struct.pack("<IIIII", count, 0, UTF8_FLAG if utf8 else 0,
                       strings_start, 0) + idx + data
    return chunk(RES_STRING_POOL_TYPE, 28, body)


def build_start_element(pool, ns, name, attrs, id_index=0, class_index=0, style_index=0):
    """headerSize=36(header 8 + lineNumber/comment 8 + attrExt 20);每个属性 20 字节。"""
    p = lambda s: pool.index(s)
    node = struct.pack("<II", 1, 0xFFFFFFFF)          # lineNumber, comment(-1)
    ext = struct.pack("<IIHHHHHH", p(ns) if ns else 0xFFFFFFFF, p(name),
                      20, 20, len(attrs), id_index, class_index, style_index)
    body = b""
    for a_ns, a_name, a_raw, dtype, dval in attrs:
        raw = p(a_raw) if a_raw is not None else 0xFFFFFFFF
        body += struct.pack("<IIIHBBI", p(a_ns) if a_ns else 0xFFFFFFFF,
                            p(a_name), raw, 8, 0, dtype, dval)
    return chunk(RES_XML_START_ELEMENT, 36, node + ext + body)


def build_axml(pool_strings, pool_chunk, ns_prefix, ns_uri, elements):
    """顶层 RES_XML_TYPE chunk 的 body = 字符串池 + 命名空间 + 元素序列。"""
    ns_chunk = chunk(RES_XML_START_NAMESPACE, 16,
                     struct.pack("<II", pool_strings.index(ns_prefix),
                                 pool_strings.index(ns_uri)))
    return chunk(RES_XML_TYPE, 8, pool_chunk + ns_chunk + b"".join(elements))


class Parser:
    """顺序走 chunk:size 字段即跳距;字符串按池索引解析。"""

    def __init__(self, data):
        self.data = data
        self.pool = []
        self.result = []

    def u16(self, o):
        return struct.unpack_from("<H", self.data, o)[0]

    def u32(self, o):
        return struct.unpack_from("<I", self.data, o)[0]

    def load_pool(self, off):
        count, _styles, flags, strings_start, _st = struct.unpack_from("<IIIII", self.data, off + 8)
        utf8 = bool(flags & UTF8_FLAG)
        for i in range(count):
            idx = self.u32(off + 28 + i * 4)
            doff = off + strings_start + idx   # 索引值是相对字符串数据区(stringsStart)的偏移
            if utf8:
                self.u16(doff)                      # 字符长度(字节数前还有一个 u8)
                blen = self.data[doff + 2]
                self.pool.append(self.data[doff + 3: doff + 3 + blen].decode("utf-8"))
            else:
                n = self.u16(doff)
                if n & 0x8000:                      # 长度拼接:高 16 位在高位置 1
                    n = ((n & 0x7FFF) << 16) | self.u16(doff + 2)
                    doff += 2
                raw = self.data[doff + 2: doff + 2 + n * 2]
                self.pool.append(raw.decode("utf-16-le"))

    def parse(self):
        off = 0
        assert self.u16(off) == RES_XML_TYPE
        total = self.u32(off + 4)
        o = 8
        while o < total:
            ctype, hsize, size = self.u16(o), self.u16(o + 2), self.u32(o + 4)
            if ctype == RES_STRING_POOL_TYPE:
                self.load_pool(o)
            elif ctype == RES_XML_START_NAMESPACE:
                pre, uri = self.u32(o + 8), self.u32(o + 12)
                self.result.append(("ns", self.pool[pre], self.pool[uri]))
            elif ctype == RES_XML_START_ELEMENT:
                # attrExt 从 o+16 起(header 8 + lineNumber 4 + comment 4)
                (_ns, _n, astart, asize, acount,
                 idi, cli, sti) = struct.unpack_from("<IIHHHHHH", self.data, o + 16)
                name = self.pool[_n]
                _ = self.pool[_ns] if _ns != 0xFFFFFFFF else None
                attrs = []
                for i in range(acount):
                    a = o + hsize + i * asize       # attrExt 后逐属性排布
                    a_ns, a_name, a_raw = self.u32(a), self.u32(a + 4), self.u32(a + 8)
                    dtype, dval = self.data[a + 15], self.u32(a + 16)
                    val = self.pool[dval] if dtype == TYPE_STRING else dval
                    attrs.append({"ns": self.pool[a_ns] if a_ns != 0xFFFFFFFF else None,
                                  "name": self.pool[a_name],
                                  "raw": self.pool[a_raw] if a_raw != 0xFFFFFFFF else None,
                                  "type": dtype, "value": val})
                self.result.append(("elem", name, attrs, idi, cli, sti))
            o += size                                # size 即完整跳距
        return self.result
