"""Windows x64 表驱动异常处理：.pdata / .xdata 与栈展开。

规范原文（实读）：
  * Microsoft Learn《x64 exception handling》
    https://learn.microsoft.com/en-us/cpp/build/exception-handling-x64
  * mingw-w64 `mingw-w64-headers/include/winnt.h`（UNW_FLAG_* 的位值）

结构（规范原文给出的字段与尺寸）：
  RUNTIME_FUNCTION: ULONG begin / ULONG end / ULONG unwind_info (image relative)
  UNWIND_INFO:  UBYTE:3 version | UBYTE:5 flags
                UBYTE  size of prolog
                UBYTE  count of unwind codes
                UBYTE:4 frame register | UBYTE:4 frame register offset (scaled)
                USHORT * n  unwind codes array
                随后是 exception handler 或 chained unwind info
  UNWIND_CODE:  UBYTE offset in prolog | UBYTE:4 unwind op | UBYTE:4 op info
"""

import struct

# ---------------------------------------------------------------- 常量

UNW_FLAG_NHANDLER = 0x0
UNW_FLAG_EHANDLER = 0x1
UNW_FLAG_UHANDLER = 0x2
UNW_FLAG_CHAININFO = 0x4

UWOP_PUSH_NONVOL = 0
UWOP_ALLOC_LARGE = 1
UWOP_ALLOC_SMALL = 2
UWOP_SET_FPREG = 3
UWOP_SAVE_NONVOL = 4
UWOP_SAVE_NONVOL_FAR = 5
UWOP_SAVE_XMM128 = 8
UWOP_SAVE_XMM128_FAR = 9
UWOP_PUSH_MACHFRAME = 10

# 操作码 -> 占用槽数；ALLOC_LARGE 视 op info 而定
NODE_COUNT = {
    UWOP_PUSH_NONVOL: 1,
    UWOP_ALLOC_SMALL: 1,
    UWOP_SET_FPREG: 1,
    UWOP_SAVE_NONVOL: 2,
    UWOP_SAVE_NONVOL_FAR: 3,
    UWOP_SAVE_XMM128: 2,
    UWOP_SAVE_XMM128_FAR: 3,
    UWOP_PUSH_MACHFRAME: 1,
}

# 操作信息字段里的整数寄存器编号（规范表格）
REG_NAMES = [
    "RAX", "RCX", "RDX", "RBX", "RSP", "RBP", "RSI", "RDI",
    "R8", "R9", "R10", "R11", "R12", "R13", "R14", "R15",
]


def reg_name(n):
    if 0 <= n < len(REG_NAMES):
        return REG_NAMES[n]
    return "reg%d" % n


# ---------------------------------------------------------------- 结构

class UnwindCode:
    """一个 USHORT 槽。作为数据槽时 16 位整体有效，故保留原始 word。"""

    __slots__ = ("offset", "op", "info", "word")

    def __init__(self, offset=0, op=0, info=0, word=None):
        self.offset = offset
        self.op = op
        self.info = info
        self.word = word

    @property
    def data(self):
        """作为数据槽时的 16 位值。"""
        if self.word is not None:
            return self.word
        return self.offset | (self.op << 8) | (self.info << 12)

    def __repr__(self):
        if self.word is not None:
            return "UnwindCode(data=0x%04x)" % self.word
        return "UnwindCode(off=%d, op=%d, info=%d)" % (self.offset, self.op, self.info)


class UnwindInfo:
    """UNWIND_INFO + 其后的 handler / chained info。"""

    def __init__(self, version=1, flags=0, prolog_size=0, codes=None,
                 frame_register=0, frame_offset=0,
                 handler_rva=None, handler_data=None, chained=None):
        self.version = version
        self.flags = flags
        self.prolog_size = prolog_size
        self.codes = list(codes or [])
        self.frame_register = frame_register
        self.frame_offset = frame_offset
        self.handler_rva = handler_rva
        self.handler_data = handler_data or b""
        self.chained = chained           # RUNTIME_FUNCTION dict

    @property
    def count(self):
        return len(self.codes)

    # ---- 编码

    def to_bytes(self):
        b = bytearray()
        b.append((self.version & 0x7) | ((self.flags & 0x1F) << 3))
        b.append(self.prolog_size & 0xFF)
        b.append(self.count & 0xFF)
        b.append(((self.frame_register & 0xF) << 4) | (self.frame_offset & 0xF))
        for c in self.codes:
            b += struct.pack("<H", c.data & 0xFFFF)
        # 对齐：数组总是偶数项，末项可能未用
        if self.count & 1:
            b += struct.pack("<H", 0)
        if self.flags & UNW_FLAG_CHAININFO:
            b += struct.pack("<III", self.chained["begin"], self.chained["end"], self.chained["unwind"])
        elif self.flags & (UNW_FLAG_EHANDLER | UNW_FLAG_UHANDLER):
            b += struct.pack("<I", self.handler_rva or 0)
            b += self.handler_data
        return bytes(b)

    @staticmethod
    def from_bytes(buf):
        if len(buf) < 4:
            raise ValueError("UNWIND_INFO 至少 4 字节")
        v = buf[0] & 0x7
        flags = (buf[0] >> 3) & 0x1F
        prolog = buf[1]
        count = buf[2]
        frame_reg = (buf[3] >> 4) & 0xF
        frame_off = buf[3] & 0xF
        codes = []
        for i in range(count):
            w = struct.unpack_from("<H", buf, 4 + 2 * i)[0]
            codes.append(UnwindCode(w & 0xFF, (w >> 8) & 0xF, (w >> 12) & 0xF, word=w))
        # 链信息落在补齐到偶数之后的那个槽上：(count + 1) & ~1
        tail = 4 + 2 * ((count + 1) & ~1)
        chained = None
        handler_rva = None
        data = b""
        if flags & UNW_FLAG_CHAININFO:
            begin, end, uw = struct.unpack_from("<III", buf, tail)
            chained = {"begin": begin, "end": end, "unwind": uw}
        elif flags & (UNW_FLAG_EHANDLER | UNW_FLAG_UHANDLER):
            handler_rva = struct.unpack_from("<I", buf, tail)[0]
            data = buf[tail + 4:]
        return UnwindInfo(v, flags, prolog, codes, frame_reg, frame_off,
                          handler_rva, data, chained)

    @property
    def chained_slot(self):
        """chained info 的槽位：(CountOfCodes + 1) & ~1。"""
        return (self.count + 1) & ~1

    def size(self):
        return len(self.to_bytes())


