# -*- coding: utf-8 -*-
"""HPACK 头部压缩 —— 模型层（RFC 7541）

原文依据（RFC 7541 全文实读）：

§5.1 整数表示
    "If the integer value is small enough, i.e., strictly less than 2^N-1,
     it is encoded within the N-bit prefix."
    —— 是 **strictly less**，等于 2^N-1 时反而走多字节形式

§6.1 indexed header field：1 位前缀 + 7 位索引
    "The index value of 0 is not used. It MUST be treated as a decoding
     error if found in an indexed header field representation."

§4.1 条目大小 = name.length + value.length + 32

§4.3 / §4.4 逐出
    改小 max size：从尾部逐出直到 size <= max
    新增前：逐出到 size <= max - new_entry_size
    新条目大于 max：整表清空且不算错误
"""

# ---------------------------------------------------------------- 整数与串
def encode_int(value, n, prefix):
    """N 位前缀整数编码。prefix 是已填好的高 8-n 位模式位。"""
    if value < 0:
        raise ValueError("HPACK 整数非负")
    limit = (1 << n) - 1
    if value < limit:                     # strictly less than 2^N-1
        return bytes([prefix | value])
    out = bytearray([prefix | limit])
    v = value - limit
    while v >= 128:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def decode_int(buf, pos, n):
    """返回 (value, newpos)。"""
    limit = (1 << n) - 1
    if pos >= len(buf):
        raise ValueError("整数编码越界")
    v = buf[pos] & limit
    pos += 1
    if v < limit:
        return v, pos
    m = 0
    while True:
        if pos >= len(buf):
            raise ValueError("整数续字节越界")
        b = buf[pos]
        pos += 1
        v += (b & 0x7F) << m
        if not (b & 0x80):
            break
        m += 7
    return v, pos


def encode_str(s, huffman=False):
    """字符串字面量：H 位 + 7 位前缀长度。长度计的是**编码后**的字节数。"""
    raw = s.encode("utf-8") if isinstance(s, str) else s
    return encode_int(len(raw), 7, 0x80 if huffman else 0) + raw


def decode_str(buf, pos):
    """返回 (bytes, huffman, newpos)。Huffman 码表不在本 demo 实现范围。"""
    h = bool(buf[pos] & 0x80)
    n, pos = decode_int(buf, pos, 7)
    if len(buf) < pos + n:
        raise ValueError("字符串长度越界")
    return bytes(buf[pos:pos + n]), h, pos + n


# ---------------------------------------------------------------- 静态表
# RFC 7541 Appendix A，从 RFC 全文逐条抽取（61 项）
STATIC_TABLE = [
    (":authority", ""), (":method", "GET"), (":method", "POST"),
    (":path", "/"), (":path", "/index.html"), (":scheme", "http"),
    (":scheme", "https"), (":status", "200"), (":status", "204"),
    (":status", "206"), (":status", "304"), (":status", "400"),
    (":status", "404"), (":status", "500"), ("accept-charset", ""),
    ("accept-encoding", "gzip, deflate"), ("accept-language", ""),
    ("accept-ranges", ""), ("accept", ""), ("access-control-allow-origin", ""),
    ("age", ""), ("allow", ""), ("authorization", ""), ("cache-control", ""),
    ("content-disposition", ""), ("content-encoding", ""),
    ("content-language", ""), ("content-length", ""), ("content-location", ""),
    ("content-range", ""), ("content-type", ""), ("cookie", ""), ("date", ""),
    ("etag", ""), ("expect", ""), ("expires", ""), ("from", ""), ("host", ""),
    ("if-match", ""), ("if-modified-since", ""), ("if-none-match", ""),
    ("if-range", ""), ("if-unmodified-since", ""), ("last-modified", ""),
    ("link", ""), ("location", ""), ("max-forwards", ""),
    ("proxy-authenticate", ""), ("proxy-authorization", ""), ("range", ""),
    ("referer", ""), ("refresh", ""), ("retry-after", ""), ("server", ""),
    ("set-cookie", ""), ("strict-transport-security", ""),
    ("transfer-encoding", ""), ("user-agent", ""), ("vary", ""), ("via", ""),
    ("www-authenticate", ""),
]
STATIC_SIZE = len(STATIC_TABLE)          # 61

