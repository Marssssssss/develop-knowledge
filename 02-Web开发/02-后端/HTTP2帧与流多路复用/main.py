"""RFC 9113（HTTP/2）帧层 / 流控 与 RFC 7541（HPACK）的可执行模型。

官方口径逐条对应：

帧（RFC 9113 §4.1）：
    HTTP Frame { Length (24), Type (8), Flags (8), Reserved (1), Stream Identifier (31),
                 Frame Payload (..) }
「The 9 octets of the frame header are not included in this value.」
Reserved 位发送时必须为 0、接收时必须忽略；Stream Identifier 为 0 表示整条连接。

SETTINGS（§6.5.2）初始值与边界：
    0x01 HEADER_TABLE_SIZE        4096
    0x02 ENABLE_PUSH              1
    0x03 MAX_CONCURRENT_STREAMS   无限制（建议不小于 100）
    0x04 INITIAL_WINDOW_SIZE      2^16-1 = 65535
    0x05 MAX_FRAME_SIZE           2^14 = 16384，允许区间 [2^14, 2^24-1]
    0x06 MAX_HEADER_LIST_SIZE     无限制

流控（§6.9.2）官方例子：
「if the client sends 60 KB immediately on connection establishment and the server sets
 the initial window size to be 16 KB, the client will recalculate the available
 flow-control window to be -44 KB」
「A SETTINGS frame cannot alter the connection flow-control window.」

HPACK（RFC 7541 §5.1 / 附录 C）官方向量：
    10   用 5 位前缀 → 0x0A
    1337 用 5 位前缀 → 0x1F 0x9A 0x0A
    42   用 8 位前缀 → 0x2A
附录 C.3.1 首个请求的字节序列 → :method GET / :scheme http / :path / /
    :authority www.example.com，解码后动态表 [1] (s=57) :authority: www.example.com
条目大小 = len(name) + len(value) + 32（§4.1）
"""

from __future__ import annotations

import struct
from typing import Dict, List, Optional, Tuple

# --- §6.5.2 各设置的初始值 -------------------------------------------------
SETTINGS_HEADER_TABLE_SIZE = 0x01
SETTINGS_ENABLE_PUSH = 0x02
SETTINGS_MAX_CONCURRENT_STREAMS = 0x03
SETTINGS_INITIAL_WINDOW_SIZE = 0x04
SETTINGS_MAX_FRAME_SIZE = 0x05
SETTINGS_MAX_HEADER_LIST_SIZE = 0x06

INITIAL_VALUES: Dict[int, Optional[int]] = {
    SETTINGS_HEADER_TABLE_SIZE: 4096,
    SETTINGS_ENABLE_PUSH: 1,
    SETTINGS_MAX_CONCURRENT_STREAMS: None,      # 无限制
    SETTINGS_INITIAL_WINDOW_SIZE: 65535,
    SETTINGS_MAX_FRAME_SIZE: 16384,
    SETTINGS_MAX_HEADER_LIST_SIZE: None,        # 无限制
}

MAX_FRAME_SIZE_MIN = 1 << 14          # 16384
MAX_FRAME_SIZE_MAX = (1 << 24) - 1    # 16777215
MAX_WINDOW_SIZE = (1 << 31) - 1       # 超过即 FLOW_CONTROL_ERROR


class ConnectionError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code


# ---------------------------------------------------------------------------
# §4.1 帧头
# ---------------------------------------------------------------------------
FRAME_TYPES = {0x0: "DATA", 0x1: "HEADERS", 0x2: "PRIORITY", 0x3: "RST_STREAM",
               0x4: "SETTINGS", 0x5: "PUSH_PROMISE", 0x6: "PING", 0x7: "GOAWAY",
               0x8: "WINDOW_UPDATE", 0x9: "CONTINUATION"}


def pack_frame(frtype: int, flags: int, stream_id: int, payload: bytes) -> bytes:
    """9 字节帧头 + payload。Length 只算 payload，不含帧头。"""
    if not 0 <= frtype <= 0xFF:
        raise ValueError("type 必须是 8 位")
    if not 0 <= flags <= 0xFF:
        raise ValueError("flags 必须是 8 位")
    if not 0 <= stream_id <= 0x7FFFFFFF:
        raise ValueError("stream id 必须是 31 位")
    return (struct.pack(">I", len(payload))[1:] + bytes([frtype, flags]) +
            struct.pack(">I", stream_id) + payload)


