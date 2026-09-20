"""dalvik_isa.py 的编码半边：字段 -> 16 位代码单元（与 decode 互为逆运算）。"""

from dalvik_isa import FORMAT_LAYOUT, decode

def encode(fmt, op, **kw):
    """按格式把字段装回 16 位代码单元。"""
    def g(k, default=0):
        return kw.get(k, default)
    if fmt in ("10x", "00x"):
        return [op]
    if fmt == "12x":
        return [(g("B") & 0x0F) << 12 | (g("A") & 0x0F) << 8 | op]
    if fmt == "11n":
        return [(g("B") & 0x0F) << 12 | (g("A") & 0x0F) << 8 | op]
    if fmt == "11x":
        return [(g("A") & 0xFF) << 8 | op]
    if fmt == "10t":
        return [(g("A") & 0xFF) << 8 | op]
    if fmt == "20t":
        return [op, g("A") & 0xFFFF]
    if fmt in ("22x", "21t", "21s", "21h", "21c"):
        return [(g("A") & 0xFF) << 8 | op, g("B") & 0xFFFF]
    if fmt == "23x":
        return [(g("A") & 0xFF) << 8 | op, (g("C") & 0xFF) << 8 | (g("B") & 0xFF)]
    if fmt == "22b":
        return [(g("A") & 0xFF) << 8 | op, (g("C") & 0xFF) << 8 | (g("B") & 0xFF)]
    if fmt in ("22t", "22s", "22c"):
        return [(g("B") & 0x0F) << 12 | (g("A") & 0x0F) << 8 | op, g("C") & 0xFFFF]
    if fmt == "30t":
        v = g("A") & 0xFFFFFFFF
        return [op, v & 0xFFFF, (v >> 16) & 0xFFFF]
    if fmt == "32x":
        return [op, g("A") & 0xFFFF, g("B") & 0xFFFF]
    if fmt in ("31i", "31t", "31c"):
        v = g("B") & 0xFFFFFFFF
        return [(g("A") & 0xFF) << 8 | op, v & 0xFFFF, (v >> 16) & 0xFFFF]
    if fmt == "35c":
        return [(g("A") & 0x0F) << 12 | (g("G") & 0x0F) << 8 | op, g("B") & 0xFFFF,
                (g("F") & 0x0F) << 12 | (g("E") & 0x0F) << 8 | (g("D") & 0x0F) << 4 | (g("C") & 0x0F)]
    if fmt == "3rc":
        return [(g("A") & 0xFF) << 8 | op, g("B") & 0xFFFF, g("C") & 0xFFFF]
    if fmt == "45cc":
        return [(g("A") & 0x0F) << 12 | (g("G") & 0x0F) << 8 | op, g("B") & 0xFFFF,
                (g("F") & 0x0F) << 12 | (g("E") & 0x0F) << 8 | (g("D") & 0x0F) << 4 | (g("C") & 0x0F),
                g("H") & 0xFFFF]
    if fmt == "4rcc":
        return [(g("A") & 0xFF) << 8 | op, g("B") & 0xFFFF, g("C") & 0xFFFF, g("H") & 0xFFFF]
    if fmt == "51l":
        v = g("B") & 0xFFFFFFFFFFFFFFFF
        return [(g("A") & 0xFF) << 8 | op] + [(v >> (16 * k)) & 0xFFFF for k in range(4)]
    raise ValueError("未实现的格式 " + fmt)


def arg_registers_35c(f):
    """35c 的 [A=N] 变体：A 决定实际使用几个寄存器，第五个放在 G 位。"""
    table = {0: [], 1: ["C"], 2: ["C", "D"], 3: ["C", "D", "E"],
             4: ["C", "D", "E", "F"], 5: ["C", "D", "E", "F", "G"]}
    return [f[k] for k in table[f["A"]]]


def arg_registers_3rc(f):
    """官方：NNNN = CCCC + AA - 1，即 A 计数、C 是第一个寄存器。"""
    if f["A"] == 0:
        return []
    start = f["C"]
    return list(range(start, start + f["A"]))


def packed_switch_units(size):
    """官方：packed-switch-payload 代码单元总数 = size * 2 + 4。"""
    return size * 2 + 4


def sparse_switch_units(size):
    """官方：sparse-switch-payload 代码单元总数 = size * 4 + 2。"""
    return size * 4 + 2


def fill_array_data_units(size, element_width):
    """官方：fill-array-data-payload 代码单元总数 = (size * element_width + 1) / 2 + 4（整除）。"""
    return (size * element_width + 1) // 2 + 4


def branch_offset_ok(offset):
    """官方注释：分支偏移量不得为 0（自旋循环需靠 nop 或后向 goto 构造）。"""
    return offset != 0


def disassemble(units):
    """把一段指令流转成 smali 风格文本。"""
    out = []
    i = 0
    while i < len(units):
        f = decode(units[i:])
        n = f["units"]
        regs = ""
        if f["format"] == "35c":
            regs = "{%s}, meth@%d" % (", ".join("v%d" % r for r in arg_registers_35c(f)), f["B"])
        elif f["format"] == "3rc":
            rr = arg_registers_3rc(f)
            regs = "{%s}, meth@%d" % (" .. ".join(("v%d" % rr[0], "v%d" % rr[-1]) if rr else ("", "")), f["B"])
        elif f["format"] in ("12x", "23x"):
            regs = "v%d, v%d" % (f["A"], f["B"])
        elif f["format"] in ("11x", "11n"):
            regs = "v%d, #+%d" % (f["A"], f["B"]) if f["format"] == "11n" else "v%d" % f["A"]
        elif f["format"] == "10t":
            regs = "+%d" % f["A"]
        elif f["format"] in ("21t", "20t", "30t", "22t", "31t"):
            regs = "v%d, +%d" % (f["A"], f["B"] if "B" in f else f["A"]) if f["format"] in ("21t", "31t") \
                else ("+%d" % f["A"] if f["format"] in ("20t", "30t") else "v%d, v%d, +%d" % (f["A"], f["B"], f["C"]))
        elif f["format"] in ("21c", "31c"):
            regs = "v%d, string@%d" % (f["A"], f["B"])
        elif f["format"] in ("21s", "21h", "31i", "51l"):
            regs = "v%d, #+%d" % (f["A"], f["B"])
        elif f["format"] == "10x":
            regs = ""
        out.append(("%s %s" % (f["mnemonic"], regs)).strip())
        i += n
    return "\n".join(out)
