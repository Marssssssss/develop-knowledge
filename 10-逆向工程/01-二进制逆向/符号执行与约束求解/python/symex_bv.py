"""符号位向量（claripy BV 语义）与朴素约束求解。

原文实读：
  * claripy `claripy/ast/bv.py`（class BV）
      - 位序：a[31] 是**最左**（最高）位，a[0] 是最右位
      - chop(bits)：返回的第一个元素是**最左**的那一组
      - get_byte(index)：**大端**字节序号，0 是最高字节
      - concat(*args)：self 在最左（最高位）
      - zero_extend / sign_extend 的示例值
      - _from_int(like, value)：整数被强制成 like.length（缺省 64）
      - _from_Bool(like, value)：True -> BVV(1, like.length)
  * angr `angr/sim_state.py`（SimState 的插件清单）
  * angr `angr/sim_manager.py`（_integral_stashes、ALL/DROP、explore 语义）

求解器用暴力枚举代替 Z3：符号位宽 <= _ENUM_BITS 时是完备的，超出则只在
候选集合里找 —— 只用于验证语义，不代表 angr 的能力。
"""

ENUM_BITS = 8


class BV:
    """位向量 AST：叶子是 BVS/BVV，其余是带 op 与 args 的节点。"""

    def __init__(self, op, args, size, name=None, value=None):
        self.op = op
        self.args = tuple(args)
        self._size = size
        self.name = name
        self.value = value

    # ---- 基本属性

    def size(self):
        return self._size

    def __len__(self):
        return self._size

    def __repr__(self):
        if self.op == "BVS":
            return "<BVS %s:%d>" % (self.name, self._size)
        if self.op == "BVV":
            return "<BVV 0x%x:%d>" % (self.value, self._size)
        return "<%s(%s)>" % (self.op, ", ".join(repr(a) for a in self.args))

    def symbols(self):
        if self.op == "BVS":
            return {self.name}
        out = set()
        for a in self.args:
            if isinstance(a, BV):
                out |= a.symbols()
        return out

    # ---- 位选择与切片：a[31] 是最左位

    def __getitem__(self, rng):
        if isinstance(rng, slice):
            left = rng.start if rng.start is not None else self._size - 1
            right = rng.stop if rng.stop is not None else 0
            if left < 0:
                left += self._size
            if right < 0:
                right += self._size
            return Extract(left, right, self)
        return Extract(int(rng), int(rng), self)

    # ---- 构造型操作

    def chop(self, bits=1):
        s = self._size
        if s % bits != 0:
            raise ValueError("expression length (%d) should be a multiple of 'bits' (%d)" % (s, bits))
        if s == bits:
            return [self]
        return list(reversed([self[(n + 1) * bits - 1:n * bits] for n in range(s // bits)]))

    def get_byte(self, index):
        return self.get_bytes(index, 1)

    def get_bytes(self, index, size):
        pos = (self._size + 7) // 8 - 1 - index
        if pos < 0:
            raise ValueError("Incorrect index %d. Your index must be between 0 and %d."
                             % (index, self._size // 8 - 1))
        if size == 0:
            return BVV(0, 0)
        r = self[min(pos * 8 + 7, self._size - 1):(pos - size + 1) * 8]
        if r.size() % 8 != 0:
            r = r.zero_extend(8 - r.size() % 8)
        return r

    def zero_extend(self, n):
        return ZeroExt(n, self)

    def sign_extend(self, n):
        return SignExt(n, self)

    def concat(self, *args):
        return Concat(self, *args)

    # ---- 算术：整数被强制成 self 的位宽（claripy _from_int）

    def _coerce(self, other):
        if isinstance(other, bool):
            return BVV(1 if other else 0, self._size)
        if isinstance(other, int):
            return BVV(other, self._size)
        return other

    def __add__(self, other):
        o = self._coerce(other)
        return BinOp("__add__", self, o, self._size)

    def __sub__(self, other):
        o = self._coerce(other)
        return BinOp("__sub__", self, o, self._size)

    def __mul__(self, other):
        o = self._coerce(other)
        return BinOp("__mul__", self, o, self._size)

    def __xor__(self, other):
        o = self._coerce(other)
        return BinOp("__xor__", self, o, self._size)

    def __and__(self, other):
        o = self._coerce(other)
        return BinOp("__and__", self, o, self._size)

    def __eq__(self, other):
        return BoolOp("__eq__", self, self._coerce(other))

    def __ne__(self, other):
        return BoolOp("__ne__", self, self._coerce(other))

    def __lt__(self, other):
        return BoolOp("__lt__", self, self._coerce(other))

    def __le__(self, other):
        return BoolOp("__le__", self, self._coerce(other))

    def __gt__(self, other):
        return BoolOp("__gt__", self, self._coerce(other))

    def __ge__(self, other):
        return BoolOp("__ge__", self, self._coerce(other))

    def __hash__(self):
        return id(self)


class Bool(BV):
    """布尔 AST，size 恒为 1（angr 里是独立的 Bool 类，这里简化）。"""

    def __init__(self, op, args):
        BV.__init__(self, op, args, 1)

    def __and__(self, other):
        return Bool("And", (self, other))

    def __or__(self, other):
        return Bool("Or", (self, other))


def BVS(name, size):
    return BV("BVS", (), size, name=name)


def BVV(value, size=None):
    if size is None:
        size = 64
    return BV("BVV", (), size, value=value & ((1 << size) - 1))


def Extract(hi, lo, bv):
    if hi < lo:
        raise ValueError("Extract 的 hi 必须 >= lo")
    return BV("Extract", (hi, lo, bv), hi - lo + 1)


def Concat(*args):
    return BV("Concat", args, sum(a.size() for a in args))


def ZeroExt(n, bv):
    return BV("ZeroExt", (n, bv), bv.size() + n)


def SignExt(n, bv):
    return BV("SignExt", (n, bv), bv.size() + n)


def BinOp(op, a, b, size):
    return BV(op, (a, b), size)


def BoolOp(op, a, b):
    return Bool(op, (a, b))


def If(cond, t, f):
    return BV("If", (cond, t, f), t.size())


# ---------------------------------------------------------------- 求值

def evaluate(node, env):
    """在给定符号赋值下求值（位宽回绕与 Python 一致）。"""
    op = node.op
    if op == "BVS":
        if node.name not in env:
            raise KeyError("unassigned symbol %s" % node.name)
        return env[node.name] & ((1 << node._size) - 1)
    if op == "BVV":
        return node.value
    if op == "Extract":
        _, lo, bv = node.args
        v = evaluate(bv, env)
        return (v >> lo) & ((1 << node._size) - 1)
    if op == "Concat":
        acc = 0
        for a in node.args:
            acc = (acc << a.size()) | evaluate(a, env)
        return acc
    if op == "ZeroExt":
        return evaluate(node.args[1], env)
    if op == "SignExt":
        n, bv = node.args
        v = evaluate(bv, env)
        if v >> (bv.size() - 1) & 1:
            return v | (((1 << n) - 1) << bv.size())
        return v
    if op == "If":
        c, t, f = node.args
        return evaluate(t, env) if evaluate(c, env) else evaluate(f, env)
    if isinstance(node, Bool):
        a = evaluate(node.args[0], env)
        b = evaluate(node.args[1], env)
        tbl = {"__eq__": a == b, "__ne__": a != b, "__lt__": a < b,
               "__le__": a <= b, "__gt__": a > b, "__ge__": a >= b,
               "And": bool(a) and bool(b), "Or": bool(a) or bool(b)}
        if op not in tbl:
            raise ValueError("unsupported bool op %s" % op)
        return 1 if tbl[op] else 0
    a = evaluate(node.args[0], env)
    b = evaluate(node.args[1], env)
    mod = 1 << node._size
    tbl = {"__add__": (a + b) % mod, "__sub__": (a - b) % mod,
           "__mul__": (a * b) % mod, "__xor__": (a ^ b) % mod,
           "__and__": (a & b) % mod}
    if op not in tbl:
        raise ValueError("unsupported op %s" % op)
    return tbl[op]


def domain_of(symbols, size_table):
    """给符号枚举候选值：位宽 <= ENUM_BITS 时全枚举，否则用 0..15 的窗口。"""
    doms = []
    for s in symbols:
        w = size_table[s]
        n = 1 << w if w <= ENUM_BITS else 16
        doms.append((s, list(range(n))))
    return doms


def solve(constraints, symbols, size_table, limit=None):
    """暴力枚举求所有满足约束的赋值（顺序固定，便于对拍）。"""
    doms = domain_of(symbols, size_table)
    out = []

    def rec(i, env):
        if i == len(doms):
            for c in constraints:
                if not evaluate(c, env):
                    return
            out.append(dict(env))
            return
        name, vals = doms[i]
        for v in vals:
            env[name] = v
            rec(i + 1, env)
            if limit is not None and len(out) >= limit:
                return
            del env[name]

    rec(0, {})
    return out
