"""QUIC(RFC 9000)变长整数与帧层的编解码模型。

口径全部来自实际抓取并阅读的 RFC 9000 全文(见 README「参考资料」):
  * §16 变长整数:2MSB 决定长度 `length = 1 << prefix`,可用位 6/14/30/62。
  * §12.4 帧:帧类型**必须**用最短编码;未知类型 -> FRAME_ENCODING_ERROR;
    报文负载至少要有一帧,否则 PROTOCOL_VIOLATION。
  * §19.3.1 ACK Range:`largest = previous_smallest - gap - 2`,
    「gap 里的包数比 Gap 字段的编码值多 1」,算出负数 -> FRAME_ENCODING_ERROR。

本模块**不做**加解密、头部保护与流控,只覆盖帧层以下的编码。
"""

MAX_1 = (1 << 6) - 1            # 63
MAX_2 = (1 << 14) - 1           # 16383
MAX_4 = (1 << 30) - 1           # 1073741823
MAX_8 = (1 << 62) - 1           # 4611686018427387903

PREFIX_TO_LEN = {0: 1, 1: 2, 2: 4, 3: 8}

FRAME_ENCODING_ERROR = "FRAME_ENCODING_ERROR"
PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"

# 表 3 的一部分(取本 demo 断言用到的那些)
PADDING = 0x00
PING = 0x01
ACK = 0x02
ACK_ECN = 0x03
RESET_STREAM = 0x04
STOP_SENDING = 0x05
CRYPTO = 0x06
NEW_TOKEN = 0x07
STREAM = 0x08                    # 0x08-0x0f
MAX_DATA = 0x10
MAX_STREAM_DATA = 0x11
MAX_STREAMS = 0x12               # 0x12-0x13
CONNECTION_CLOSE = 0x1c          # 0x1c-0x1d

KNOWN_FRAME_TYPES = (
    [PADDING, PING, ACK, ACK_ECN, RESET_STREAM, STOP_SENDING, CRYPTO,
     NEW_TOKEN, MAX_DATA, MAX_STREAM_DATA]
    + list(range(0x08, 0x10)) + list(range(0x12, 0x1c)) + [0x1c, 0x1d, 0x1e]
)

# 无内容的帧
EMPTY_FRAMES = {PADDING, PING}


class QuicError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__(kind if not detail else "%s: %s" % (kind, detail))
        self.kind = kind


def varint_len_for(value):
    """最短编码长度。"""
    if value <= MAX_1:
        return 1
    if value <= MAX_2:
        return 2
    if value <= MAX_4:
        return 4
    if value <= MAX_8:
        return 8
    raise QuicError(FRAME_ENCODING_ERROR, "value out of range")


def varint_encode(value, length=None):
    """编码;length 为 None 时取最短编码。"""
    if value < 0:
        raise QuicError(FRAME_ENCODING_ERROR, "negative")
    if length is None:
        length = varint_len_for(value)
    if length not in (1, 2, 4, 8):
        raise QuicError(FRAME_ENCODING_ERROR, "bad length")
    prefix = {1: 0, 2: 1, 4: 2, 8: 3}[length]
    if value > PREFIX_TO_LEN_MAX[prefix]:
        raise QuicError(FRAME_ENCODING_ERROR, "value too big for length")
    out = bytearray(length)
    out[0] = (prefix << 6) | ((value >> (8 * (length - 1))) & 0x3F)
    for i in range(1, length):
        out[i] = (value >> (8 * (length - 1 - i))) & 0xFF
    return bytes(out)


PREFIX_TO_LEN_MAX = {0: MAX_1, 1: MAX_2, 2: MAX_4, 3: MAX_8}


def varint_decode(data, pos=0):
    """解码,返回 (value, newpos)。实现 RFC 9000 附录 A.1 的伪码。"""
    if pos >= len(data):
        raise QuicError(FRAME_ENCODING_ERROR, "truncated: no first byte")
    first = data[pos]
    prefix = first >> 6
    length = 1 << prefix
    if pos + length > len(data):
        raise QuicError(FRAME_ENCODING_ERROR, "truncated: need %d bytes" % length)
    v = first & 0x3F
    for i in range(1, length):
        v = (v << 8) + data[pos + i]
    return v, pos + length


def is_shortest_encoding(raw):
    """判断给定字节序列是不是该值的**最短**编码(§12.4 对帧类型的要求)。"""
    try:
        value, consumed = varint_decode(raw, 0)
    except QuicError:
        return False
    return consumed == len(raw) and raw == varint_encode(value)


def decode_ack_ranges(largest, first_ack_range, ranges):
    """解码 ACK 的区间序列,返回 [(smallest, largest), ...] 升序无重叠。

    ranges 是 [(gap, ack_range_length), ...]。
    """
    out = []
    smallest = largest - first_ack_range
    if smallest < 0 or largest < 0:
        raise QuicError(FRAME_ENCODING_ERROR, "negative packet number")
    out.append((smallest, largest))
    for gap, ack_range_length in ranges:
        # largest = previous_smallest - gap - 2
        nxt_largest = smallest - gap - 2
        nxt_smallest = nxt_largest - ack_range_length
        if nxt_largest < 0 or nxt_smallest < 0:
            raise QuicError(FRAME_ENCODING_ERROR, "negative packet number")
        out.append((nxt_smallest, nxt_largest))
        smallest = nxt_smallest
    return out


def gap_packet_count(gap):
    """「The number of packets in the gap is one higher than the encoded
    value of the Gap field.」"""
    return gap + 1


def parse_frames(payload):
    """把一包负载切成帧,返回 [(type, payload_bytes), ...]。

    只处理「无内容的帧」与「未知/未建模的有参数帧」两种情形:后者按字段数
    不够建模,遇到时抛 FRAME_ENCODING_ERROR 并标注未建模。
    """
    if not payload:
        raise QuicError(PROTOCOL_VIOLATION, "packet payload has no frames")
    frames = []
    pos = 0
    while pos < len(payload):
        ftype, after = varint_decode(payload, pos)
        nbytes = after - pos
        if nbytes != len(varint_encode(ftype)):
            raise QuicError(FRAME_ENCODING_ERROR,
                            "frame type 0x%x not shortest" % ftype)
        pos = after
        if ftype in EMPTY_FRAMES:
            frames.append((ftype, b""))
            continue
        if ftype not in KNOWN_FRAME_TYPES:
            raise QuicError(FRAME_ENCODING_ERROR, "unknown frame type 0x%x" % ftype)
        raise QuicError(FRAME_ENCODING_ERROR,
                        "frame 0x%x not modelled in this demo" % ftype)
    return frames
