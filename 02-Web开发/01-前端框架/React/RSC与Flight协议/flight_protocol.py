# -*- coding: utf-8 -*-
"""
React Flight（RSC 线格式）最小编解码器。

权威依据：
  - React 源码 packages/react-client/src/ReactFlightClient.js（jsDelivr 实读）：
      * 行格式状态机 ROW_ID -> ROW_TAG -> ROW_LENGTH -> ROW_CHUNK_BY_LENGTH/NEWLINE
      * rowID 为十六进制：rowID = (rowID << 4) | (byte > 96 ? byte - 87 : byte - 48)
      * 带「字节长度」的 tag 集合：T A O o b U S s L l G g M m V（形如 <hex>:T<hexLen>,<payload>）
      * 其余 A-Z 以及 '#' 'r' 'x' 按换行 '\n' 切分
      * 未知 tag 一律视为数据内容，不升级为行头
      * 引用前缀（parseModelString 的 switch (value[1])）：
          $   -> REACT_ELEMENT_TYPE        $$  -> 转义后的字面 '$'
          L   -> Lazy chunk(hex)           @   -> Promise chunk(hex)
          w   -> Weak Promise              S   -> Symbol.for(name)
          h/H -> Server (Object) Reference T   -> Temporary Reference
          Q   -> Map    W -> Set    B -> Blob    K -> FormData
          Z   -> Error  i -> Iterator
          I   -> Infinity  N -> NaN  u -> undefined  - -> $-0 / $-Infinity
          D   -> Date(ISO) n -> BigInt
  - React 官方文档 Server Components（渲染在打包之前、'use client' 边界、Suspense 流式）：
      https://react.dev/reference/rsc/server-components
"""

import json
import math
from datetime import datetime, timezone

LENGTH_TAGS = set("TAOobUSsLlGgMmV")            # 这些 tag 后面跟 <hex 字节长度>,
NEWLINE_TAGS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ") | {"#", "r", "x"}

ROW_ID, ROW_TAG, ROW_LENGTH, BY_NEWLINE, BY_LENGTH = range(5)


class Undefined:
    def __repr__(self):
        return "undefined"


UNDEFINED = Undefined()


class ElementType:
    """源码里 `case value === '$'` 返回的 REACT_ELEMENT_TYPE 哨兵。"""

    def __repr__(self):
        return "REACT_ELEMENT_TYPE"


ELEMENT = ElementType()

_SYMBOLS = {}


class Symbol:
    """Symbol.for(name)：同名返回同一个实例。"""

    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "Symbol(%s)" % self.name


def symbol_for(name):
    if name not in _SYMBOLS:
        _SYMBOLS[name] = Symbol(name)
    return _SYMBOLS[name]


class ServerRef:
    def __init__(self, rid):
        self.id = rid

    def __repr__(self):
        return "ServerRef(%s)" % self.id


class Chunk:
    """一行 Flight 数据对应一个 chunk：pending -> fulfilled。"""

    def __init__(self, row_id):
        self.id = row_id
        self.status = "pending"
        self.value = None
        self.raw = None
        self.waiters = []

    def resolve(self, value):
        self.value = value
        self.status = "fulfilled"
        for w in self.waiters:
            w(value)
        self.waiters.clear()

    def then(self, fn):
        if self.status == "fulfilled":
            fn(self.value)
        else:
            self.waiters.append(fn)


class Lazy:
    """`$L<id>`：包成一个 lazy，内容后到也不影响先渲染 shell。"""

    def __init__(self, chunk):
        self.chunk = chunk

    @property
    def resolved(self):
        return self.chunk.status == "fulfilled"

    def value(self):
        return self.chunk.value


# ---------------- 编码器 ----------------

def encode_row(row_id, tag, payload, with_length=True):
    """<hexRowID>:<TAG>[<hexByteLen>,]<payload>"""
    if with_length:
        n = len(payload.encode("utf-8"))
        return "%x:%s%x,%s" % (row_id, tag, n, payload)
    return "%x:%s%s\n" % (row_id, tag, payload)


# ---------------- 解码器 ----------------

