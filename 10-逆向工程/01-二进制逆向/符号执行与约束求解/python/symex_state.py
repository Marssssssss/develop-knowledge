"""SimState 的插件模型与 SimulationManager 的 stash 状态机。

原文实读：
  * angr `angr/sim_state.py`：SimState 是 PluginHub，默认插件有
    regs / registers / mem / memory / solver / inspect / history / scratch /
    posix / fs / libc / heap / callstack / jni_references
  * angr `angr/sim_manager.py`：
    - ALL = "_ALL"，DROP = "_DROP"
    - _integral_stashes = ("active","stashed","pruned","unsat","errored",
                           "deadended","unconstrained")
    - explore(stash, n, find, avoid, find_stash="found", avoid_stash="avoid",
              cfg, num_find=1, avoid_priority=False)
    - num_find += len(self._stashes[find_stash]) if find_stash in self._stashes else 0
"""

from symex_bv import BVS, evaluate, solve  # noqa: F401


INTEGRAL_STASHES = ("active", "stashed", "pruned", "unsat", "errored",
                    "deadended", "unconstrained")
ALL = "_ALL"
DROP = "_DROP"


class SimSolver:
    """简化版 solver：约束 + 符号位宽表 + 暴力求解。"""

    def __init__(self):
        self.constraints = []
        self.sizes = {}

    def BVS(self, name, size):
        self.sizes[name] = size
        return BVS(name, size)

    def BVV(self, value, size=None):
        from symex_bv import BVV
        return BVV(value, size)

    def add(self, *cs):
        for c in cs:
            self.constraints.append(c)
        return self.constraints

    # 兼容 angr 的名字
    add_constraints = add

    def _symbols(self):
        s = set()
        for c in self.constraints:
            s |= c.symbols()
        return sorted(s)

    def satisfiable(self):
        return len(solve(self.constraints, self._symbols(), self.sizes, limit=1)) > 0

    is_sat = satisfiable

    def eval(self, expr, n=1):
        # 约束里出现但表达式里没有的符号也必须参与枚举，否则求值会缺变量
        syms = sorted(expr.symbols() | set(self._symbols()))
        sols = solve(self.constraints, syms, self.sizes, limit=n)
        return [evaluate(expr, s) for s in sols]

    def eval_upto(self, expr, n):
        return self.eval(expr, n)

    def min(self, expr):
        vals = self.eval(expr, 1 << 20)
        return min(vals) if vals else None

    def max(self, expr):
        vals = self.eval(expr, 1 << 20)
        return max(vals) if vals else None


class SimState:
    """插件化状态：每个插件一个属性，copy() 是浅拷贝的独立对象。"""

    PLUGINS = ("regs", "registers", "mem", "memory", "solver", "inspect",
               "history", "scratch", "posix", "fs", "libc", "heap",
               "callstack")

    def __init__(self, addr=0, solver=None):
        self.addr = addr
        self.solver = solver or SimSolver()
        self.regs = {}
        self.registers = {}
        self.memory = {}
        self.mem = self.memory
        self.inspect = []
        self.history = []
        self.scratch = {}
        self.posix = None
        self.fs = None
        self.libc = None
        self.heap = None
        self.callstack = []
        self.plugins = set(self.PLUGINS)

    def copy(self):
        s = SimState(self.addr, self.solver)
        s.regs = dict(self.regs)
        s.memory = dict(self.memory)
        s.history = list(self.history)
        return s

    def __repr__(self):
        return "<SimState 0x%x>" % self.addr