def unpack_frame(raw: bytes) -> Tuple[int, int, int, int, bytes]:
    """返回 (length, type, flags, stream_id, payload)。Reserved 位被丢弃（必须忽略）。"""
    if len(raw) < 9:
        raise ConnectionError("FRAME_SIZE_ERROR", "帧头不足 9 字节")
    length = (raw[0] << 16) | (raw[1] << 8) | raw[2]
    # 帧头布局：0-2 Length、3 Type、4 Flags、5-8 是 Reserved(1) + Stream ID(31)
    # Reserved 是字节 5 的最高位：发送时必须为 0、接收时必须忽略，故直接掩掉
    stream_id = struct.unpack(">I", raw[5:9])[0] & 0x7FFFFFFF
    payload = raw[9:9 + length]
    if len(payload) != length:
        raise ConnectionError("FRAME_SIZE_ERROR", "payload 长度与 Length 字段不符")
    return length, raw[3], raw[4], stream_id, payload


def check_frame_size(length: int, peer_max_frame_size: int) -> None:
    """§4.2：超过对端通告的 SETTINGS_MAX_FRAME_SIZE → FRAME_SIZE_ERROR。"""
    if length > peer_max_frame_size:
        raise ConnectionError("FRAME_SIZE_ERROR",
                              f"{length} > {peer_max_frame_size}")


def check_settings_max_frame_size(v: int) -> None:
    if not MAX_FRAME_SIZE_MIN <= v <= MAX_FRAME_SIZE_MAX:
        raise ConnectionError("PROTOCOL_ERROR", f"MAX_FRAME_SIZE={v} 越界")


# ---------------------------------------------------------------------------
# §6.9 流控
# ---------------------------------------------------------------------------
class FlowControl:
    """连接窗口 + 每流窗口。SETTINGS 只能改流窗口，连接窗口只能靠 WINDOW_UPDATE。"""

    def __init__(self, initial_window: int = INITIAL_VALUES[SETTINGS_INITIAL_WINDOW_SIZE]):
        self.initial_window = initial_window
        self.connection_window = initial_window
        self.stream_window: Dict[int, int] = {}

    def open_stream(self, sid: int) -> None:
        self.stream_window[sid] = self.initial_window

    def send(self, sid: int, size: int) -> int:
        """发送受流控的帧；返回实际可发送量（受两级窗口共同限制，可为 0）。"""
        allowed = max(0, min(self.connection_window, self.stream_window.get(sid, 0)))
        n = min(allowed, size)
        self.connection_window -= n
        self.stream_window[sid] -= n
        return n

    def window_update(self, sid: Optional[int], increment: int) -> None:
        """sid 为 None 表示作用于连接。"""
        if sid is None:
            self.connection_window += increment
            if self.connection_window > MAX_WINDOW_SIZE:
                raise ConnectionError("FLOW_CONTROL_ERROR", "连接窗口超过 2^31-1")
        else:
            self.stream_window[sid] = self.stream_window.get(sid, 0) + increment
            if self.stream_window[sid] > MAX_WINDOW_SIZE:
                raise ConnectionError("FLOW_CONTROL_ERROR", "流窗口超过 2^31-1")

    def apply_initial_window_change(self, new_value: int) -> None:
        """§6.9.2：所有已存在的流窗口按**差值**调整，可能变负。"""
        if new_value > MAX_WINDOW_SIZE:
            raise ConnectionError("FLOW_CONTROL_ERROR", "INITIAL_WINDOW_SIZE 超过 2^31-1")
        delta = new_value - self.initial_window
        self.initial_window = new_value
        for sid in self.stream_window:
            self.stream_window[sid] += delta


# ---------------------------------------------------------------------------
# RFC 7541 §5.1 HPACK 整数表示
# ---------------------------------------------------------------------------
def hpack_encode_int(value: int, prefix_bits: int) -> bytes:
    """N 位前缀整数编码：小于 2^N-1 直接放前缀，否则前缀置全 1 后按 128 进制续字节。"""
    if value < 0:
        raise ValueError("HPACK 整数不能为负")
    limit = (1 << prefix_bits) - 1
    if value < limit:
        return bytes([value])
    out = bytearray([limit])
    value -= limit
    while value >= 128:
        out.append((value % 128) + 128)
        value //= 128
    out.append(value)
    return bytes(out)


def hpack_decode_int(data: bytes, pos: int, prefix_bits: int) -> Tuple[int, int]:
    """返回 (值, 新位置)。"""
    limit = (1 << prefix_bits) - 1
    value = data[pos] & limit
    if value < limit:
        return value, pos + 1
    m = 0
    pos += 1
    while True:
        b = data[pos]
        value += (b & 127) << m
        m += 7
        pos += 1
        if not (b & 128):
            break
    return value, pos


