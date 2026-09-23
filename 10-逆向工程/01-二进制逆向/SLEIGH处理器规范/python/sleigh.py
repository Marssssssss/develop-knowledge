"""Ghidra SLEIGH 处理器规范语言：token / field / table / constructor 的最小引擎。

原文实读（GhidraDocs/languages/html/）：
  * sleigh_definitions.html —— 第一条必须是 `define endian=`；`define alignment=`；
    `define space`；`define register offset= size=`；`define bitrange`
  * sleigh_tokens.html      —— `define token name (bits) field=(lo,hi) attrs`；
    最低位标 0、区间闭、大于 1 字节时受全局字节序影响、可 `endian=big|little` 覆盖；
    属性 signed/hex/dec（默认十六进制，dec 暂不支持）；字段可重复可重叠；
    `attach variables fieldlist registerlist;` 索引从 0 起
  * sleigh_constructors.html —— 构造函数五段：表头 / 显示 / 位模式 / 反汇编动作 / 语义；
    空标识符 = 根指令表；`instruction` 保留；约束用**原始整数编码**（attach 之后的
    含义在位模式里不适用）；`&` 与 `|` 与括号；单独出现的标识符即操作数（本地符号
    链接到全局 family 符号，可以是字段也可以是表）；`...` 与 `;` 处理变长
  * Ghidra/Processors/6502/data/languages/6502.slaspec —— 真实规范样例

位模式的解析与求值在 `sleigh_pattern.py`。
"""

from sleigh_pattern import parse_pattern, eval_pattern  # noqa: F401

# ---------------------------------------------------------------- 定义

class Token:
    def __init__(self, name, bits, endian=None):
        self.name = name
        self.bits = bits
        self.endian = endian
        self.fields = {}      # name -> (lo, hi)
        self.attrs = {}       # name -> set()

    def add_field(self, name, lo, hi, attrs=()):
        if hi < lo:
            raise ValueError("字段区间必须是 (lo,hi)，最低位为 0")
        if hi >= self.bits:
            raise ValueError("字段 %s 超出 token 位宽 %d" % (name, self.bits))
        self.fields[name] = (lo, hi)
        self.attrs[name] = set(attrs)

    def value(self, raw, endian):
        """按字节序把 token 的字节拼成整数（位 0 = 最低位）。"""
        order = self.endian or endian
        return int.from_bytes(raw, "big" if order == "big" else "little")

    def field_value(self, name, tokval):
        lo, hi = self.fields[name]
        v = (tokval >> lo) & ((1 << (hi - lo + 1)) - 1)
        if "signed" in self.attrs[name]:
            width = hi - lo + 1
            if v >> (width - 1) & 1:
                v -= 1 << width
        return v

    def display(self, name, tokval):
        v = self.field_value(name, tokval)
        if "dec" in self.attrs[name]:
            return str(v)
        if v < 0:
            return "-0x%x" % -v
        return "0x%x" % v


class Constructor:
    def __init__(self, table, mnemonic, operands, pattern, semantics=""):
        self.table = table            # "" 表示根表
        self.mnemonic = mnemonic
        self.operands = list(operands)
        self.pattern = pattern
        self.semantics = semantics

    def __repr__(self):
        return "<Constructor %s:%s>" % (self.table or "instruction", self.mnemonic)


class Spec:
    def __init__(self):
        self.endian = None
        self.alignment = 1
        self.spaces = {}
        self.registers = {}           # name -> (offset, size)
        self.bitranges = {}
        self.tokens = {}
        self.token_order = []
        self.constructors = []
        self.attach = {}              # field -> [register names]

    # ---- 定义语句

    def define_endian(self, e):
        if self.endian is not None:
            raise ValueError("endianness 只能定义一次，且必须是第一条")
        self.endian = e

    def define_alignment(self, n):
        self.alignment = n

    def define_space(self, name, attrs):
        self.spaces[name] = attrs

    def define_register(self, offset, size, names):
        for i, n in enumerate(names):
            self.registers[n] = (offset + i * size, size)

    def define_bitrange(self, name, reg, lo, size):
        self.bitranges[name] = (reg, lo, size)

    def define_token(self, tok):
        if tok.bits % 8 != 0:
            raise ValueError("token 位宽必须是 8 的倍数")
        self.tokens[tok.name] = tok
        self.token_order.append(tok.name)

    def attach_variables(self, fields, registers):
        """attach variables fieldlist registerlist;

        注意两侧**不要求等长**：fieldlist 里的**每一个**字段都变成同一张
        寄存器查表（文档原文：For each field in the fieldlist ... becomes a
        look-up table for the given list of registers）。
        """
        known = set()
        for t in self.tokens.values():
            known |= set(t.fields)
        for f in fields:
            if f not in known:
                raise ValueError("attach 的 %s 不是已声明的字段" % f)
            self.attach[f] = list(registers)

    # ---- 构造函数

    def add(self, c):
        self.constructors.append(c)
        return c

    def root(self):
        return [c for c in self.constructors if c.table == ""]

    def table(self, name):
        return [c for c in self.constructors if c.table == name]

