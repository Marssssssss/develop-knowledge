"""Dalvik 指令编码：官方《Dalvik 指令格式》+《Dalvik 字节码》的格式 ID、位布局与 opcode 表。

格式 ID 规则（官方「格式 ID」）：三个字符 ——
  第 1 个数字 = 该格式的 16 位代码单元数；
  第 2 个数字 = 寄存器数量上限（'r' 表示编码了一系列寄存器）；
  第 3 个字母 = 半助记符，表示额外数据类型（b/c/f/h/i/l/m/n/s/t/x）。
后缀 's' = 建议的静态链接格式，'i' = 建议的内联链接格式。
参考：https://source.android.google.cn/docs/core/dalvik/instruction-formats
"""

# 类型代码字母（官方「类型代码字母的完整列表」）
TYPE_LETTER_BITS = {
    "b": 8,     # 有符号立即数（字节）
    "c": 16,    # 常量池索引（16 或 32）
    "f": 16,    # 接口常量（仅静态链接格式）
    "h": 16,    # 有符号立即数 hat（32/64 位值的高阶位）
    "i": 32,    # 有符号立即数（整型）或 32 位浮点
    "l": 64,    # 有符号立即数（长整型）或 64 位双精度
    "m": 16,    # 方法常量（仅静态链接格式）
    "n": 4,     # 有符号立即数（半字节）
    "s": 16,    # 有符号立即数（短整型）
    "t": 8,     # 分支目标（8/16/32）
    "x": 0,     # 无额外数据
}

# 格式 ID -> (代码单元数, 位布局串)。布局串取自官方「格式」表第一列
FORMAT_LAYOUT = {
    "00x": (1, "ØØ|op"),
    "10x": (1, "ØØ|op"),
    "12x": (1, "B|A|op"),
    "11n": (1, "B|A|op"),
    "11x": (1, "AA|op"),
    "10t": (1, "AA|op"),
    "20t": (2, "ØØ|op AAAA"),
    "22x": (2, "AA|op BBBB"),
    "21t": (2, "AA|op BBBB"),
    "21s": (2, "AA|op BBBB"),
    "21h": (2, "AA|op BBBB"),
    "21c": (2, "AA|op BBBB"),
    "23x": (2, "AA|op CC|BB"),
    "22b": (2, "AA|op CC|BB"),
    "22t": (2, "B|A|op CCCC"),
    "22s": (2, "B|A|op CCCC"),
    "22c": (2, "B|A|op CCCC"),
    "30t": (3, "ØØ|op AAAAlo AAAAhi"),
    "32x": (3, "ØØ|op AAAA BBBB"),
    "31i": (3, "AA|op BBBBlo BBBBhi"),
    "31t": (3, "AA|op BBBBlo BBBBhi"),
    "31c": (3, "AA|op BBBBlo BBBBhi"),
    "35c": (3, "A|G|op BBBB F|E|D|C"),
    "3rc": (3, "AA|op BBBB CCCC"),
    "45cc": (4, "A|G|op BBBB F|E|D|C HHHH"),
    "4rcc": (4, "AA|op BBBB CCCC HHHH"),
    "51l": (5, "AA|op BBBBlo BBBB BBBB BBBBhi"),
}

# opcode -> (助记符, 格式)。取自官方「Dalvik 字节码」操作码表
OPCODES = {
    0x00: ("nop", "10x"),
    0x01: ("move", "12x"),
    0x0A: ("move-result", "11x"),
    0x0E: ("return-void", "10x"),
    0x0F: ("return", "11x"),
    0x12: ("const/4", "11n"),
    0x13: ("const/16", "21s"),
    0x14: ("const", "31i"),
    0x15: ("const/high16", "21h"),
    0x1A: ("const-string", "21c"),
    0x1B: ("const-string/jumbo", "31c"),
    0x1C: ("const-class", "21c"),
    0x28: ("goto", "10t"),
    0x29: ("goto/16", "20t"),
    0x2A: ("goto/32", "30t"),
    0x32: ("if-eq", "22t"),
    0x33: ("if-ne", "22t"),
    0x34: ("if-lt", "22t"),
    0x35: ("if-ge", "22t"),
    0x36: ("if-gt", "22t"),
    0x37: ("if-le", "22t"),
    0x38: ("if-eqz", "21t"),
    0x39: ("if-nez", "21t"),
    0x3A: ("if-ltz", "21t"),
    0x3B: ("if-gez", "21t"),
    0x3C: ("if-gtz", "21t"),
    0x3D: ("if-lez", "21t"),
    0x6E: ("invoke-virtual", "35c"),
    0x6F: ("invoke-super", "35c"),
    0x70: ("invoke-direct", "35c"),
    0x71: ("invoke-static", "35c"),
    0x72: ("invoke-interface", "35c"),
    0x74: ("invoke-virtual/range", "3rc"),
    0x75: ("invoke-super/range", "3rc"),
    0x76: ("invoke-direct/range", "3rc"),
    0x77: ("invoke-static/range", "3rc"),
    0x78: ("invoke-interface/range", "3rc"),
    0xFA: ("invoke-polymorphic", "45cc"),
    0xFB: ("invoke-polymorphic/range", "4rcc"),
    0xFC: ("invoke-custom", "35c"),
    0xFD: ("invoke-custom/range", "3rc"),
    0xFE: ("const-method-handle", "21c"),
    0xFF: ("const-method-type", "21c"),
}