class RuntimeFunction:
    __slots__ = ("begin", "end", "unwind")

    def __init__(self, begin, end, unwind):
        self.begin = begin
        self.end = end
        self.unwind = unwind

    def to_bytes(self):
        return struct.pack("<III", self.begin, self.end, self.unwind)


# ---------------------------------------------------------------- 展开机

class Machine:
    """寄存器 + 一个字节寻址的栈（按 8 字节槽读写）。"""

    def __init__(self, rsp=0, rip=0, stack=None):
        self.regs = {n: 0 for n in REG_NAMES}
        self.regs["RSP"] = rsp
        self.rip = rip
        self.stack = dict(stack or {})

    def load(self, addr):
        return self.stack.get(addr, 0)

    def store(self, addr, val):
        self.stack[addr] = val


def frame_base(m, info):
    """SAVE_* 类操作码的偏移基准。

    规范：Frame Register 为 0 时偏移自 RSP；否则自「FP 建立时的 RSP」，
    也就是 FP 减去 16 * scaled offset。
    """
    if info.frame_register == 0:
        return m.regs["RSP"]
    return m.regs[reg_name(info.frame_register)] - 16 * info.frame_offset


def apply_code(m, info, idx):
    """按规范撤销第 idx 个 unwind code 的效果，返回消耗的槽数。"""
    c = info.codes[idx]
    op, inf = c.op, c.info
    if op == UWOP_PUSH_NONVOL:
        m.regs[reg_name(inf)] = m.load(m.regs["RSP"])
        m.regs["RSP"] += 8
        return 1
    if op == UWOP_ALLOC_LARGE:
        if inf == 0:
            m.regs["RSP"] += info.codes[idx + 1].data * 8
            return 2
        size = (info.codes[idx + 2].data << 16) | info.codes[idx + 1].data
        m.regs["RSP"] += size
        return 3
    if op == UWOP_ALLOC_SMALL:
        m.regs["RSP"] += inf * 8 + 8
        return 1
    if op == UWOP_SET_FPREG:
        m.regs["RSP"] = m.regs[reg_name(info.frame_register)] - 16 * info.frame_offset
        return 1
    if op == UWOP_SAVE_NONVOL:
        m.regs[reg_name(inf)] = m.load(frame_base(m, info) + info.codes[idx + 1].data * 8)
        return 2
    if op == UWOP_SAVE_NONVOL_FAR:
        off = (info.codes[idx + 2].data << 16) | info.codes[idx + 1].data
        m.regs[reg_name(inf)] = m.load(frame_base(m, info) + off)
        return 3
    if op == UWOP_SAVE_XMM128:
        m.regs["xmm%d" % inf] = m.load(frame_base(m, info) + info.codes[idx + 1].data * 16)
        return 2
    if op == UWOP_SAVE_XMM128_FAR:
        off = (info.codes[idx + 2].data << 16) | info.codes[idx + 1].data
        m.regs["xmm%d" % inf] = m.load(frame_base(m, info) + off)
        return 3
    if op == UWOP_PUSH_MACHFRAME:
        m.rip = m.load(m.regs["RSP"])
        m.regs["RSP"] += 40 if inf == 0 else 48
        return 1
    raise ValueError("unknown unwind op %d" % op)


def node_size(c):
    """该操作码连带数据槽一共占几个 USHORT。"""
    if c.op == UWOP_ALLOC_LARGE:
        return 3 if c.info == 1 else 2
    return NODE_COUNT.get(c.op, 1)


def nodes(info):
    """按「操作码节点」遍历（跳过数据槽），产出槽下标。"""
    i = 0
    while i < len(info.codes):
        yield i
        i += node_size(info.codes[i])


def unwind_prolog(m, info, rip_offset):
    """Case b)：rip 落在 prolog 里。只撤销 offset <= rip_offset 的节点。

    数组按 offset 降序排列，所以「向前扫到第一个 offset <= rip_offset 的节点」，
    它及其之后的节点才是已经执行、需要撤销的。返回被撤销的节点槽下标。
    """
    idxs = list(nodes(info))
    applied = []
    started = False
    for i in idxs:
        if not started and info.codes[i].offset > rip_offset:
            continue
        started = True
        apply_code(m, info, i)
        applied.append(i)
    return applied


def unwind_full(m, info):
    """Case c)：不在 prolog/epilog，整段 code 数组全部撤销。"""
    applied = []
    for i in nodes(info):
        apply_code(m, info, i)
        applied.append(i)
    return applied


def unwind_leaf(m):
    """查不到 RUNTIME_FUNCTION：当作叶函数，返回地址就在 [RSP]。"""
    m.rip = m.load(m.regs["RSP"])
    m.regs["RSP"] += 8
