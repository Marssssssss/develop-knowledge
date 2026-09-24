"""Swift 参数包(变长泛型)的模型。

转写自 apple/swift-evolution@main 的四份提案:
  proposals/0393-parameter-packs.md              —— 包、捕获、同形状要求、求值语义
  proposals/0398-variadic-types.md               —— 变长泛型类型、实存属性、要求推断
  proposals/0399-tuple-of-value-pack-expansion.md —— 抽象元组与重复模式
  proposals/0408-pack-iteration.md               —— for-in 遍历包
"""


class PackError(Exception):
    pass


# ---------------------------------------------------------------- 表达式与捕获
class Each:
    """each x :对值包或类型包的一次引用。"""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "each %s" % self.name


class Repeat:
    """repeat <pattern> :包扩展表达式 / 类型。"""

    def __init__(self, *parts):
        self.parts = list(parts)

    def __repr__(self):
        return "repeat(%s)" % ", ".join(repr(p) for p in self.parts)


class Node:
    """其它节点(函数调用、成员访问…)。"""

    def __init__(self, label, *parts):
        self.label = label
        self.parts = list(parts)

    def __repr__(self):
        return "%s(%s)" % (self.label, ", ".join(repr(p) for p in self.parts))


def children(n):
    if isinstance(n, Repeat):
        return n.parts
    if isinstance(n, Node):
        return n.parts
    return []


def captures(pattern):
    """包扩展表达式捕获了哪些包?

    规则:模式里以 `each p` 出现的包被捕获;但**内层** repeat 自己形成一个包扩展,
    它捕获的包不算外层的。
    """
    out = set()
    # 传入的是 repeat 节点本身,先展开它的模式;之后遇到的 repeat 才是"内层"
    stack = list(pattern.parts) if isinstance(pattern, Repeat) else [pattern]
    while stack:
        n = stack.pop()
        if isinstance(n, Each):
            out.add(n.name)
        elif isinstance(n, Repeat):
            continue          # 不进入内层包扩展
        else:
            stack.extend(children(n))
    return out


def captured_type_packs(type_node):
    """被捕获的值包的类型里,哪些类型包也被顺带捕获(同样不进入 repeat)。"""
    return captures(type_node)


# ---------------------------------------------------------------- 形状求解
class ShapeSolver:
    """同形状要求的等价类。本提案只开放"抽象形状":每个包自带一个抽象形状,
    同形状要求把抽象形状合并成等价类;给包强加具体形状会被诊断成 conflict。
    """

    def __init__(self, packs):
        self.parent = {p: p for p in packs}
        self.concrete = {}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        self.parent[rb] = ra
        ca, cb = self.concrete.get(ra), self.concrete.get(rb)
        if ca is not None and cb is not None and ca != cb:
            raise PackError("conflict")
        if ca is None and cb is not None:
            self.concrete[ra] = cb

    def same_shape(self, a, b):
        return self.find(a) == self.find(b)

    def class_of(self, p):
        root = self.find(p)
        return sorted(q for q in self.parent if self.find(q) == root)

    def impose_concrete(self, pack, length):
        """给包强加具体形状(例如 where (repeat each S) == (Int, String))。"""
        root = self.find(pack)
        if root in self.concrete and self.concrete[root] != length:
            raise PackError("conflict")
        self.concrete[root] = length

    def impose_abstract(self, pack):
        """只允许抽象形状:一旦该类已被钉成具体长度,再加抽象约束就是 conflict。"""
        if self.find(pack) in self.concrete:
            raise PackError("conflict: concrete shape imposed on pack")


def infer_from_same_type_pack(solver, lhs_packs, rhs_packs):
    """same-type-pack 要求:两侧模式捕获到的包两两合并。"""
    for a in lhs_packs:
        for b in rhs_packs:
            solver.union(a, b)


def infer_from_pack_expansion(solver, packs):
    """位于"函数参数类型/返回类型"或"尾随 where 子句"的包扩展:捕获到的包两两合并。"""
    ps = sorted(packs)
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            solver.union(a, b)


def check_pack_expansion(solver, packs, position):
    """其它位置的包扩展:必须**已经**同形状,否则报错(不做推断)。"""
    ps = sorted(packs)
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            if not solver.same_shape(a, b):
                raise PackError("pack expansion requires known same shape at %s" % position)


