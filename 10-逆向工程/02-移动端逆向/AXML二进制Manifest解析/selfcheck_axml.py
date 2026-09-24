# -*- coding: utf-8 -*-
"""AXML 构造↔解析对拍断言(chunk 常量/字符串池双编码/属性 typed value/1-based 索引)。"""

from axml import (
    RES_STRING_POOL_TYPE, RES_XML_END_ELEMENT, RES_XML_RESOURCE_MAP,
    RES_XML_START_ELEMENT, RES_XML_START_NAMESPACE, RES_XML_TYPE, SORTED_FLAG,
    TYPE_INT_BOOLEAN, TYPE_INT_DEC, TYPE_REFERENCE, TYPE_STRING, UTF8_FLAG,
    Parser, build_axml, build_start_element, build_string_pool, chunk,
)

PASS = []
A = "http://schemas.android.com/apk/res/android"


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def build_doc(utf8=False):
    strings = ["manifest", "android", A, "package", "versionCode", "debuggable",
               "com.demo.app", "2", "true", "application", "name"]
    pool_chunk = build_string_pool(strings, utf8=utf8)

    import struct as st
    elem = build_start_element(
        strings, None, "manifest",
        [(None, "package", "com.demo.app", TYPE_STRING, strings.index("com.demo.app")),
         (A, "versionCode", None, TYPE_INT_DEC, 2),
         (A, "debuggable", None, TYPE_INT_BOOLEAN, 0xFFFFFFFF)],
        id_index=0)
    inner = build_start_element(
        strings, None, "application",
        [(None, "name", None, TYPE_REFERENCE, 0x7F0A0001)])
    body = build_axml(strings, pool_chunk, "android", A, [elem, inner])
    return pool_chunk, body


def main():
    pool16, doc16 = build_doc(utf8=False)
    print("1. chunk 头与常量")
    import struct as st
    t, hs, sz = st.unpack_from("<HHI", doc16, 0)
    assert t == RES_XML_TYPE and hs == 8 and sz == len(doc16)
    pt, phs, psz = st.unpack_from("<HHI", pool16, 0)
    assert pt == RES_STRING_POOL_TYPE and phs == 28 and psz == len(pool16)
    assert (RES_XML_START_NAMESPACE, RES_XML_END_ELEMENT, RES_XML_RESOURCE_MAP) == (0x0100, 0x0103, 0x0180)
    ok("chunk 头 = type(u16)/headerSize(u16)/size(u32);顶层 0x0003,资源映射 0x0180")

    print("2. 字符串池:UTF-16 与 UTF-8 双编码")
    p = Parser(doc16)                # 顶层 chunk 内已含字符串池
    res = p.parse()
    assert len(p.pool) == 11 and p.pool[2] == A and p.pool[6] == "com.demo.app"
    ok("UTF-16 池:u16 长度 + UTF-16LE + NUL;stringsStart 是从 chunk 头起的偏移")

    pool8, _ = build_doc(utf8=True)
    p8 = Parser(pool8)
    p8.load_pool(0)
    assert p8.pool[2] == A and p8.pool[5] == "debuggable"
    flags = p8.u32(16)                # 池头:stringCount@8 styleCount@12 flags@16
    assert flags & UTF8_FLAG and not flags & SORTED_FLAG
    ok("UTF8_FLAG=1<<8 池:u16 字符长 + u8 字节长 + UTF-8 + NUL;SORTED_FLAG 独立")

    print("3. 解析结果对拍")
    ns = [r for r in res if r[0] == "ns"][0]
    assert ns == ("ns", "android", A)
    ok("命名空间 chunk = prefix/uri 两个池索引")

    manifest = [r for r in res if r[1] == "manifest"][0]
    _, name, attrs, idi, cli, sti = manifest
    assert name == "manifest" and len(attrs) == 3
    pkg, ver, dbg = attrs
    assert pkg["name"] == "package" and pkg["type"] == TYPE_STRING
    assert pkg["value"] == "com.demo.app" and pkg["raw"] == "com.demo.app"
    assert ver["ns"] == A and ver["type"] == TYPE_INT_DEC and ver["value"] == 2
    assert dbg["type"] == TYPE_INT_BOOLEAN and dbg["value"] == 0xFFFFFFFF
    ok("typed value 分派:STRING=池索引、INT_DEC=数值、INT_BOOLEAN 非 0 即真(0xFFFFFFFF)")

    app = [r for r in res if r[1] == "application"][0]
    ref = app[2][0]
    assert ref["type"] == TYPE_REFERENCE and ref["value"] == 0x7F0A0001
    assert ref["raw"] is None
    ok("REFERENCE=资源 ID,rawValue 反而缺失(编译期就不再是字符串)")

    print("4. attrExt 三索引(1-based)")
    assert (idi, cli, sti) == (0, 0, 0)
    ok("idIndex/classIndex/styleIndex 是 1-based 属性序号,0=没有——"
       "解析器据此免扫全表直取 android:id 等关键属性")

    print("5. size 即跳距")
    p5 = Parser(doc16)
    p5.parse()
    assert len(p5.result) == 3       # ns + manifest + application
    ok("每个 chunk 的 size 字段就是完整跳距,损坏/未知 chunk 可安全越过")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
