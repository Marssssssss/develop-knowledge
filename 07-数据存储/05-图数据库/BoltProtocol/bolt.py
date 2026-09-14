# -*- coding: utf-8 -*-
"""Bolt 协议三层最小实现: 握手 / 分块传输 / PackStream.

依据 Neo4j Bolt Protocol 官方文档:
1. Handshake: 连接后客户端发 4 字节标识 60 60 B0 17 + 4 个大端 32 位
   协议版本(按偏好排序, 不足补 0); 服务器回 1 个 32 位选中版本, 0 = 无匹配并断连.
2. Chunking: 每条消息编码为 1..n 个 chunk(2 字节大端长度头 + 数据,
   单 chunk 上限 65535), 消息以 0x0000 结束; 接收方重组跨 chunk 的消息.
3. PackStream: marker byte 编码类型与规模(TINY_INT 单字节 / String 8x /
   List 9x / Dictionary Ax / Structure Bx + tag byte / 扩展 D0-DE).
"""
import struct

MAGIC = b"\x60\x60\xb0\x17"   # Bolt 协议标识
MAX_CHUNK = 0xFFFF            # 16 位长度头的理论上限

_SIZES8, _SIZES16, _SIZES32 = ((">B", 1), ">B", 1), (">H", 2), (">I", 4)


# ---------- 1. 握手 ----------

def build_handshake(versions):
    """客户端握手: magic + 恰好 4 个大端 32 位版本(不足补 0, 按偏好降序)."""
    assert 1 <= len(versions) <= 4
    vs = list(versions) + [0] * (4 - len(versions))
    return MAGIC + struct.pack(">4I", *vs)


def parse_handshake_response(data):
    """服务器 4 字节响应: 选中版本; 0x00000000 = 无匹配(服务器随即断连)."""
    (v,) = struct.unpack(">I", data)
    return v


def server_negotiate(client_versions, supported):
    """服务器侧: 按客户端偏好顺序取第一个双方支持的版本, 无匹配返回 0."""
    for v in client_versions:
        if v != 0 and v in supported:
            return v
    return 0


# ---------- 2. 分块传输 (Chunking) ----------

def chunk_message(payload, max_chunk=MAX_CHUNK):
    """消息 -> chunk 流: 每个 chunk = 2B 大端长度 + 数据; 尾接 00 00 结束标记."""
    assert len(payload) > 0
    out = bytearray()
    for i in range(0, len(payload), max_chunk):  # 大消息拆多 chunk
        part = payload[i:i + max_chunk]
        out += struct.pack(">H", len(part)) + part
    out += b"\x00\x00"                          # 0x0000: 消息边界标记
    return bytes(out)


class ChunkReader:
    """接收方: 从字节流逐 chunk 重组完整消息(TCP 无消息边界).

    实现要点: 先扫描确认整条消息(含 0x0000 结束标记)都已到达, 才一次性
    消费缓冲 —— 未收全时返回 None 且不动缓冲, 否则会丢已到达的 chunk.
    """

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data

    def next_message(self):
        """返回下一条完整消息的 bytes; 数据不足时返回 None(缓冲保持不变)."""
        msg = bytearray()
        pos = 0
        while True:
            if len(self.buf) - pos < 2:
                return None          # 连 chunk 头都没收全
            (size,) = struct.unpack(">H", self.buf[pos:pos + 2])
            if size == 0:            # 00 00 结束标记: 消息完整, 一次性消费
                del self.buf[:pos + 2]
                return bytes(msg)
            if len(self.buf) - pos < 2 + size:
                return None          # chunk 数据未收全, 等待更多字节
            msg += self.buf[pos + 2:pos + 2 + size]
            pos += 2 + size


# ---------- 3. PackStream ----------

