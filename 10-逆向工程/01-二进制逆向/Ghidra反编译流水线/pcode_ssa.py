#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P-code 的 SSA 构造(Heritage)—— 复刻 Ghidra 反编译器的核心中间表示环节。

依据(Ghidra 官方反编译器文档, 本轮实读):
  * Decompiler Analysis Engine 总览「Main Work Flow」15 阶段
    —— 02 Generate Raw P-code / 03 Generate Basic Blocks and the CFG /
       06 The Main Simplification Loop(a Generate SSA Form / b Eliminate Dead Code /
       c Propagate Local Types / d Perform Term Rewriting / e Adjust CFG /
       f Recover Control Flow Structure)/ 08 Exit SSA Form
  * 官方原文要点:SSA 用「基于支配树与支配边界的标准 phi 放置算法 + 标准重命名算法」,
    且反编译器的 SSA 是**增量式**的(栈引用在项重写之后才提升为完整变量,
    因此需要 1 次或多次额外遍历才能完全建成 SSA 树);
    INDIRECT 操作用于标注「输出由输入经某种(常常未知)间接效果派生」。

本模块只做 IR + CFG + 支配树 + 支配边界 + phi 放置 + 重命名 + SSA 校验。
死代码消除与项重写在 `pipeline_check.py`。
运行: python pipeline_check.py
"""

from collections import defaultdict


# --------------------------------------------------------------- 变量与操作

def const(v):
    """常量 varnode —— 对应 Ghidra 的 const 地址空间。"""
    return ("const", v)


def rv(name, space="register"):
    """变量 varnode:space ∈ {register, ram, unique}(对应 Ghidra 的地址空间)。"""
    return (space, name)


def is_const(x):
    return x[0] == "const"


# 有副作用的操作:死代码消除时不可删(对应 p-code 里的 STORE/CALL/RETURN/分支)
class Op:
    __slots__ = ("op", "out", "ins", "addr", "src_name")

    def __init__(self, op, out=None, ins=(), addr=None):
        self.op = op
        self.out = out
        self.ins = list(ins)
        self.addr = addr

    def text(self):
        rhs = " ".join(fmt(v) for v in self.ins)
        return "%s = %s %s" % (fmt(self.out), self.op, rhs) if self.out \
            else "%s %s" % (self.op, rhs)

    def __repr__(self):
        return self.text()


def fmt(v):
    if v is None:
        return "-"
    if v[0] == "neg":                       # 规范化产生的「取二进制补码」标记
        return "(-%s)" % fmt(v[1])
    return "0x%x" % v[1] if is_const(v) else str(v[1])


class Block:
    def __init__(self, name, ops=(), succ=()):
        self.name = name
        self.ops = list(ops)
        self.succ = list(succ)
        self.phis = []                      # MULTIEQUAL 节点,由 Heritage 放置
        self.preds = []

    def all_ops(self):
        return self.phis + self.ops


class Func:
    """一个函数的 P-code 表示(对应 Ghidra 的 Funcdata)。"""

    def __init__(self, blocks, entry):
        self.blocks = {b.name: b for b in blocks}
        self.order = [b.name for b in blocks]
        self.entry = entry
        for b in blocks:
            for s in b.succ:
                self.blocks[s].preds.append(b.name)

    # -------------------------------------------------- CFG 顺序与支配树
    def rpo(self):
        """逆后序:支配树迭代算法的前提(入口必须先于被支配者)。"""
        seen, post = set(), []

        def dfs(n):
            seen.add(n)
            for s in self.blocks[n].succ:
                if s not in seen:
                    dfs(s)
            post.append(n)

        dfs(self.entry)
        return list(reversed(post))

    def reachable(self):
        return set(self.rpo())

    def idoms(self):
        """迭代式立即支配者(Cooper-Harvey-Kennedy):不动点求解。"""
        order = self.rpo()
        idx = {n: i for i, n in enumerate(order)}
        idom = {self.entry: self.entry}
        changed = True
        while changed:
            changed = False
            for n in order[1:]:
                preds = [p for p in self.blocks[n].preds if p in idx]
                new = None
                for p in preds:
                    if p not in idom:
                        continue
                    new = p if new is None else intersect(new, p, idom, idx)
                if new is not None and idom.get(n) != new:
                    idom[n] = new
                    changed = True
        return idom

    def dom_tree(self):
        """支配树:parent → children(重命名阶段对它做 DFS)。"""
        idom = self.idoms()
        tree = defaultdict(list)
        for n, d in idom.items():
            if n != d:
                tree[d].append(n)
        for k in tree:
            tree[k].sort(key=lambda x: self.order.index(x))
        return tree

    def df(self):
        """支配边界:标准定义 DF[x] = {y | x 支配 y 的某个前驱,但 x 不严格支配 y}。"""
        idom = self.idoms()
        frontier = defaultdict(set)
        for y in self.rpo():
            preds = [p for p in self.blocks[y].preds if p in idom]
            if len(preds) < 2:
                continue
            for p in preds:
                runner = p
                while runner != idom.get(y) and runner in idom:
                    frontier[runner].add(y)
                    runner = idom[runner]
        return frontier

    def dom_chain(self, n):
        idom, chain = self.idoms(), []
        while True:
            chain.append(n)
            if idom[n] == n:
                return chain
            n = idom[n]

    # -------------------------------------------------- Heritage: phi 放置
    def defs_of(self, var):
        """收集变量的所有定义点(含已放置的 phi —— 这正是需要迭代的原因)。"""
        out = []
        for n in self.order:
            for op in self.blocks[n].all_ops():
                if op.out == var:
                    out.append(n)
        return out

    def liveness(self):
        """活跃性分析:返回 (live_in, live_out)。

        经典 phi 放置算法要求「变量在汇合点入口活跃」才插 phi(最小 SSA),
        否则会为 `cc = cmp; br` 这类同块内定义-使用的临时量白白插 phi。
        use = 块内**向上暴露**的使用(先于本块定义出现的读),def = 块内定义集合。
        """
        use, killed = {}, {}
        for n in self.order:
            u, d = set(), set()
            for op in self.blocks[n].ops:
                for v in op.ins:
                    if v is not None and not is_const(v) and v not in d:
                        u.add(v)
                if op.out is not None:
                    d.add(op.out)
            use[n], killed[n] = u, d
        live_in = {n: set(use[n]) for n in self.order}
        live_out = {n: set() for n in self.order}
        changed = True
        while changed:
            changed = False
            for n in reversed(self.rpo()):
                out = set()
                for s in self.blocks[n].succ:
                    out |= live_in[s]
                new_in = use[n] | (out - killed[n])
                if out != live_out[n] or new_in != live_in[n]:
                    live_out[n], live_in[n] = out, new_in
                    changed = True
        return live_in, live_out

    def place_phis(self, verbose=False):
        """在支配边界上放置 MULTIEQUAL,按「工作列表 + 不动点」迭代到不再新增。

        官方文档明确这是标准算法,且因为反编译器内部 SSA 是增量式的,
        「往往需要 1 次或多次额外遍历才能完全构建 SSA 树」。
        """
        variables = set()
        for n in self.order:
            for op in self.blocks[n].all_ops():
                if op.out is not None:
                    variables.add(op.out)
        frontier, live_in = self.df(), self.liveness()[0]
        rounds, inserted = 0, 0
        for var in sorted(variables, key=str):
            work = list(self.defs_of(var))
            placed = set()
            while work:
                d = work.pop()
                for y in frontier.get(d, ()):
                    if (var, y) in placed or var not in live_in[y]:
                        continue
                    placed.add((var, y))
                    b = self.blocks[y]
                    phi = Op("MULTIEQUAL", var, [None] * len(b.preds))
                    phi.src_name = var
                    b.phis.append(phi)
                    inserted += 1
                    if verbose:
                        print("    + phi %s @%s" % (fmt(var), y))
                    work.append(y)          # phi 本身也是定义 → 继续传播
            rounds += 1
        return {"phis": inserted, "rounds": rounds}

    # -------------------------------------------------- Heritage: 重命名
    def rename(self):
        """以支配树 DFS + 每变量版本栈的标准重命名,产出 SSA 形式。"""
        tree, counters, stacks = self.dom_tree(), defaultdict(int), defaultdict(list)
        self.versions = {}

        def fresh(v):
            name = v[1]
            k = (v[0], name)
            counters[k] += 1
            new = (v[0], "%s#%d" % (name, counters[k]))
            stacks[k].append(new)
            return new

        def top(v):
            if is_const(v):
                return v
            k = (v[0], v[1].split("#")[0])
            st = stacks.get(k)
            return st[-1] if st else (v[0], "%s#0" % v[1].split("#")[0])

        def walk(n):
            b = self.blocks[n]
            pushed = []
            for phi in b.phis:
                src = phi.src_name
                new = fresh(src)
                pushed.append(src)
                phi.out = new
            for op in b.ops:
                op.ins = [top(v) for v in op.ins]
                if op.out is not None:
                    src = (op.out[0], op.out[1].split("#")[0])
                    new = fresh(src)
                    pushed.append(src)
                    op.out = new
            for s in b.succ:
                sb = self.blocks[s]
                k = self.blocks[s].preds.index(n)
                for phi in sb.phis:
                    phi.ins[k] = top(phi.src_name)
            for c in tree.get(n, ()):
                walk(c)
            for src in pushed:
                stacks[(src[0], src[1])].pop()
            for phi in b.phis:
                self.versions.setdefault(phi.src_name[1], 0)

        walk(self.entry)
        return self.versions


def intersect(a, b, idom, idx):
    """沿支配树向上找共同祖先(迭代式,避免递归)。"""
    while a != b:
        while idx[a] > idx[b]:
            a = idom[a]
        while idx[b] > idx[a]:
            b = idom[b]
    return a
