# -*- coding: utf-8 -*-
"""HTTP/2 二进制分帧 —— 模型层（帧格式部分）

原文依据（RFC 9113 全文实读）：

§4.1
    HTTP Frame { Length (24), Type (8), Flags (8), Reserved (1),
                 Stream Identifier (31), Frame Payload (..) }
    * "The 9 octets of the frame header are not included in this value."
    * 未经 SETTINGS 放宽前 Length "MUST NOT be sent ... greater than
      2^14 (16,384)"
    * "Implementations MUST ignore and discard frames of unknown types."
    * Reserved: "MUST remain unset (0x00) when sending and MUST be ignored
      when receiving"
    * Unused flags: "MUST be ignored on receipt and MUST be left unset
      (0x00) when sending"
    * Stream 0x00 "is reserved for frames that are associated with the
      connection as a whole"

§4.2
    SETTINGS_MAX_FRAME_SIZE 取值区间 2^14 .. 2^24-1
"""

DATA, HEADERS, PRIORITY, RST_STREAM, SETTINGS = 0x0, 0x1, 0x2, 0x3, 0x4
PUSH_PROMISE, PING, GOAWAY, WINDOW_UPDATE, CONTINUATION = 0x5, 0x6, 0x7, 0x8, 0x9

FRAME_NAMES = {
    DATA: "DATA", HEADERS: "HEADERS", PRIORITY: "PRIORITY",
    RST_STREAM: "RST_STREAM", SETTINGS: "SETTINGS",
    PUSH_PROMISE: "PUSH_PROMISE", PING: "PING", GOAWAY: "GOAWAY",
    WINDOW_UPDATE: "WINDOW_UPDATE", CONTINUATION: "CONTINUATION",
}

DEFAULT_MAX_FRAME = 1 << 14          # 16384
MIN_MAX_FRAME = 1 << 14
MAX_MAX_FRAME = (1 << 24) - 1
HEADER_LEN = 9


class FrameError(Exception):
    pass


def encode_frame(ftype, flags, stream_id, payload=b"", reserved=0):
    """组帧：Length(24) + Type(8) + Flags(8) + R(1)|StreamID(31) + payload"""
    if len(payload) > MAX_MAX_FRAME:
        raise FrameError("payload 超过 2^24-1")
    if reserved not in (0, 1):
        raise FrameError("reserved 只能是 1 位")
    if not 0 <= stream_id <= 0x7FFFFFFF:
        raise FrameError("stream id 必须落在 31 位")
    hdr = (len(payload).to_bytes(3, "big")
           + bytes([ftype & 0xFF, flags & 0xFF])
           + ((reserved << 31) | stream_id).to_bytes(4, "big"))
    return hdr + payload


class Frame:
    __slots__ = ("length", "type", "flags", "reserved", "stream_id", "payload")

    def __init__(self, length, ftype, flags, reserved, stream_id, payload):
        self.length = length
        self.type = ftype
        self.flags = flags
        self.reserved = reserved
        self.stream_id = stream_id
        self.payload = payload

    @property
    def name(self):
        return FRAME_NAMES.get(self.type, "UNKNOWN(0x%02x)" % self.type)

    def __repr__(self):
        return "<Frame %s len=%d flags=0x%02x sid=%d>" % (
            self.name, self.length, self.flags, self.stream_id)


def decode_frame(buf, pos=0):
    """解一帧，返回 (Frame, 新的 pos)。不消费不足一帧的尾部。"""
    if len(buf) - pos < HEADER_LEN:
        raise FrameError("不足 9 字节帧头")
    length = int.from_bytes(buf[pos:pos + 3], "big")
    ftype = buf[pos + 3]
    flags = buf[pos + 4]
    word = int.from_bytes(buf[pos + 5:pos + 9], "big")
    reserved = (word >> 31) & 1          # 收到时 MUST be ignored
    stream_id = word & 0x7FFFFFFF
    end = pos + HEADER_LEN + length
    if len(buf) < end:
        raise FrameError("payload 不完整")
    return Frame(length, ftype, flags, reserved, stream_id,
                 buf[pos + HEADER_LEN:end]), end


def check_frame_size(length, max_frame_size):
    """超过对端通告的 SETTINGS_MAX_FRAME_SIZE → FRAME_SIZE_ERROR"""
    return length <= max_frame_size