def pack(value):
    """Python 值 -> PackStream 字节(marker + size + body 按需)."""
    if value is None:
        return b"\xc0"
    if value is True:
        return b"\xc3"
    if value is False:
        return b"\xc2"
    if isinstance(value, int):
        return pack_int(value)
    if isinstance(value, float):
        return b"\xc1" + struct.pack(">d", value)
    if isinstance(value, (bytes, bytearray)):
        data = bytes(value)
        return _sized((0xCC, 0xCD, 0xCE), len(data)) + data
    if isinstance(value, str):
        data = value.encode("utf-8")           # size = UTF-8 字节数而非字符数
        if len(data) < 16:
            return bytes([0x80 | len(data)]) + data
        return _sized((0xD0, 0xD1, 0xD2), len(data)) + data
    if isinstance(value, (list, tuple)):
        return _seq(0x90, (0xD4, 0xD5, 0xD6), value)
    if isinstance(value, dict):
        flat = [x for kv in value.items() for x in kv]
        return _seq(0xA0, (0xD8, 0xD9, 0xDA), flat)
    raise TypeError(type(value))


def pack_int(v):
    """最优整数表示: -16..127 单字节, 否则按宽度选 INT_8/16/32/64."""
    if -16 <= v <= 127:
        return struct.pack(">b", v)
    if -128 <= v <= 127:
        return b"\xc8" + struct.pack(">b", v)
    if -32768 <= v <= 32767:
        return b"\xc9" + struct.pack(">h", v)
    if -(1 << 31) <= v < (1 << 31):
        return b"\xca" + struct.pack(">i", v)
    return b"\xcb" + struct.pack(">q", v)


def _sized(markers, n):
    """按规模挑 marker: <=255 用 8bit / <=65535 用 16bit / 其余 32bit."""
    if n <= 0xFF:
        return markers[0] + struct.pack(">B", n)
    if n <= 0xFFFF:
        return markers[1] + struct.pack(">H", n)
    if n <= 0xFFFFFFFF:
        return markers[2] + struct.pack(">I", n)
    raise ValueError("size overflow")


def _seq(tiny, markers, items):
    if len(items) < 16:
        head = bytes([tiny | len(items)])
    else:
        head = _sized(markers, len(items))
    return head + b"".join(pack(x) for x in items)


# D-marker 解码表: marker -> (kind, size 字段宽度)
_D_TABLE = {
    0xCC: ("bytes", 1), 0xCD: ("bytes", 2), 0xCE: ("bytes", 4),
    0xD0: ("str", 1),   0xD1: ("str", 2),   0xD2: ("str", 4),
    0xD4: ("list", 1),  0xD5: ("list", 2),  0xD6: ("list", 4),
    0xD8: ("dict", 1),  0xD9: ("dict", 2),  0xDA: ("dict", 4),
}
_FMT = {1: ">B", 2: ">H", 4: ">I"}


def unpack(data):
    """PackStream 字节 -> (值, 消耗字节数)."""
    m = data[0]
    if m == 0xC0:
        return None, 1
    if m == 0xC2:
        return False, 1
    if m == 0xC3:
        return True, 1
    if m == 0xC1:
        return struct.unpack(">d", data[1:9])[0], 9
    if m in (0xC8, 0xC9, 0xCA, 0xCB):        # INT_8/16/32/64
        fmt = {0xC8: ">b", 0xC9: ">h", 0xCA: ">i", 0xCB: ">q"}[m]
        return struct.unpack(fmt, data[1:1 + struct.calcsize(fmt)])[0], \
            1 + struct.calcsize(fmt)
    if m <= 0x7F or m >= 0xF0:               # TINY_INT(正 00-7F / 负 F0-FF)
        return struct.unpack(">b", data[:1])[0], 1
    hi, lo = m >> 4, m & 0x0F
    if hi == 0x8:                            # String (tiny)
        return data[1:1 + lo].decode("utf-8"), 1 + lo
    if hi == 0x9:                            # List (tiny)
        vals, used = _unpack_seq(data[1:], lo)
        return vals, 1 + used
    if hi == 0xA:                            # Dictionary (tiny)
        vals, used = _unpack_seq(data[1:], lo * 2)
        return dict(zip(vals[::2], vals[1::2])), 1 + used
    if hi == 0xB:                            # Structure: marker + tag + fields
        tag = data[1]
        fields, used = _unpack_seq(data[2:], lo)
        return ("struct", tag, fields), 2 + used
    if m in _D_TABLE:
        kind, width = _D_TABLE[m]
        n = struct.unpack(_FMT[width], data[1:1 + width])[0]
        off = 1 + width
        if kind == "bytes":
            return bytes(data[off:off + n]), off + n
        if kind == "str":
            return data[off:off + n].decode("utf-8"), off + n
        vals, used = _unpack_seq(data[off:], n if kind == "list" else n * 2)
        return (vals if kind == "list"
                else dict(zip(vals[::2], vals[1::2]))), off + used
    raise ValueError(f"reserved/unknown marker 0x{m:02x}")


