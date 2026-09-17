# -*- coding: utf-8 -*-
"""lsm_formats.py — LSM 引擎的两个磁盘格式:WAL 日志分块格式 + SSTable 文件格式。

权威来源(google/leveldb 官方文档原文):
  - doc/log_format.md
      * "The log file contents are a sequence of 32KB blocks."
      * record := checksum:uint32(crc32c of type and data) / length:uint16 / type:uint8 / data
      * "A record never starts within the last six bytes of a block" → 零填充 trailer
      * 剩余恰好 7 字节时,写一条**零字节用户数据的 FIRST** 记录把 trailing 7 字节填满
      * FULL=1 FIRST=2 MIDDLE=3 LAST=4
  - doc/table_format.md
      * 布局 [data block*][meta block*][metaindex][index][Footer]
      * BlockHandle = {offset: varint64, size: varint64}
      * footer 定长:metaindex_handle / index_handle / 零填充到 40 字节 / magic fixed64
      * magic == 0xdb4775248b80fb57 (little-endian)
      * filter 元块按 base=2KB 分区间,块尾是 4 字节偏移数组 + "offset of beginning" + lg(base)
"""

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

BLOCK_SIZE = 32768          # 官方:32KB 块
RECORD_HEADER = 7           # uint32 校验和 + uint16 长度 + uint8 类型
FULL, FIRST, MIDDLE, LAST = 1, 2, 3, 4
TYPE_NAMES = {FULL: "FULL", FIRST: "FIRST", MIDDLE: "MIDDLE", LAST: "LAST"}

FOOTER_MAGIC = 0xDB4775248B80FB57
BLOCK_HANDLE_MAX_ENCODED = 20       # 官方 40 == 2*BlockHandle::kMaxEncodedLength
FOOTER_SIZE = 2 * BLOCK_HANDLE_MAX_ENCODED + 8
FILTER_BASE = 2048                  # 官方:"Currently, base is 2KB."


# ------------------------------------------------------------------ varint
def encode_varint(value: int) -> bytes:
    """base-128 变长整数:每字节 7 位有效位,最高位标记是否续接。"""
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def decode_varint(data: bytes, pos: int = 0) -> Tuple[int, int]:
    """返回 (值, 新位置)。超过 10 字节即视为损坏(varint64 上限)。"""
    result = 0
    shift = 0
    start = pos
    while True:
        if pos >= len(data) or pos - start >= 10:
            raise ValueError("bad varint at %d" % start)
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


# ------------------------------------------------------------------ WAL 分块
@dataclass
class Fragment:
    """一条物理记录。一条用户记录可能被切成 FIRST/MIDDLE/LAST 多段。"""

    block: int
    offset: int
    rtype: int
    data_len: int
    phys_len: int

    def label(self) -> str:
        return TYPE_NAMES[self.rtype]


class LogWriter:
    """严格按官方分块规则写。不真正落盘,只维护块号/块内偏移与碎片序列。"""

    def __init__(self, block_size: int = BLOCK_SIZE) -> None:
        self.block_size = block_size
        self.block = 0
        self.offset = 0
        self.fragments: List[Fragment] = []
        self.trailer_bytes = 0

    def left_in_block(self) -> int:
        return self.block_size - self.offset

    def append(self, data_len: int) -> List[Fragment]:
        """追加一条用户记录,返回它的物理碎片列表。"""
        if data_len <= 0:
            raise ValueError("user record must be non-empty")
        out: List[Fragment] = []
        begin = True
        remaining = data_len
        while True:
            left = self.left_in_block()
            if left < RECORD_HEADER:
                # 官方:记录绝不从块的最后 6 字节内开始 → 余下字节零填充为 trailer
                self.trailer_bytes += left
                self.block += 1
                self.offset = 0
                left = self.block_size
            avail = left - RECORD_HEADER
            if remaining <= avail:
                rtype = FULL if begin else LAST
                frag = Fragment(self.block, self.offset, rtype, remaining, RECORD_HEADER + remaining)
                self.offset += frag.phys_len
                self.fragments.append(frag)
                out.append(frag)
                return out
            # 本块塞不下 → FIRST / MIDDLE
            rtype = FIRST if begin else MIDDLE
            frag = Fragment(self.block, self.offset, rtype, avail, RECORD_HEADER + avail)
            self.offset += frag.phys_len
            self.fragments.append(frag)
            out.append(frag)
            remaining -= avail
            begin = False
            self.block += 1
            self.offset = 0

    def block_count(self) -> int:
        return self.block + 1


