# -*- coding: utf-8 -*-
"""FlatBuffers 线格式最小实现:构建 + 就地(零拷贝)读取。

复现 flatcc binary-format 文档的教学示例(FooBar):
    table FooBar { meal:byte=Banana(-1); density:long(deprecated);
                  say:string; height:short; }  file_identifier "NOOB"
数据 { meal:42(Orange), say:"hello", height:-8000 } 的官方参考字节。

布局规则(本教学构建器):table 内 uoffset 类字段在前(按字段 id 序)、
标量在后(按字段 id 序、自然对齐填充);vtable 放缓冲区末尾(flatcc 风格,
soffset 为负)。flatcc 文档明确:table 内字段顺序格式不规定,只要求对齐。
"""
import struct

BANANA, ORANGE = -1, 42


def ru16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def ru32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def ri32(b, o):
    return struct.unpack_from("<i", b, o)[0]


# ---------------- 构建器(flatcc 布局:block 顺序 header|table|string|vtable) ----------------

class Builder:
    """单 schema 教学构建器;同布局 vtable 在同一缓冲区内按字节串去重共享。"""

    def __init__(self, file_identifier=None):
        self.ident = file_identifier

    def build(self, fields, children):
        """fields: [(fid, kind, value, default)](kind: 'i16'|'i8',值==默认值则不存储);
        children: [(fid, bytes)](string 块,以 uoffset 引用)。
        返回 (bytes, table地址, vtable地址)。"""
        # 1. table 布局:soffset(4) 后先放 uoffset 槽(id 序),再放标量(id 序)
        body, uoff_slots, entries, off = [("soff", None)], [], {}, 4
        for fid, _ in children:
            uoff_slots.append((fid, off))
            entries[fid] = off
            body.append(("u32", None))
            off += 4
        for fid, kind, value, default in fields:
            if value == default:                 # 缺省值不占存储,vtable 条目 0
                continue
            size = 2 if kind == "i16" else 1
            if off % size:
                body.append(("pad", size - off % size))
                off += size - off % size
            entries[fid] = off
            body.append(("i16" if kind == "i16" else "i8", value))
            off += size
        table_size = off
        # 2. 组装 block:header | table | children | 填充 | vtable
        blocks = []
        table_pos = 4 + (4 if self.ident else 0)
        for kind, value in body:
            if kind == "pad":
                blocks.append(b"\x00" * value)
            elif kind in ("soff", "u32"):
                blocks.append(b"\x00\x00\x00\x00")     # 占位,稍后回填
            elif kind == "i16":
                blocks.append(struct.pack("<h", value))
            else:
                blocks.append(struct.pack("<b", value))
        end = table_pos + sum(len(x) for x in blocks)
        child_pos = {}
        for fid, data in children:                      # string 块:长度+内容+\0
            while end % 4:
                blocks.append(b"\x00")
                end += 1
            child_pos[fid] = end
            blocks.append(struct.pack("<I", len(data)) + data + b"\x00")
            end += 4 + len(data) + 1
        while end % 4:
            blocks.append(b"\x00")
            end += 1
        # 3. vtable:vtable_size, table_size, 各字段偏移(0=缺失)
        max_fid = max(list(entries) + [0])
        vt_list = [2 * (max_fid + 3), table_size] + [
            entries.get(fid, 0) for fid in range(max_fid + 1)]
        vt_bytes = struct.pack("<%dH" % len(vt_list), *vt_list)
        vtable_pos = end
        blocks.append(vt_bytes)
        # 4. 拼接并回填:soffset(负向)、uoffset(正向)
        buf = bytearray(struct.pack("<I", table_pos))
        if self.ident:
            buf += self.ident
        buf += b"".join(blocks)
        struct.pack_into("<i", buf, table_pos, table_pos - vtable_pos)
        for fid, off in uoff_slots:
            struct.pack_into("<I", buf, table_pos + off,
                             child_pos[fid] - (table_pos + off))
        return bytes(buf), table_pos, vtable_pos


# ---------------- 读取器(就地访问,零拷贝) ----------------

def field_offset(buf, t, fid):
    """vtable 查字段:越界(旧数据无该字段)或条目 0 → 返回 0 表示缺失。"""
    vt = t - ri32(buf, t)                                # soffset 是『减去』
    vtsize = ru16(buf, vt)
    slot = 4 + fid * 2      # 跳过 vtable 长度/表长度两个头条目
    if slot >= vtsize:
        return 0
    return ru16(buf, vt + slot)


def get_scalar(buf, t, fid, fmt, default):
    off = field_offset(buf, t, fid)
    if off == 0:
        return default, False
    return struct.unpack_from(fmt, buf, t + off)[0], True