def _unpack_seq(data, count):
    vals, off = [], 0
    for _ in range(count):
        v, used = unpack(data[off:])
        vals.append(v)
        off += used
    return vals, off


def demo():
    # --- PackStream: 断言官方文档字节示例 ---
    assert pack(42) == b"\x2a"                        # TINY_INT 42 -> 2A
    assert pack(-16) == b"\xf0" and pack(-1) == b"\xff"
    assert pack(1.23) == b"\xc1\x3f\xf3\xae\x14\x7a\xe1\x47\xae"  # 官方 Float 示例
    assert pack("") == b"\x80" and pack("A") == b"\x81\x41"
    assert pack("ABCDEFGHIJKLMNOPQRSTUVWXYZ") == (
        b"\xd0\x1a" + b"ABCDEFGHIJKLMNOPQRSTUVWXYZ")   # 26 字节走 D0
    assert pack([1, 2, 3]) == b"\x93\x01\x02\x03"     # 官方 List 示例
    assert pack({"name": "neo"}) == b"\xa1\x84name\x83neo"
    assert unpack(pack({"a": [1, 2.5, "x"]}))[0] == {"a": [1, 2.5, "x"]}
    assert pack(2 ** 33) == b"\xcb" + (2 ** 33).to_bytes(8, "big")  # INT_64
    assert unpack(b"\x91\xc0\xc3\x81x")[0] == [None, True, "x"]     # 嵌套
    print("PackStream 官方字节示例断言全部通过")

    # --- 握手: 官方示例"客户端知 1/2/3, 服务器选 2" ---
    hs = build_handshake([3, 2, 1])
    assert hs[:4] == MAGIC
    assert parse_handshake_response(struct.pack(">I", 2)) == 2
    assert server_negotiate([3, 2, 1], {1, 2}) == 2     # 按客户端偏好顺序
    assert server_negotiate([5, 0, 0, 0], {4, 3}) == 0   # 0 = 无匹配 -> 断连
    print(f"握手字节: {hs.hex(' ', 1)}  (magic + 3/2/1/0)")

    # --- 分块: 官方 16 字节单 chunk 示例 ---
    payload = bytes(range(16))
    wire = chunk_message(payload)
    assert wire == b"\x00\x10" + payload + b"\x00\x00"
    # 20 字节消息拆两 chunk(官方示例: 16 + 4)
    wire2 = chunk_message(bytes(range(16)) + b"\x01\x02\x03\x04", max_chunk=16)
    assert wire2 == (b"\x00\x10" + bytes(range(16)) +
                     b"\x00\x04\x01\x02\x03\x04" + b"\x00\x00")

    # --- 接收方重组: 模拟 TCP 分段到达 ---
    r = ChunkReader()
    assert r.next_message() is None                   # 无数据
    for i in range(0, len(wire2), 3):                 # 每 3 字节一段(与 chunk 边界不对齐)
        r.feed(wire2[i:i + 3])
        if i + 3 < len(wire2):
            assert r.next_message() is None           # 未收全时必须返回 None
    assert r.next_message() == bytes(range(16)) + b"\x01\x02\x03\x04"
    print("分块重组: 消息跨 chunk + TCP 分段错位均正确重组")

    # --- 组合: 一条 RUN 消息(Structure tag=0x10) 的完整编码链路 ---
    run_msg = pack(Struct(0x10, ["RETURN 1", {}, {}]))       # RUN(query, params, extra)
    full = chunk_message(run_msg)
    r2 = ChunkReader()
    r2.feed(full)
    got, _ = unpack(r2.next_message())
    assert got == ("struct", 0x10, ["RETURN 1", {}, {}])
    print(f"RUN 消息完整链路(pack->chunk->feed->unpack): {len(full)} 字节")


if __name__ == "__main__":
    demo()