def read_log(writer: LogWriter) -> Iterator[bytes]:
    """从碎片序列还原用户记录;遇到不完整序列(块边界截断)即丢弃该序列。"""
    buf: Optional[bytearray] = None
    for frag in writer.fragments:
        if frag.rtype == FULL:
            buf = None
            yield b"x" * frag.data_len
        elif frag.rtype == FIRST:
            buf = bytearray(b"x" * frag.data_len)
        elif frag.rtype == MIDDLE:
            if buf is None:
                continue
            buf += b"x" * frag.data_len
        elif frag.rtype == LAST:
            if buf is None:
                continue
            buf += b"x" * frag.data_len
            yield bytes(buf)
            buf = None


# ------------------------------------------------------------------ SSTable footer
@dataclass
class BlockHandle:
    offset: int
    size: int

    def encode(self) -> bytes:
        return encode_varint(self.offset) + encode_varint(self.size)


def encode_footer(metaindex: BlockHandle, index: BlockHandle) -> bytes:
    body = metaindex.encode() + index.encode()
    if len(body) > 2 * BLOCK_HANDLE_MAX_ENCODED:
        raise ValueError("footer handles too large")
    padding = b"\x00" * (2 * BLOCK_HANDLE_MAX_ENCODED - len(body))
    return body + padding + FOOTER_MAGIC.to_bytes(8, "little")


def decode_footer(data: bytes) -> Tuple[BlockHandle, BlockHandle]:
    if len(data) != FOOTER_SIZE:
        raise ValueError("footer must be %d bytes, got %d" % (FOOTER_SIZE, len(data)))
    magic = int.from_bytes(data[-8:], "little")
    if magic != FOOTER_MAGIC:
        raise ValueError("bad magic 0x%x" % magic)
    off, pos = decode_varint(data, 0)
    size, pos = decode_varint(data, pos)
    meta = BlockHandle(off, size)
    off, pos = decode_varint(data, pos)
    size, pos = decode_varint(data, pos)
    idx = BlockHandle(off, size)
    return meta, idx


def footer_magic_hex() -> str:
    return "0x%016x" % FOOTER_MAGIC


# ------------------------------------------------------------------ filter 元块
def filter_index_for_offset(block_offset: int, base: int = FILTER_BASE) -> int:
    """官方:块起始偏移落在 [i*base, (i+1)*base-1] 的 key 归入第 i 个 filter。"""
    return block_offset // base


def filter_block_layout(n_filters: int, base: int = FILTER_BASE) -> Dict[str, int]:
    """返回 filter 块各段长度:filter 数据 / 偏移数组 / 尾部锚点 + lg(base)。"""
    return {
        "filter_bytes": n_filters,          # 每个 filter 至少 1 字节
        "offset_array": 4 * n_filters,
        "array_anchor": 4,
        "lg_base": 1,
        "trailer_positions": 4 * n_filters + 5,
        "lg_base_value": base.bit_length() - 1,
    }


def table_layout(data_block_sizes: List[int], meta_block_sizes: List[int],
                 index_block_size: int) -> Dict[str, int]:
    """按官方布局算各段偏移:[data*][meta*][metaindex][index][footer]。"""
    off = 0
    data_off = off
    off += sum(data_block_sizes)
    meta_off = off
    off += sum(meta_block_sizes)
    metaindex_off = off
    off += 32                                  # metaindex 条目:名 + BlockHandle
    index_off = off
    off += index_block_size
    footer_off = off
    off += FOOTER_SIZE
    return {
        "data_offset": data_off,
        "n_data_blocks": len(data_block_sizes),
        "meta_offset": meta_off,
        "metaindex_offset": metaindex_off,
        "index_offset": index_off,
        "footer_offset": footer_off,
        # 官方:"Footer (fixed size; starts at file_size - sizeof(Footer))"
        "footer_starts_at": off - FOOTER_SIZE,
        "file_size": off,
    }