# ---------------------------------------------------------------- 变长泛型类型
def parse_generic_params(spec):
    """spec 例:[('scalar','T'), ('pack','U'), ('scalar','V')]。

    一个泛型类型最多只能声明一个类型参数包。
    """
    packs = [n for k, n in spec if k == "pack"]
    if len(packs) > 1:
        raise PackError("generic type declares more than one parameter pack")
    return spec


def bind_generic_args(spec, args):
    """把实参列表绑到形参上:非包形参构成固定的前缀与后缀,包吃掉中间那一段。

    args 里的 None 表示占位符 `_`(总是被理解为**一个**包元素)。
    返回 dict:形参名 -> 绑定值(标量形参给单值,包形参给 list)。
    """
    parse_generic_params(spec)
    scalars = [n for k, n in spec if k == "scalar"]
    if len(args) < len(scalars):
        raise PackError("expected at least %d generic arguments" % len(scalars))
    pack_names = [n for k, n in spec if k == "pack"]
    out = {}
    prefix = 0
    while prefix < len(spec) and spec[prefix][0] == "scalar":
        prefix += 1
    suffix = 0
    while suffix < len(spec) and spec[len(spec) - 1 - suffix][0] == "scalar":
        suffix += 1
    idx = 0
    for i in range(prefix):
        out[spec[i][1]] = args[idx]
        idx += 1
    tail_start = len(spec) - suffix
    pack_slice = args[idx:len(args) - suffix] if suffix else args[idx:]
    for name in pack_names:
        out[name] = list(pack_slice)
    for i in range(tail_start, len(spec)):
        out[spec[i][1]] = args[len(args) - suffix + (i - tail_start)]
    return out


def check_stored_property(type_repr):
    """实存属性的类型里可以含包扩展类型,但自身**不能**就是包扩展类型。"""
    if isinstance(type_repr, Repeat):
        raise PackError("stored property cannot have a pack expansion type")
    return True


def infer_requirements(imposing_kind, applied_args, pack_depths=None):
    """SE-0398 的三条要求推断规则。

    imposing_kind : 'scalar'     该泛型类型对每个泛型实参推断一个标量要求
                    'expansion'  该泛型类型自身带一条 `repeat each T: P` 的要求扩展
    applied_args  : 实参列表,每项 ('concrete', X) 或 ('pack', P)
    pack_depths   : 当该类型出现在包扩展内部时,记录每个包被哪一层 repeat 捕获
    """
    reqs = []
    if imposing_kind == "scalar":
        # 规则 1:标量要求施加在包元素上 -> 推断成要求扩展
        for kind, name in applied_args:
            reqs.append(("expansion", (name,)) if kind == "pack" else ("scalar", name))
        return reqs
    # imposing_kind == 'expansion'
    # 规则 3a:含多个被不同深度扩展捕获的包 -> 非法
    if pack_depths and len(pack_depths) > 1 and len(set(pack_depths.values())) > 1:
        raise PackError("multiple pack elements captured at expansions of different depth")
    # 规则 2:对每个具体实参各展开一条,包实参仍是要求扩展
    for kind, name in applied_args:
        reqs.append(("expansion", (name,)) if kind == "pack" else ("scalar", name))
    return reqs


# ---------------------------------------------------------------- 包遍历
def iterate_over_pack(values, pattern_fn, break_at=None):
    """for x in repeat <pattern>:pattern 在**每次迭代**才求值一次。

    返回按执行顺序产生的事件列表。与 `repeat <pattern>` 的差别是后者会一次性
    把 n 份模式全部求值完。
    """
    events = []
    i = 0
    while i < len(values):
        events.append(("eval", pattern_fn(i)))
        events.append(("iter", i))
        if break_at is not None and i == break_at:
            break
        i += 1
    return events


def expand_all(values, pattern_fn):
    """repeat <pattern>:一次性把 n 份模式全部求值。"""
    return [("eval", pattern_fn(i)) for i in range(len(values))]


def abstract_tuple_example(value_pack, tuple_value):
    """SE-0399 的四组输出:区分 `tuple` 与 `each tuple`。"""
    return {
        "each value": tuple(value_pack),
        "each tuple": tuple(tuple_value),
        "value + tuple": tuple((v, tuple(tuple_value)) for v in value_pack),
        "value + each tuple": tuple((v, tuple_value[i]) for i, v in enumerate(value_pack)),
    }
