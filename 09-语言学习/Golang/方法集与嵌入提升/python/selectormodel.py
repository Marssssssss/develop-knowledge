"""Go 方法集与选择器解析的可执行模型。

规则逐条来自官方语言规范（go.dev/ref/spec）：
  - §Method sets：类型 T 的方法集 = 接收者为 T 的方法；*T 的方法集 = 接收者为 T 或 *T 的方法
  - §Struct types：嵌入字段带来的提升（promoted methods）规则
  - §Selectors：x.f 表示 T 中**深度最浅**的 f；最浅层不唯一则选择器非法
  - §Selectors 的例外：定义型指针类型 Q = *T2，仅当 (*x).f 是**字段**（而非方法）时 x.f 才是简写
"""

BLANK = "_"


class Ambiguous(Exception):
    pass


class NotFound(Exception):
    pass


class GoType(object):
    """一个命名类型：字段、嵌入字段、方法。"""

    def __init__(self, name, fields=None, embedded=None, methods=None,
                 is_defined_pointer_of=None, is_interface=False, type_set=None):
        self.name = name
        self.fields = dict(fields or {})          # name -> type name
        self.embedded = list(embedded or [])      # [(field_name, type_name, is_pointer)]
        self.methods = dict(methods or {})        # name -> True 表示接收者是 *T
        self.is_defined_pointer_of = is_defined_pointer_of
        self.is_interface = is_interface
        self.type_set = list(type_set or [])      # 接口的类型集（用于求交集）

    def __repr__(self):
        return "<GoType %s>" % self.name


class Universe(object):
    def __init__(self):
        self.types = {}

    def add(self, t):
        self.types[t.name] = t
        return t

    def get(self, name):
        return self.types[name]

    def base(self, name):
        """剥掉定义型指针：Q = *T2 → T2。"""
        t = self.types[name]
        while t.is_defined_pointer_of:
            t = self.types[t.is_defined_pointer_of]
        return t


# ------------------------------------------------------------------ 方法集
def method_set(u, type_name, pointer=False):
    """返回 {方法名: (声明它的类型, 接收者是否为指针, 深度)}。"""
    t = u.types[type_name]
    if t.is_interface:
        return _interface_method_set(u, t)
    out = {}

    def put(name, decl, is_ptr, depth):
        if name == BLANK:
            return
        if name in out:
            out[name] = (out[name][0], out[name][1], out[name][2], True)  # 冲突标记
            return
        out[name] = (decl, is_ptr, depth, False)

    for m, is_ptr in t.methods.items():
        if pointer or not is_ptr:          # T 只收值接收者；*T 两者都收
            put(m, t.name, is_ptr, 0)
    for fname, sub, is_ptr_embed in t.embedded:
        # 嵌入 *T：S 与 *S 都获得接收者为 T 或 *T 的方法
        # 嵌入 T ：S 获得接收者为 T 的方法，*S 额外获得接收者为 *T 的方法
        src = method_set(u, sub, pointer or is_ptr_embed)
        for m, v in src.items():
            put(m, v[0], v[1], v[2] + 1)
    return out


def _interface_method_set(u, t):
    """接口的方法集 = 类型集里每个类型方法集的**交集**（规范原文）。"""
    if not t.type_set:
        return {}
    sets = [method_set(u, n, False) for n in t.type_set]
    common = set(sets[0])
    for s in sets[1:]:
        common &= set(s)
    return {m: (t.name, False, 0, False) for m in common}


# ------------------------------------------------------------------ 选择器
def _collect(u, t, f, depth, path, out, visiting):
    """按深度收集 T 中所有名为 f 的字段/方法（含嵌入展开）。"""
    if t.name in visiting:      # 嵌入成环（实际不可能，这里做防御）
        return
    visiting = visiting | {t.name}
    if f in t.methods:
        out.append((depth, list(path), "method", t.name))
    if f in t.fields:
        out.append((depth, list(path), "field", t.name))
    for fname, sub, _isptr in t.embedded:
        if fname == f:
            out.append((depth, list(path), "field", t.name))
        _collect(u, u.types[sub], f, depth + 1, path + [fname], out, visiting)


def lookup(u, x_type, f, x_is_defined_pointer=False):
    """实现规范 §Selectors：返回 (深度, 路径, 种类, 声明类型)。"""
    if f == BLANK:
        # 规范原文：The identifier f is called the selector; it must not be
        # the blank identifier.
        raise NotFound("selector must not be the blank identifier")
    t = u.base(x_type) if not x_is_defined_pointer else u.base(x_type)
    if t.is_interface:
        # 接口：x.f 表示动态值的同名方法；方法集里没有就非法
        if f in method_set(u, t.name, False):
            return (0, [], "method", t.name)
        raise NotFound("x.f undefined (type %s has no method %s)" % (t.name, f))
    out = []
    _collect(u, t, f, 0, [], out, frozenset())
    if not out:
        raise NotFound("x.f undefined (type %s has no field or method %s)" % (t.name, f))
    best = min(o[0] for o in out)
    cands = [o for o in out if o[0] == best]
    if len(cands) != 1:
        raise Ambiguous("ambiguous selector %s at depth %d (%d candidates)"
                        % (f, best, len(cands)))
    return cands[0]


def selector(u, x_type, f):
    """完整实现「定义型指针类型」的例外：只有**字段**才有简写。"""
    t = u.types[x_type]
    if t.is_defined_pointer_of:
        try:
            res = lookup(u, x_type, f)
        except (NotFound, Ambiguous) as e:
            raise e
        if res[2] != "field":
            raise NotFound(
                "%s.f undefined (type %s is pointer to type parameter/defined pointer; "
                "(*x).f is a method, not a field)" % (f, x_type))
        return res
    return lookup(u, x_type, f)