# ---------------------------------------------------------------------------
# RFC 7541 附录 A 静态表（前 61 项，逐项照抄规范 Table 1）
# ---------------------------------------------------------------------------
STATIC_TABLE: List[Tuple[str, str]] = [
    (":authority", ""), (":method", "GET"), (":method", "POST"),
    (":path", "/"), (":path", "/index.html"), (":scheme", "http"),
    (":scheme", "https"), (":status", "200"), (":status", "204"),
    (":status", "206"), (":status", "304"), (":status", "400"),
    (":status", "404"), (":status", "500"), ("accept-charset", ""),
    ("accept-encoding", "gzip, deflate"), ("accept-language", ""),
    ("accept-ranges", ""), ("accept", ""), ("access-control-allow-origin", ""),
    ("age", ""), ("allow", ""), ("authorization", ""), ("cache-control", ""),
    ("content-disposition", ""), ("content-encoding", ""), ("content-language", ""),
    ("content-length", ""), ("content-location", ""), ("content-range", ""),
    ("content-type", ""), ("cookie", ""), ("date", ""), ("etag", ""),
    ("expect", ""), ("expires", ""), ("from", ""), ("host", ""),
    ("if-match", ""), ("if-modified-since", ""), ("if-none-match", ""),
    ("if-range", ""), ("if-unmodified-since", ""), ("last-modified", ""),
    ("link", ""), ("location", ""), ("max-forwards", ""), ("proxy-authenticate", ""),
    ("proxy-authorization", ""), ("range", ""), ("referer", ""), ("refresh", ""),
    ("retry-after", ""), ("server", ""), ("set-cookie", ""),
    ("strict-transport-security", ""), ("transfer-encoding", ""), ("user-agent", ""),
    ("vary", ""), ("via", ""), ("www-authenticate", ""),
]

ENTRY_OVERHEAD = 32      # §4.1：每条动态表条目额外计 32 字节


def entry_size(name: str, value: str) -> int:
    return len(name) + len(value) + ENTRY_OVERHEAD


class HpackDecoder:
    """极简解码器：支持索引表示、带索引的字面量、静态/动态表管理。

    Huffman 编码未实现（附录 B 的码表有 257 项），故本模型只处理
    「字符串以原始字节表示」的情形 —— 与附录 C.2/C.3 的无 Huffman 示例一致。
    """

    def __init__(self, max_size: int = INITIAL_VALUES[SETTINGS_HEADER_TABLE_SIZE]):
        self.max_size = max_size
        self.dynamic: List[Tuple[str, str]] = []   # 索引 1 是最新（表头）
        self.size = 0

    def lookup(self, index: int) -> Tuple[str, str]:
        if index <= 0:
            raise ConnectionError("PROTOCOL_ERROR", "索引 0 非法")
        if index <= len(STATIC_TABLE):
            return STATIC_TABLE[index - 1]
        d = index - len(STATIC_TABLE) - 1
        if d >= len(self.dynamic):
            raise ConnectionError("PROTOCOL_ERROR", f"索引 {index} 越界")
        return self.dynamic[d]

    def add(self, name: str, value: str) -> None:
        self.dynamic.insert(0, (name, value))
        self.size += entry_size(name, value)
        self.evict()

    def evict(self) -> None:
        while self.size > self.max_size and self.dynamic:
            n, v = self.dynamic.pop()
            self.size -= entry_size(n, v)

    def decode(self, data: bytes) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        pos = 0
        while pos < len(data):
            b = data[pos]
            if b & 0x80:                          # 1xxxxxxx 索引表示
                idx, pos = hpack_decode_int(data, pos, 7)
                out.append(self.lookup(idx))
            elif b & 0x40:                        # 01xxxxxx 带索引的字面量
                idx, pos = hpack_decode_int(data, pos, 6)
                if idx:
                    name = self.lookup(idx)[0]     # 索引名 + 字面值
                else:
                    name, pos = self._read_str(data, pos)
                value, pos = self._read_str(data, pos)
                out.append((name, value))
                self.add(name, value)
            else:
                raise ConnectionError("PROTOCOL_ERROR", f"未支持的表示 0x{b:02x}")
        return out

    @staticmethod
    def _read_str(data: bytes, pos: int) -> Tuple[str, int]:
        length, pos = hpack_decode_int(data, pos, 7)
        raw = data[pos:pos + length]
        return raw.decode("utf-8"), pos + length