# ---------------------------------------------------------------- 解码

class Insn:
    def __init__(self, mnemonic, operands, length, fields):
        self.mnemonic = mnemonic
        self.operands = operands
        self.length = length
        self.fields = fields

    def __repr__(self):
        ops = ", ".join(str(o) for o in self.operands)
        return "%s %s" % (self.mnemonic, ops) if ops else self.mnemonic


def resolve_operand(name, tokval, spec, tok):
    """把操作数标识变成显示串：attach 优先，否则是字段值。"""
    if name in spec.attach:
        regs = spec.attach[name]
        idx = tokval & 0xFFFFFFFF if not isinstance(tokval, int) else tokval
        if 0 <= idx < len(regs):
            return regs[idx]
        return "%s[%d]" % (name, idx)
    if name in tok.fields:
        return tok.display(name, tokval)
    return name


def decode(spec, data, addr=0):
    """从 data[addr:] 解一条指令；返回 Insn 或 None。"""
    if not spec.token_order:
        raise ValueError("没有定义 token")
    raw = data[addr:]
    if not raw:
        return None
    for c in spec.root():
        r = _try(spec, c, raw, 0, {})
        if r is not None:
            return r[0]
    return None


def _find_ellipsis(node):
    if node[0] == "ellipsis":
        return True
    if node[0] in ("and", "or"):
        return _find_ellipsis(node[1]) or _find_ellipsis(node[2])
    return False


def _try(spec, ctor, raw, off, fields):
    """尝试用一个构造函数匹配；递归处理子表与 `...`。"""
    if not spec.token_order:
        return None
    tok = spec.tokens[spec.token_order[0]]
    nbytes = tok.bits // 8
    if off + nbytes > len(raw):
        return None
    tokval = tok.value(raw[off:off + nbytes], spec.endian)
    local = dict(fields)
    for fn in tok.fields:
        local.setdefault(fn, tok.field_value(fn, tokval))
    used = nbytes
    pool = [(tok, tokval)]

    # 出现 `...` 就再取下一个 token（变长指令）
    if _find_ellipsis(ctor.pattern) and len(spec.token_order) >= 2:
        t2 = spec.tokens[spec.token_order[1]]
        n2 = t2.bits // 8
        if off + used + n2 > len(raw):
            return None
        v2 = t2.value(raw[off + used:off + used + n2], spec.endian)
        for fn in t2.fields:
            local[fn] = t2.field_value(fn, v2)
        used += n2
        pool.append((t2, v2))

    operands = []
    try:
        if not eval_pattern(ctor.pattern, local, spec, operands):
            return None
    except KeyError:
        return None

    resolved = []
    for op in ctor.operands:
        hit = None
        for t, v in pool:
            if op in t.fields:
                hit = (t, t.field_value(op, v))
                break
        if hit is not None:
            resolved.append(resolve_operand(op, hit[1], spec, hit[0]))
        elif _is_table(spec, op):
            sub = None
            for sc in spec.table(op):
                r = _try_token(spec, sc, raw, off + used, local)
                if r is not None:
                    sub, extra = r
                    used += extra
                    break
            if sub is None:
                return None
            resolved.append(" ".join(str(x) for x in sub))
        else:
            resolved.append(op)
    return Insn(ctor.mnemonic, resolved, used, dict(local)), used


def _is_table(spec, name):
    return any(c.table == name for c in spec.constructors)


def _try_token(spec, ctor, raw, off, fields):
    """子表：用第二个 token 匹配。"""
    if len(spec.token_order) < 2:
        return None
    tok = spec.tokens[spec.token_order[1]]
    nbytes = tok.bits // 8
    if off + nbytes > len(raw):
        return None
    tokval = tok.value(raw[off:off + nbytes], spec.endian)
    local = dict(fields)
    for fn in tok.fields:
        local[fn] = tok.field_value(fn, tokval)
    operands = []
    try:
        if not eval_pattern(ctor.pattern, local, spec, operands):
            return None
    except KeyError:
        return None
    parts = [ctor.mnemonic]
    for o in ctor.operands:
        if o in tok.fields:
            parts.append(str(resolve_operand(o, tokval, spec, tok)))
        else:
            parts.append(o)
    return parts, nbytes