INDEXED = 0x80             # 1xxxxxxx
INCREMENTAL = 0x40         # 01xxxxxx
SIZE_UPDATE = 0x20         # 001xxxxx
NEVER_INDEXED = 0x10       # 0001xxxx
NO_INDEXING = 0x00         # 0000xxxx


def entry_size(name, value):
    """RFC 7541 §4.1：name + value + 32 字节开销"""
    return len(name) + len(value) + 32


class HPackError(Exception):
    pass


class Context:
    """编码器/解码器共享的 HPACK 上下文：静态表 + 动态表。"""

    def __init__(self, max_size=4096):
        self.max_size = max_size
        self.dyn = []                    # dyn[0] 对应索引 62（最新）
        self.size = 0

    def lookup(self, index):
        if index <= 0:
            raise HPackError("索引 0 必须判为解码错误")
        if index <= STATIC_SIZE:
            return STATIC_TABLE[index - 1]
        off = index - STATIC_SIZE - 1
        if off >= len(self.dyn):
            raise HPackError("动态表索引越界: %d" % index)
        return self.dyn[off]

    def find(self, name, value=None):
        for i, (n, v) in enumerate(self.dyn):
            if n == name and (value is None or v == value):
                return STATIC_SIZE + 1 + i
        for i, (n, v) in enumerate(STATIC_TABLE):
            if n == name and (value is None or v == value):
                return i + 1
        return None

    def evict_to(self, target):
        while self.size > target and self.dyn:
            n, v = self.dyn.pop()        # 从尾部（最旧）逐出
            self.size -= entry_size(n, v)

    def set_max_size(self, new_max):
        self.max_size = new_max
        self.evict_to(new_max)

    def add(self, name, value):
        esz = entry_size(name, value)
        if esz > self.max_size:
            self.dyn = []                # 大于 max 时整表清空
            self.size = 0
            return False
        self.evict_to(self.max_size - esz)
        self.dyn.insert(0, (name, value))
        self.size += esz
        return True


# ---------------------------------------------------------------- 编码
def encode_indexed(ctx, index):
    return encode_int(index, 7, INDEXED)


def encode_literal(ctx, name, value, mode="incremental", name_index=None):
    """mode: incremental / none / never"""
    if mode == "incremental":
        prefix, n = INCREMENTAL, 6
    elif mode == "never":
        prefix, n = NEVER_INDEXED, 4
    else:
        prefix, n = NO_INDEXING, 4
    if name_index:
        out = encode_int(name_index, n, prefix)
    else:
        out = encode_int(0, n, prefix) + encode_str(name)
    out += encode_str(value)
    if mode == "incremental":
        ctx.add(name, value)
    return out


def encode_size_update(ctx, new_max):
    out = encode_int(new_max, 5, SIZE_UPDATE)
    ctx.set_max_size(new_max)
    return out


# ---------------------------------------------------------------- 解码
def decode(ctx, block):
    """解码一个头部块，返回 [(name, value), ...]"""
    out = []
    pos = 0
    while pos < len(block):
        b = block[pos]
        if b & INDEXED:
            idx, pos = decode_int(block, pos, 7)
            if idx == 0:
                raise HPackError("indexed 表示中索引 0 必须判为解码错误")
            out.append(ctx.lookup(idx))
        elif b & INCREMENTAL:
            idx, pos = decode_int(block, pos, 6)
            name = ctx.lookup(idx)[0] if idx else None
            if name is None:
                raw, _, pos = decode_str(block, pos)
                name = raw.decode()
            raw, _, pos = decode_str(block, pos)
            ctx.add(name, raw.decode())
            out.append((name, raw.decode()))
        elif b & SIZE_UPDATE:
            new_max, pos = decode_int(block, pos, 5)
            ctx.set_max_size(new_max)
        else:                                    # 0000 without / 0001 never
            idx, pos = decode_int(block, pos, 4)
            name = ctx.lookup(idx)[0] if idx else None
            if name is None:
                raw, _, pos = decode_str(block, pos)
                name = raw.decode()
            raw, _, pos = decode_str(block, pos)
            out.append((name, raw.decode()))     # 两种都不入动态表
    return out
