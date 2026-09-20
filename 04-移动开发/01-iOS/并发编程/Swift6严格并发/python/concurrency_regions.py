"""Swift 6 严格并发:隔离区(SE-0414) + Sendable(SE-0302) + 全局变量(SE-0412) 的可执行模型。

模型口径(全部对应提案原文,见 README 参考资料):
* 每个非 Sendable 值属于唯一一个「隔离区」(isolation region);隔离区带一个隔离域
  (disconnected=None / actor 实例 / global actor / task),或为 invalid。
* 隔离区合并:同一函数/赋值的参与方进入同一区域(保守假设:实现里任意参数都可能
  被另一个参数引用)。
* 传递(transfer):把值跨隔离域传入函数 = 区域被并入目标域;actor 隔离区永不可传出;
  task 隔离区传给同 task 的 nonisolated async 函数不算传递。
* 弱传递:所有权仍在调用方,生命周期在调用方作用域末尾结束;nonisolated async 函数
  返回后区域重新变回 disconnected。
"""

# ---------------------------------------------------------------- 类型与 Sendable

SENDABLE_PRIMITIVES = {"Int", "String", "Double", "Bool"}


class Type:
    def __init__(self, name, kind, fields=(), sendable=None, is_actor=False,
                 public=False, frozen=False, generic_param=None, decl_file="A.swift"):
        self.name = name
        self.kind = kind                 # "struct" | "class" | "enum" | "actor"
        self.fields = list(fields)       # [(name, Type, mutable:bool)]
        self._sendable = sendable        # 显式声明的 : Sendable / @unchecked Sendable
        self.is_actor = is_actor
        self.public = public
        self.frozen = frozen
        self.generic_param = generic_param
        self.decl_file = decl_file

    def declared_sendable(self):
        return self._sendable

    def members_all_sendable(self, memo=None):
        # memo 只用于防环:把自身放进去,嵌套引用回自己时按「假设成立」处理。
        # 注意不能让根类型自己命中 memo,否则任何类型都会直接判成 Sendable。
        memo = set(memo) if memo else set()
        memo.add(self.name)
        return all(is_sendable(t, memo) for _, t, _ in self.fields)


def is_sendable(ty, memo=None):
    """SE-0302 的 Sendable 判定(模型版,只实现提案里写死的几条规则)。"""
    memo = memo or set()
    if ty.name in memo:
        return True
    memo.add(ty.name)
    if ty.is_actor or ty.kind == "actor":
        return True                                            # actor 隐式 Sendable
    if ty.name in SENDABLE_PRIMITIVES:
        return True
    if ty._sendable == "unchecked":
        return True                                            # @unchecked 一律放行
    if ty.kind == "class":
        # 只允许 final + 全部存储属性为不可变 Sendable
        final = ty.name.startswith("final_")
        return final and all((not m) and is_sendable(t, memo) for _, t, m in ty.fields)
    if ty.kind in ("struct", "enum"):
        if ty.generic_param and not is_sendable(ty.generic_param, memo):
            return False                                       # 泛型且 T 不 Sendable
        return ty.members_all_sendable(memo)
    return False


def implicit_sendable(ty):
    """隐式推导:非 public / frozen public 且成员全 Sendable;不会隐式产生条件一致。"""
    if ty.generic_param is not None:
        # 泛型:仅当实例数据保证是 Sendable(即 T: Sendable)才有隐式一致;
        # Swift 不会隐式引入「条件一致」。
        return is_sendable(ty.generic_param) and ty.members_all_sendable()
    if ty.public and not ty.frozen:
        return False                                           # public 非 frozen 无隐式一致
    return ty.members_all_sendable()


def can_declare_conformance(ty, file):
    """Sendable 一致只能写在类型定义所在的源文件;@unchecked 例外。"""
    if ty._sendable == "unchecked":
        return True
    return file == ty.decl_file


# ---------------------------------------------------------------- 隔离区环境

INVALID = "invalid"


class Env:
    def __init__(self):
        self.regions = []      # [ {"domain": str|None, "members": set} ]
        self.loc = {}          # value key -> region idx

    # -- 基本操作
    def _new(self, domain, members):
        self.regions.append({"domain": domain, "members": set(members)})
        return len(self.regions) - 1

    def fresh(self, key, domain=None):
        idx = self._new(domain, {key})
        self.loc[key] = idx
        return idx

    def region(self, key):
        return self.regions[self.loc[key]]

    def domain_of(self, key):
        return self.region(key)["domain"]

    def _unify(self, d1, d2):
        if d1 is None:
            return d2
        if d2 is None:
            return d1
        if d1 == d2:
            return d1
        return INVALID

    def merge(self, i, j):
        if i == j:
            return i
        a, b = self.regions[i], self.regions[j]
        dom = self._unify(a["domain"], b["domain"])
        members = a["members"] | b["members"]
        a["domain"], a["members"] = dom, members
        b["members"] = set()
        b["domain"] = dom
        b["alive"] = False
        for k in list(self.loc):
            if self.loc[k] == j:
                self.loc[k] = i
        return i

    def init_binding(self, new_key, src_key=None):
        """`let y = x`:新绑定进入源值的区域;无源值则开一个 disconnected 新区。"""
        if src_key is None:
            return self.fresh(new_key)
        idx = self.fresh(new_key)
        return self.merge(idx, self.loc[src_key])

    def reassign(self, key, src, captured=False):
        """`x = y`:未被闭包按引用捕获 → 旧区域被遗忘(3b);被捕获 → 新旧合并(3a)。"""
        if captured:
            return self.merge(self.loc[key], self.loc[src])
        old = self.region(key)
        old["members"].discard(key)
        if not old["members"]:
            old["domain"] = None
        idx = self._new(None, {key})
        self.loc[key] = idx
        return self.merge(idx, self.loc[src])

    def touch(self, *keys):
        """参与同一函数/赋值 → 并入同一区域。"""
        idxs = [self.loc[k] for k in keys if k in self.loc]
        if not idxs:
            return None
        base = idxs[0]
        for other in idxs[1:]:
            base = self.merge(base, other)
        return base

    def regions_snapshot(self):
        out = []
        for r in self.regions:
            m = r["members"]
            if m:
                out.append((frozenset(m), r["domain"]))
        return out

    def count(self):
        return len([r for r in self.regions if r["members"]])


def diagnose_use(env, key, at_domain):
    """在当前隔离域 at_domain 使用 key,返回 None 或错误字符串。"""
    dom = env.domain_of(key)
    if dom == INVALID:
        return "invalid-region"
    if dom is not None and dom != at_domain:
        return "use-after-transfer"
    return None


def transfer(env, key, target_domain):
    """跨隔离域传值:区域并入 target。返回 None 或错误字符串。"""
    dom = env.domain_of(key)
    if dom == INVALID:
        return "invalid-region"
    if dom is not None and dom != target_domain:
        return "cannot-transfer-isolated"
    env.region(key)["domain"] = target_domain
    return None


def call_same_domain(env, key, domain):
    """同隔离域内调用(不产生传递)。"""
    return diagnose_use(env, key, domain)


# ---------------------------------------------------------------- 全局变量(SE-0412)

def check_global(immutable, sendable, isolated_to=None, unsafe=False):
    if unsafe:
        return "ok(nonisolated(unsafe) 关闭静态检查)"
    if isolated_to:
        return "ok(global actor 隔离)"
    if immutable and sendable:
        return "ok(不可变且 Sendable)"
    if not immutable and not sendable:
        return "error: 可变全局变量既未隔离到 global actor,也不是 Sendable"
    return "error: 不满足「不可变且 Sendable」"