# 三个 payload 伪运算码的识别值（官方 packed/sparse/fill-array-data 小节）
IDENT_PACKED_SWITCH = 0x0100
IDENT_SPARSE_SWITCH = 0x0200
IDENT_FILL_ARRAY_DATA = 0x0300


def parse_format_id(fid):
    """拆格式 ID：'35c' -> (3, '5', 'c')，'3rc' -> (3, 'r', 'c')。"""
    body = fid.rstrip("si") if fid[-1] in "si" and len(fid) > 3 else fid
    return int(body[0]), body[1], body[2]


def format_units(fid):
    return parse_format_id(fid)[0]


def _s8(v):
    return v - 0x100 if v & 0x80 else v


def _s16(v):
    return v - 0x10000 if v & 0x8000 else v


def _s4(v):
    return v - 0x10 if v & 0x08 else v


def decode(units, fmt=None):
    """把 16 位代码单元序列解成 {op, mnemonic, format, ...字段}。

    fmt 省略时按 opcode 查表；显式给出时可解析官方表里没有单独 opcode 的纯格式
    （如 23x / 22b / 32x），便于按格式逐条对拍。
    """
    op = units[0] & 0xFF
    if fmt is None:
        mnemonic, fmt = OPCODES[op]
    else:
        mnemonic = next((n for n, f in OPCODES.values() if f == fmt), "?")
    n, _layout = FORMAT_LAYOUT[fmt]
    assert len(units) >= n, "%s 需要 %d 个代码单元" % (fmt, n)
    u = units
    f = {"op": op, "mnemonic": mnemonic, "format": fmt, "units": n}
    if fmt in ("10x", "00x"):
        pass
    elif fmt == "12x":
        f["A"] = (u[0] >> 8) & 0x0F
        f["B"] = (u[0] >> 12) & 0x0F
    elif fmt == "11n":
        f["A"] = (u[0] >> 8) & 0x0F
        f["B"] = _s4((u[0] >> 12) & 0x0F)
    elif fmt == "11x":
        f["A"] = (u[0] >> 8) & 0xFF
    elif fmt == "10t":
        f["A"] = _s8((u[0] >> 8) & 0xFF)
    elif fmt == "20t":
        f["A"] = _s16(u[1])
    elif fmt == "21s":
        f["A"] = (u[0] >> 8) & 0xFF
        f["B"] = _s16(u[1])          # s = 有符号立即数（短整型）
    elif fmt == "21t":
        f["A"] = (u[0] >> 8) & 0xFF
        f["B"] = _s16(u[1])          # t = 分支目标，有符号
    elif fmt in ("22x", "21h", "21c"):
        f["A"] = (u[0] >> 8) & 0xFF
        f["B"] = u[1]
    elif fmt == "23x":
        f["A"] = (u[0] >> 8) & 0xFF
        f["B"] = u[1] & 0xFF
        f["C"] = (u[1] >> 8) & 0xFF
    elif fmt == "22b":
        f["A"] = (u[0] >> 8) & 0xFF
        f["B"] = u[1] & 0xFF
        f["C"] = _s8((u[1] >> 8) & 0xFF)
    elif fmt in ("22t", "22s", "22c"):
        f["A"] = (u[0] >> 8) & 0x0F
        f["B"] = (u[0] >> 12) & 0x0F
        f["C"] = _s16(u[1]) if fmt == "22t" or fmt == "22s" else u[1]
    elif fmt == "30t":
        f["A"] = _signed32((u[2] << 16) | u[1])
    elif fmt == "32x":
        f["A"] = u[1]
        f["B"] = u[2]
    elif fmt in ("31i", "31t"):
        f["A"] = (u[0] >> 8) & 0xFF
        f["B"] = _signed32((u[2] << 16) | u[1])
    elif fmt == "31c":
        f["A"] = (u[0] >> 8) & 0xFF
        f["B"] = (u[2] << 16) | u[1]      # 常量池索引，无符号
    elif fmt == "35c":
        f["A"] = (u[0] >> 12) & 0x0F
        f["G"] = (u[0] >> 8) & 0x0F
        f["B"] = u[1]
        f["C"] = u[2] & 0x0F
        f["D"] = (u[2] >> 4) & 0x0F
        f["E"] = (u[2] >> 8) & 0x0F
        f["F"] = (u[2] >> 12) & 0x0F
    elif fmt == "3rc":
        f["A"] = (u[0] >> 8) & 0xFF
        f["B"] = u[1]
        f["C"] = u[2]
    elif fmt == "45cc":
        f["A"] = (u[0] >> 12) & 0x0F
        f["G"] = (u[0] >> 8) & 0x0F
        f["B"] = u[1]
        f["C"] = u[2] & 0x0F
        f["D"] = (u[2] >> 4) & 0x0F
        f["E"] = (u[2] >> 8) & 0x0F
        f["F"] = (u[2] >> 12) & 0x0F
        f["H"] = u[3]
    elif fmt == "4rcc":
        f["A"] = (u[0] >> 8) & 0xFF
        f["B"] = u[1]
        f["C"] = u[2]
        f["H"] = u[3]
    elif fmt == "51l":
        f["A"] = (u[0] >> 8) & 0xFF
        f["B"] = _signed64(u[1], u[2], u[3], u[4])
    return f


def _signed32(v):
    return v - 0x100000000 if v & 0x80000000 else v


def _signed64(u1, u2, u3, u4):
    v = u1 | (u2 << 16) | (u3 << 32) | (u4 << 48)
    return v - (1 << 64) if v & (1 << 63) else v
