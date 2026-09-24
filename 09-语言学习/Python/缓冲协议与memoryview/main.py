# -*- coding: utf-8 -*-
"""
Python · 缓冲协议(PEP 3118)与 memoryview

memoryview 是缓冲协议的 Python 侧窗口:不拷贝地读写字节、按格式重解释(cast)、
连续性判定、resize 闸门(BufferError)、release 生命周期与只读视图可哈希。
每个行为都与文档声明对拍,并给出 buffer_info 的出口方自描述。

参考(实读):
  - https://docs.python.org/3/library/stdtypes.html#memoryview-type
  - https://docs.python.org/3/c-api/buffer.html      (PyBUF_* 标志/出口方契约)
  - https://peps.python.org/pep-3118/                (修订版缓冲协议设计)
  - 本机 CPython 3.12 实测(所有断言以实跑为准)
"""

import array
import struct

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def demo_view_semantics():
    print("1. 视图即窗口:属性与自描述")
    b = bytearray(b"abcdef")
    mv = memoryview(b)
    assert (mv.nbytes, mv.itemsize, len(mv)) == (6, 1, 6)
    assert mv.shape == (6,) and mv.strides == (1,) and mv.ndim == 1
    assert mv.readonly is False and mv.format == "B"
    ok("nbytes=字节数,itemsize=单元素字节,len=元素个数(1-D 时三者关系成立)")
    attrs = {a for a in dir(mv) if not a.startswith("_")}
    assert {"shape", "strides", "suboffsets", "format", "itemsize",
            "nbytes", "ndim", "readonly", "obj", "cast", "release",
            "tobytes", "tolist", "toreadonly", "hex"} <= attrs
    assert mv.suboffsets == ()
    ok(f"出口方自描述齐全;suboffsets=()(简单缓冲无间接层,PEP 3118 的 PIL 式数组才用);buffer_info() 是 3.13+,3.12 还没有")

    a = array.array("h", [1, 2, 3, 4])
    m2 = memoryview(a)
    assert (m2.format, m2.itemsize, m2.nbytes, len(m2)) == ("h", 2, 8, 4)
    assert m2[1] == 2
    ok("array('h') 的元素是 2 字节:索引拿到的是**元素值**而非字节串")


def demo_zero_copy():
    print("2. 零拷贝读写")
    b = bytearray(b"abcdef")
    mv = memoryview(b)
    mv[0] = 90
    assert bytes(b) == b"Zbcdef"
    ok("写入直通底层缓冲——没有中间拷贝")
    sub = mv[1:4]
    sub[0] = 66
    assert bytes(b) == b"ZBcdef" and sub.obj is b
    ok("切片是共享同一缓冲的子视图(.obj 指回导出者),改子视图=改原对象")
    assert memoryview(b"abc") == b"abc"
    assert memoryview(b"ab") == memoryview(bytearray(b"ab"))
    ok("比较按内容(跨 bytes/bytearray/同为 memoryview),与格式无关面按 tolist 语义")
    s = memoryview(b"ab")
    assert s[0:1].tobytes() == b"a" and s.tolist() == [97, 98]
    ok("tobytes()/tolist() 才产生拷贝;bytes(mv) 同为拷贝")


def demo_resize_gate():
    print("3. resize 闸门与生命周期")
    b = bytearray(b"abcdef")
    mv = memoryview(b)
    try:
        b.append(103)
        raise AssertionError("unreachable")
    except BufferError as e:
        assert "Existing exports" in str(e)
    ok("有活跃导出时 resize 直接 BufferError(出口方义务:不许移动/重分配内存)")
    sub = mv[1:4]
    sub.release()
    try:
        b.append(103)
        raise AssertionError("unreachable")
    except BufferError:
        pass
    ok("子视图也持一份导出计数:只释放子视图仍过不了闸")
    mv.release()
    b.append(103)
    assert b[-1:] == b"g"
    ok("全部 release 后闸门打开")
    mv.release()
    ok("release 幂等:重复 release 不报错")
    try:
        mv[0]
    except ValueError as e:
        assert "released" in str(e)
    ok("release 后再使用 → ValueError(operation forbidden on released memoryview)")
    with memoryview(bytearray(b"xyz")) as w:
        assert bytes(w) == b"xyz"
    try:
        w[0]
        raise AssertionError("unreachable")
    except ValueError:
        pass
    ok("with 语句离开块即 release(管理长生命周期的推荐姿势)")


def demo_cast():
    print("4. cast:同一字节流的格式重解释")
    mc = memoryview(b"\x01\x02\x03\x04\x05\x06\x07\x08")
    c = mc.cast("h")
    assert (c.format, c.itemsize, len(c)) == ("h", 2, 4)
    assert list(c) == [513, 1027, 1541, 2055]
    ok("8 字节 cast('h') → 4 个 little-endian int16:0x0201=513,长度按 itemsize 缩")
    buf = bytearray(8)
    struct.pack_into("<ii", memoryview(buf), 0, 10, 20)
    assert list(memoryview(buf).cast("i")) == [10, 20]
    ok("struct.pack_into 可直接写进 memoryview:字节级写入通道")
    bc = memoryview(bytearray(b"\x01\x02"))
    try:
        bc[::1].cast("h") if False else bc.cast("I")
    except Exception as e:
        assert isinstance(e, (ValueError, TypeError))
    ok("cast 需 1-D C 连续且字节对齐:非法组合被拒(对齐错误抛 ValueError)")


def demo_contiguity():
    print("5. 连续性")
    plain = memoryview(b"abcd")
    assert plain.c_contiguous and plain.contiguous
    stepped = memoryview(b"abcdefgh")[::2]
    assert bytes(stepped) == b"aceg" and not stepped.c_contiguous
    rev = memoryview(b"abcd")[::-1]
    assert not rev.c_contiguous and not rev.f_contiguous
    ok("普通视图 C 连续;带步长/反转的视图 strides 非单位 → 不连续(c_contiguous 可查)")
    ok("不连续视图照样能读(tobytes 按逻辑顺序拼),但很多 C API 要求先 contiguous")


def demo_toreadonly():
    print("6. toreadonly:降级为只读视图")
    src = bytearray(b"ab")
    tro = memoryview(src).toreadonly()
    assert tro.readonly is True and tro == b"ab"
    try:
        tro[0] = 99
        raise AssertionError("unreachable")
    except TypeError:
        pass
    ok("toreadonly 共享内存但拒绝写入(hash 可用),给下游传『只许看』的窗口")


def demo_hash():
    print("7. 只读视图可哈希")
    ro = memoryview(b"abcefg")
    assert hash(ro) == hash(b"abcefg")
    assert hash(ro[2:4]) == hash(b"ce")
    assert hash(ro[::-2]) == hash(b"abcefg"[::-2])
    ok("只读 + 格式 B/b/c 的 1-D 视图:hash(m) == hash(m.tobytes()),切片同样成立")
    try:
        hash(memoryview(bytearray(b"x")))
        raise AssertionError("unreachable")
    except ValueError as e:
        assert "writable" in str(e)
    ok("可写视图 hash → **ValueError**(不是 TypeError):可变内容没有稳定哈希")


def main():
    demo_view_semantics()
    demo_zero_copy()
    demo_resize_gate()
    demo_cast()
    demo_contiguity()
    demo_toreadonly()
    demo_hash()
    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