class SimulationManager:
    """stash 状态机：step 推进 active，按 filter 在 stash 之间搬移。"""

    def __init__(self, active_states=None, stashes=None, save_unsat=False,
                 auto_drop=None, step_func=None):
        self._stashes = {k: [] for k in INTEGRAL_STASHES}
        if stashes:
            for k, v in stashes.items():
                self._stashes[k] = list(v)
        for s in active_states or []:
            self._stashes["active"].append(s)
        self.save_unsat = save_unsat
        self.auto_drop = set(auto_drop or [])
        self.errored = []
        self.steps = 0
        self.step_func = step_func

    # ---- 访问

    @property
    def stashes(self):
        return self._stashes

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        stashes = self.__dict__.get("_stashes")
        if stashes is None:
            raise AttributeError(name)
        if name in stashes:                      # 任意 stash 都能当属性访问
            return stashes[name]
        if name.startswith("one_") and name[4:] in stashes:
            st = stashes[name[4:]]
            return st[0] if st else None
        raise AttributeError(name)

    def _targets(self, name):
        if name == ALL:
            return list(INTEGRAL_STASHES)
        return [name]

    def apply(self, state_func=None, stash="active", to_stash=None):
        """对某个 stash 里的状态施加函数（angr 的 apply 语义）。"""
        out = []
        for s in list(self._stashes[stash]):
            r = state_func(s) if state_func else s
            if r is not None:
                out.append(r)
        if to_stash:
            self._stashes[to_stash].extend(out)
        return out

    def move(self, from_stash, to_stash, filter_func=None):
        moved, kept = [], []
        for s in self._stashes[from_stash]:
            if filter_func is None or filter_func(s):
                moved.append(s)
            else:
                kept.append(s)
        self._stashes[from_stash] = kept
        if to_stash != DROP:
            self._stashes.setdefault(to_stash, [])
            self._stashes[to_stash].extend(moved)
        return moved

    def stash(self, filter_func=None, from_stash="active", to_stash="stashed"):
        return self.move(from_stash, to_stash, filter_func)

    def drop(self, filter_func=None, stash="active"):
        return self.move(stash, DROP, filter_func)

    def prune(self, filter_func=None, from_stash="active", to_stash="pruned"):
        return self.move(from_stash, to_stash, filter_func)

    # ---- 推进

    def step(self, step_func=None, stash="active", n=1):
        """对 active 里的每个状态调用 step_func，产出后继；无后继则 deadended。"""
        for _ in range(n):
            succ, dead = [], []
            for s in self._stashes[stash]:
                fn = step_func or self.step_func
                out = fn(s) if fn else []
                if out:
                    succ.extend(out)
                else:
                    dead.append(s)
            self._stashes[stash] = succ
            self._stashes["deadended"].extend(dead)
            # 约束不可解的状态按 save_unsat 决定去 unsat 还是丢弃
            keep = []
            for s in self._stashes[stash]:
                if s.solver.satisfiable():
                    keep.append(s)
                elif self.save_unsat:
                    self._stashes["unsat"].append(s)
            self._stashes[stash] = keep
            self.steps += 1
            if not self._stashes[stash]:
                break
        return self

    def explore(self, stash="active", n=None, find=None, avoid=None,
                find_stash="found", avoid_stash="avoid", num_find=1,
                avoid_priority=False):
        """num_find 会先把已有的 found 数量加上，这是源码里最容易被忽略的一行。"""
        num_find += len(self._stashes[find_stash]) if find_stash in self._stashes else 0
        self._stashes.setdefault(find_stash, [])
        self._stashes.setdefault(avoid_stash, [])
        steps = 0
        while True:
            if n is not None and steps >= n:
                break
            if len(self._stashes[find_stash]) >= num_find:
                break
            if not self._stashes[stash]:
                break
            self.step(stash=stash)
            steps += 1
            if avoid is not None:
                self.move(stash, avoid_stash, _as_pred(avoid))
            if find is not None:
                self.move(stash, find_stash, _as_pred(find))
        return self


def _as_pred(spec):
    if callable(spec):
        return spec
    addrs = set(spec) if isinstance(spec, (list, set, tuple)) else {spec}
    return lambda s: s.addr in addrs


# ---------------------------------------------------------------- 路径爆炸计数

def count_paths(branch_count):
    """每层二分的路径数（演示用，与被测实现无关）。"""
    return 2 ** branch_count


def branch_states(state, branches):
    """给一个状态派生 branches 个后继（用于演示路径爆炸）。"""
    out = []
    for b in range(branches):
        ns = state.copy()
        ns.addr = state.addr + b + 1
        ns.scratch["branch"] = b
        out.append(ns)
    return out


def linear_chain(limit):
    """addr -> addr+1 直到 limit：用作 step_func 的现成例子。"""
    return lambda s: [] if s.addr >= limit else [SimState(s.addr + 1)]
