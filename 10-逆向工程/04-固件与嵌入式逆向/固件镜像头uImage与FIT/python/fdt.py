"""最小 FDT（扁平设备树）构建与解析。

FIT（Flattened Image Tree）本质上就是一个 FDT blob，所以要读固件里的 .fit，
第一道工序是把 FDT 的结构块（structure block）拆成 token 流。这里只实现
规范里与 FIT 相关的部分：BEGIN_NODE / END_NODE / PROP / NOP / END 五个 token，
以及 header 的 10 个字段。

token 值来自 Devicetree Specification（FIT spec §7.3 明确以它为基准）：
    FDT_BEGIN_NODE = 0x1   FDT_END_NODE = 0x2
    FDT_PROP       = 0x3   FDT_NOP      = 0x4
    FDT_END        = 0x9
"""

import struct

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE = 0x1
FDT_END_NODE = 0x2
FDT_PROP = 0x3
FDT_NOP = 0x4
FDT_END = 0x9

HEADER_SIZE = 40
VERSION = 17
LAST_COMP_VERSION = 16


class Node:
    """一个 FDT 结点：属性名 -> bytes，以及有序的子结点。"""

    def __init__(self):
        self.props = {}
        self.children = {}

    def add(self, path, name, value):
        node = self
        for part in [p for p in path.split("/") if p]:
            node = node.children.setdefault(part, Node())
        node.props[name] = value
        return node

    def child(self, *names):
        node = self
        for n in names:
            node = node.children[n]
        return node


def _pad4(n):
    return (4 - (n & 3)) & 3


def build_fdt(root):
    """把 Node 树序列化成 FDT blob。返回 bytes。"""
    strings = bytearray()
    stroff = {}

    def s(name):
        if name not in stroff:
            stroff[name] = len(strings)
            strings.extend(name.encode("utf-8") + b"\0")
        return stroff[name]

    sb = bytearray()

    def u32(v):
        sb.extend(struct.pack(">I", v & 0xFFFFFFFF))

    def align():
        sb.extend(b"\0" * _pad4(len(sb)))

    def emit_props(node):
        for pname, pval in node.props.items():
            u32(FDT_PROP)
            u32(len(pval))
            u32(s(pname))
            sb.extend(pval)
            align()

    def walk(node):
        emit_props(node)
        for name, child in node.children.items():
            u32(FDT_BEGIN_NODE)
            sb.extend(name.encode("utf-8") + b"\0")
            align()
            walk(child)
            u32(FDT_END_NODE)

    u32(FDT_BEGIN_NODE)
    sb.extend(b"\0")
    align()
    walk(root)
    u32(FDT_END)

    off_rsv = HEADER_SIZE
    off_struct = off_rsv + 16
    off_strings = off_struct + len(sb)
    total = off_strings + len(strings)
    struct_blob = bytes(sb)

    head = struct.pack(
        ">IIIIIIIIII",
        FDT_MAGIC,
        total,
        off_struct,
        off_strings,
        off_rsv,
        VERSION,
        LAST_COMP_VERSION,
        0,
        len(strings),
        len(struct_blob),
    )
    return head + b"\0" * 16 + struct_blob + bytes(strings)


class Token:
    __slots__ = ("kind", "start", "end", "name", "len", "nameoff", "data")

    def __init__(self, kind, start, end, name=None, plen=0, nameoff=0, data=b""):
        self.kind = kind
        self.start = start
        self.end = end
        self.name = name
        self.len = plen
        self.nameoff = nameoff
        self.data = data


def parse_fdt(blob):
    """解析 FDT blob，返回 (root, tokens, strings, header)。

    tokens 里每一项都带 start/end 两个**结构块内的绝对偏移**，这是 FIT
    签名哈希按字节选区（§7.3）时唯一需要的东西。
    """
    magic, total, off_struct, off_strings, off_rsv, ver, lcv, _cpu, ssz, stsz = struct.unpack(
        ">IIIIIIIIII", blob[:HEADER_SIZE]
    )
    header = {
        "magic": magic,
        "totalsize": total,
        "off_dt_struct": off_struct,
        "off_dt_strings": off_strings,
        "off_mem_rsvmap": off_rsv,
        "version": ver,
        "last_comp_version": lcv,
        "size_dt_strings": ssz,
        "size_dt_struct": stsz,
    }
    strings = blob[off_strings:off_strings + ssz]

    def cstr(off):
        end = strings.index(b"\0", off)
        return strings[off:end].decode("utf-8")

    sb = blob[off_struct:off_struct + stsz]
    pos = 0
    tokens = []
    root = Node()
    parsed = None
    stack = []
    cur = root
    while pos < len(sb):
        start = pos
        (tok,) = struct.unpack(">I", sb[pos:pos + 4])
        pos += 4
        if tok == FDT_BEGIN_NODE:
            end = sb.index(b"\0", pos)
            name = sb[pos:end].decode("utf-8")
            pos = end + 1
            pos += _pad4(pos)
            node = Node()
            if name:
                cur.children[name] = node
            else:
                parsed = node
            stack.append(cur)
            cur = node
            tokens.append(Token(tok, start + off_struct, pos + off_struct, name=name))
        elif tok == FDT_END_NODE:
            cur = stack.pop()
            tokens.append(Token(tok, start + off_struct, pos + off_struct))
        elif tok == FDT_PROP:
            plen, noff = struct.unpack(">II", sb[pos:pos + 8])
            pos += 8
            data = sb[pos:pos + plen]
            pos += plen
            pos += _pad4(pos)
            cur.props[cstr(noff)] = bytes(data)
            tokens.append(Token(tok, start + off_struct, pos + off_struct, name=cstr(noff), plen=plen, nameoff=noff, data=data))
        elif tok == FDT_NOP:
            tokens.append(Token(tok, start + off_struct, pos + off_struct))
        elif tok == FDT_END:
            tokens.append(Token(tok, start + off_struct, pos + off_struct))
            break
        else:
            raise ValueError("unknown FDT token 0x%x at %d" % (tok, start))
    return (parsed if parsed is not None else root), tokens, strings, header
