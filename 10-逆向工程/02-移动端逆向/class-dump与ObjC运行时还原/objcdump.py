# -*- coding: utf-8 -*-
"""按 class-dump(CDObjectiveC2Processor.m) 的读取顺序,从 __objc_classlist 静态还原 ObjC 类。

结构对照(实读源):
  class-dump Source/CDObjectiveC2Processor.m : loadClasses/loadCategories/loadProtocols,
    objc2Class 读取序 isa→superclass→cache→vtable→data(value&~7,bit0=isSwiftClass)
  objc4 runtime/objc-runtime-new.h : method_t{name,types,imp} / class_ro_t 字段序 /
    ivar_t.alignment()(alignment_raw==~0 → 1<<WORD_SHIFT) / RO_META=(1<<0) /
    FAST_DATA_MASK
"""

import struct

WORD_SHIFT = 3          # 64 位
RO_META = 1 << 0        # objc-runtime-new.h:359
SWIFT_BIT = 0x1         # class-dump: value & 0x1
DATA_MASK = ~7          # class-dump: value & ~7(FAST_DATA_MASK 低位掩码)
ENTSIZE_MASK = ~3       # class-dump: entsize & ~3(低 2 位是 fixup 标记)


class Image:
    """一段假的 __DATA_CONST:记录 section 表,支持按绝对偏移取指针/C 字符串。"""

    def __init__(self):
        self.buf = bytearray()
        self.sections = {}

    def alloc(self, data, align=8):
        while len(self.buf) % align:
            self.buf += b"\x00"
        off = len(self.buf)
        self.buf += data
        return off

    def add_section(self, name, data, align=8):
        off = self.alloc(data, align)
        self.sections[name] = (off, len(data))
        return off

    def u32(self, off):
        return struct.unpack_from("<I", self.buf, off)[0]

    def u64(self, off):
        return struct.unpack_from("<Q", self.buf, off)[0]

    def ptr(self, off):
        v = self.u64(off)
        return v if v != 0 else None

    def cstr(self, off):
        end = self.buf.index(b"\x00", off)
        return self.buf[off:end].decode("utf-8")


class ObjCImage(Image):
    """构造 + 解析,两端共用同一套结构常量。"""

    # ---- 构造 ----

    def put_class(self, name, superclass, meta_ptr, ro_off_flagged, base):
        """objc_class:isa|superclass|cache|vtable|data(带低位标志)|r1|r2|r3(64 位)。"""
        return self.alloc(struct.pack("<8Q", meta_ptr, superclass or 0, 0, 0,
                                      ro_off_flagged, 0, 0, 0))

    def put_ro(self, flags, name, base_methods=0, ivars=0, props=0,
               instance_start=8, instance_size=16):
        """class_ro_t 字段序,64 位含 reserved。"""
        return self.alloc(struct.pack(
            "<IIIIQQQQQQQ", flags, instance_start, instance_size, 0,
            0, name, base_methods, 0, ivars, 0, props), align=8)

    def put_method_list(self, methods, marker=0):
        """methods: [(name_off, types_off, imp)]。经典 3 指针 entsize=24;
        marker 模拟低 2 位 fixup 标记(解析端须 &~3 掩掉)。"""
        body = b"".join(struct.pack("<QQQ", n, t, i) for n, t, i in methods)
        return self.alloc(struct.pack("<II", 24 | marker, len(methods)) + body)

    def put_ivar_list(self, ivars):
        """ivar_t{offset*,name,type,alignment_raw,size} = 32 字节。"""
        body = b"".join(struct.pack("<QQQII", *iv) for iv in ivars)
        return self.alloc(struct.pack("<II", 32, len(ivars)) + body)

    def put_property_list(self, props):
        body = b"".join(struct.pack("<QQ", n, a) for n, a in props)
        return self.alloc(struct.pack("<II", 16, len(props)) + body)

    # ---- 解析(镜像 class-dump 的 cursor 读取序) ----

    def load_classes(self):
        out = []
        off, size = self.sections["__objc_classlist"]
        for i in range(size // 8):
            out.append(self.class_at(self.u64(off + i * 8)))
        return out

    def class_at(self, addr):
        isa = self.u64(addr)
        superclass = self.u64(addr + 8)
        value = self.u64(addr + 32)
        data = value & DATA_MASK
        c = {"isa": isa, "superclass": superclass,
             "is_swift": (value & SWIFT_BIT) != 0,
             "data_flags": value & 7}
        ro = data
        flags, inst_start, inst_size = (self.u32(ro), self.u32(ro + 4), self.u32(ro + 8))
        name_off, methods_off = self.u64(ro + 24), self.u64(ro + 32)
        ivars_off, props_off = self.u64(ro + 48), self.u64(ro + 64)
        c.update(flags=flags, is_meta=(flags & RO_META) != 0,
                 instance_start=inst_start, instance_size=inst_size,
                 name=self.cstr(name_off),
                 methods=self.method_list(methods_off),
                 ivars=self.ivar_list(ivars_off),
                 properties=self.property_list(props_off))
        return c

    def method_list(self, off):
        if not off:
            return []
        entsize = self.u32(off) & ENTSIZE_MASK
        count = self.u32(off + 4)
        out = []
        for i in range(count):
            e = off + 8 + i * entsize
            out.append({"name": self.cstr(self.u64(e)),
                        "types": self.cstr(self.u64(e + 8)),
                        "imp": self.u64(e + 16)})
        return out

    def ivar_list(self, off):
        if not off:
            return []
        entsize, count = self.u32(off), self.u32(off + 4)
        out = []
        for i in range(count):
            e = off + 8 + i * entsize
            raw = self.u32(e + 24)
            out.append({"name": self.cstr(self.u64(e + 8)),
                        "type": self.cstr(self.u64(e + 16)),
                        "alignment_raw": raw,
                        "alignment": (1 << WORD_SHIFT) if raw == 0xFFFFFFFF else (1 << raw),
                        "size": self.u32(e + 28)})
        return out

    def property_list(self, off):
        if not off:
            return []
        entsize, count = self.u32(off), self.u32(off + 4)
        return [{"name": self.cstr(self.u64(off + 8 + i * entsize)),
                 "attributes": self.cstr(self.u64(off + 8 + i * entsize + 8))}
                for i in range(count)]

    def load_categories(self):
        off, size = self.sections["__objc_catlist"]
        out = []
        for i in range(size // 8):
            a = self.u64(off + i * 8)
            out.append({"name": self.cstr(self.u64(a)),
                        "cls": self.u64(a + 8),
                        "instance_methods": self.method_list(self.u64(a + 16)),
                        "class_methods": self.method_list(self.u64(a + 24))})
        return out

    def load_protocols(self):
        off, size = self.sections["__objc_protolist"]
        out = []
        for i in range(size // 8):
            a = self.u64(off + i * 8)
            out.append({"name": self.cstr(self.u64(a + 8)),
                        "instance_methods": self.method_list(self.u64(a + 24))})
        return out