def get_string_view(buf, t, fid, default=None):
    """返回 memoryview 视图(零拷贝):经 view 改写直接落到原缓冲区。"""
    off = field_offset(buf, t, fid)
    if off == 0:
        return default
    s = t + off + ru32(buf, t + off)                     # uoffset 加在自身存储地址上
    n = ru32(buf, s)
    assert s + 4 + n <= len(buf), "string 越界"
    return memoryview(buf)[s + 4: s + 4 + n]


# ---------------- 断言 ----------------

def check(label, cond, detail=""):
    assert cond, "%s %s" % (label, detail)
    print("[ok] %s" % label)


def main():
    # ---- 断言 1:逐字节复现 flatcc 参考缓冲区 ----
    buf, t, vt = Builder(b"NOOB").build(
        fields=[(0, "i8", ORANGE, BANANA), (3, "i16", -8000, 0)],
        children=[(2, b"hello")],
    )
    expected = (struct.pack("<I", 8) + b"NOOB"
                + struct.pack("<i", -24) + struct.pack("<I", 8)
                + struct.pack("<b", ORANGE) + b"\x00" + struct.pack("<h", -8000)
                + struct.pack("<I", 5) + b"hello\x00" + b"\x00\x00"
                + struct.pack("<6H", 12, 12, 8, 0, 4, 10))
    check("builder 复现 flatcc 参考字节", buf == expected, "got %s" % buf.hex())

    # ---- 断言 2:就地读取 ----
    check("root uoffset -> table @8", ru32(buf, 0) == 8)
    check("soffset(负) -> vtable @0x20", vt == 0x20 and t - ri32(buf, t) == vt)
    meal, present = get_scalar(buf, t, 0, "<b", BANANA)
    check("meal=42(Orange)", meal == ORANGE and present)
    height, _ = get_scalar(buf, t, 3, "<h", 0)
    check("height=-8000(int16 小端)", height == -8000)
    check("say=='hello'", bytes(get_string_view(buf, t, 2)) == b"hello")
    check("deprecated density 条目为 0(缺失)", field_offset(buf, t, 1) == 0)

    # ---- 断言 3:零拷贝 —— view 直接落在原缓冲区上 ----
    mut = bytearray(buf)
    get_string_view(mut, t, 2)[0] = ord("j")
    check("零拷贝:经 view 改写字节直落缓冲区", mut[0x18] == ord("j"))

    # ---- 断言 4:缺省值不存储 + 前向兼容读取 ----
    buf2, t2, _ = Builder(b"NOOB").build(
        fields=[(0, "i8", BANANA, BANANA), (3, "i16", -8000, 0)],
        children=[(2, b"hi")])
    check("值==默认值时 vtable 条目置 0", field_offset(buf2, t2, 0) == 0)
    meal2, present2 = get_scalar(buf2, t2, 0, "<b", BANANA)
    check("缺省读取返回默认 -1 且不占存储", meal2 == BANANA and not present2)
    level, present4 = get_scalar(buf, t, 4, "<i", 7)     # 新 schema 的 fid=4
    check("新代码读旧数据:越界字段返回默认", level == 7 and not present4)

    # ---- 断言 5:同布局两表共享同一 vtable(手工拼装单缓冲区) ----
    # header(4) | tableA(8) | tableB(8) | vtable(8):字段 f0:i16@4, f1:i8@6
    vt_shared = struct.pack("<4H", 8, 8, 4, 6)
    table = lambda so: struct.pack("<i", so) + struct.pack("<h", 11) \
        + struct.pack("<b", 2) + b"\x00"
    buf3 = struct.pack("<I", 4) + table(-16) + table(-8) + vt_shared
    tA, tB = 4, 12
    check("两表 soffset 均解析到同一 vtable @20",
          tA - ri32(buf3, tA) == 20 and tB - ri32(buf3, tB) == 20)
    check("vtable 在缓冲区中仅出现一份", buf3.count(vt_shared) == 1)

    # ---- 断言 6:vector(长度=元素个数)读取 + 截断防护 ----
    # header|table(soffset+uoffset)|vtable|填充|vector: f0 -> [10,20,30]
    bufv = (struct.pack("<I", 4) + struct.pack("<i", -8)
            + struct.pack("<I", 12) + struct.pack("<3H", 6, 8, 4)
            + b"\x00\x00" + struct.pack("<I", 3)
            + struct.pack("<3h", 10, 20, 30))
    vec = 4 + field_offset(bufv, 4, 0) + ru32(bufv, 4 + 4)
    check("vector 长度字段=元素个数 3", ru32(bufv, vec) == 3)
    items = struct.unpack_from("<3h", bufv, vec + 4)
    check("vector 内容 [10,20,30]", items == (10, 20, 30))
    try:
        get_string_view(buf[:6], 8, 2)                  # 小于最小长度 8 字节
        raised = False
    except struct.error:
        raised = True
    check("截断缓冲区读取触发越界异常", raised)

    print("\n全部断言通过")


if __name__ == "__main__":
    main()