class FlightDecoder:
    """复刻 ReactFlightClient 的行状态机；write() 可以任意切分 chunk 喂入。"""

    def __init__(self):
        self.state = ROW_ID
        self.row_id = 0
        self.tag = 0
        self.length = 0
        self.buf = bytearray()
        self.chunks = {}
        self.rows = {}

    def get_chunk(self, row_id):
        c = self.chunks.get(row_id)
        if c is None:
            c = Chunk(row_id)
            self.chunks[row_id] = c
        return c

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        i = 0
        n = len(data)
        while i < n:
            if self.state == ROW_ID:
                b = data[i]
                i += 1
                if b == 58:                       # ':'
                    self.state = ROW_TAG
                else:
                    self.row_id = (self.row_id << 4) | (b - 87 if b > 96 else b - 48)
                continue
            if self.state == ROW_TAG:
                t = chr(data[i])
                if t in LENGTH_TAGS:
                    i += 1
                    self.tag = t
                    self.state = ROW_LENGTH
                elif t in NEWLINE_TAGS:
                    i += 1
                    self.tag = t
                    self.state = BY_NEWLINE
                else:
                    # 源码这个分支不前进 i：未知 tag 字符本身就是数据的一部分
                    self.tag = 0
                    self.state = BY_NEWLINE
                continue
            if self.state == ROW_LENGTH:
                b = data[i]
                i += 1
                if b == 44:                       # ','
                    self.state = BY_LENGTH
                else:
                    self.length = (self.length << 4) | (b - 87 if b > 96 else b - 48)
                continue
            # 收 payload：按长度或按换行
            if self.state == BY_LENGTH:
                take = min(self.length - len(self.buf), n - i)
                self.buf += data[i:i + take]
                i += take
                if len(self.buf) == self.length:
                    self._finish()
                continue
            if self.state == BY_NEWLINE:
                j = data.find(b"\n", i)
                if j == -1:
                    self.buf += data[i:]
                    i = n
                else:
                    self.buf += data[i:j]
                    i = j + 1
                    self._finish()
                continue
        return self

    def _finish(self):
        raw = bytes(self.buf).decode("utf-8")
        rid, tag = self.row_id, self.tag
        self.rows[rid] = (tag, raw)
        chunk = self.get_chunk(rid)
        chunk.raw = raw
        try:
            value = self._resolve(json.loads(raw))
        except (ValueError, TypeError):
            value = raw
        chunk.resolve(value)          # 兑现：此前所有 $L / @ 引用在此刻被回填
        self.state = ROW_ID
        self.row_id = 0
        self.tag = 0
        self.length = 0
        self.buf = bytearray()

    # ---- model 解析 ----

    def model(self, row_id):
        tag, raw = self.rows[row_id]
        return self._resolve(json.loads(raw))

    def _resolve(self, node):
        if isinstance(node, str):
            return self._resolve_ref(node)
        if isinstance(node, list):
            return [self._resolve(x) for x in node]
        if isinstance(node, dict):
            return {k: self._resolve(v) for k, v in node.items()}
        return node

    def _resolve_ref(self, value):
        if not value.startswith("$"):
            return value
        if value == "$":
            return ELEMENT
        kind = value[1]
        rest = value[2:]
        if kind == "$":
            return value[1:]                               # 源码 case '$': value.slice(1)
        if kind == "L":
            return Lazy(self.get_chunk(int(rest, 16)))
        if kind == "@":
            return self.get_chunk(int(rest, 16))
        if kind == "S":
            return symbol_for(rest)
        if kind == "h":
            return ServerRef(rest)
        if kind == "Q":
            entries = self._outlined(rest)
            return dict(entries) if entries else {}
        if kind == "W":
            items = self._outlined(rest)
            return set(items) if items else set()
        if kind == "i":
            return iter(self._outlined(rest) or [])
        if kind == "I":
            return math.inf
        if kind == "N":
            return math.nan
        if kind == "u":
            return UNDEFINED
        if kind == "n":
            return int(rest)
        if kind == "D":
            return datetime.fromisoformat(rest.replace("Z", "+00:00"))
        if kind == "-":
            return -0.0 if value == "$-0" else -math.inf
        return value                                        # 未知引用原样保留

    def _outlined(self, ref):
        row_id = int(ref, 16)
        if row_id not in self.rows:
            return None
        return self.model(row_id)
