# -*- coding: utf-8 -*-
"""火山模型算子库: Row / Operator 基类 + 叶/一元/二元算子.

语义依据 Neo4j Cypher Manual "Execution plans"(详见同目录 pipeline.py).
"""


class Row(dict):
    """一行 = 变量绑定(变量名 -> 节点/关系 id)."""
    pass


class Operator:
    """火山模型: 算子实现 open()/next(); next 返回 None 表示流尽."""

    def open(self, ctx):
        self.ctx = ctx
        for c in self.children:
            c.open(ctx)

    def next(self):
        raise NotImplementedError

    def exec(self, ctx):
        """收集全部输出行(仅 demo 展示用; 生产实现应逐行拉取)."""
        self.open(ctx)
        out, r = [], self.next()
        while r is not None:
            out.append(r)
            r = self.next()
        return out

    def describe(self, depth=0):
        """计划树文本: 官方约定二元算子 RHS 先显示且缩进更深."""
        lines = ["  " * depth + self.name() + self._detail_suffix()]
        if len(self.children) == 2:
            lines += self.children[1].describe(depth + 1)
            lines += self.children[0].describe(depth)
        else:
            for c in self.children:
                lines += c.describe(depth + 1)
        return "\n".join(lines)

    def name(self):
        return type(self).__name__

    def _detail_suffix(self):
        d = getattr(self, "detail", lambda: "")()
        return f"  {d}" if d else ""


# ---------- 叶算子(0 子) ----------

class AllNodesScan(Operator):
    def __init__(self, var):
        self.children = []
        self.var = var
        self.detail = lambda: self.var

    def open(self, ctx):
        super().open(ctx)
        self.it = iter(sorted(ctx["graph"]["nodes"]))  # 稳定输出

    def next(self):
        for nid in self.it:
            return Row({self.var: nid})
        return None


class NodeByLabelScan(Operator):
    def __init__(self, var, label):
        self.children = []
        self.var, self.label = var, label
        self.detail = lambda: f"{self.var}:{self.label}"

    def open(self, ctx):
        super().open(ctx)
        nodes = ctx["graph"]["nodes"]
        self.it = iter(sorted(
            n for n, meta in nodes.items() if self.label in meta["labels"]))

    def next(self):
        for nid in self.it:
            return Row({self.var: nid})
        return None


# ---------- 一元算子(1 子) ----------

class Filter(Operator):
    def __init__(self, child, pred, detail="pred"):
        self.children = [child]
        self.pred, self._detail = pred, detail
        self.detail = lambda: self._detail

    def next(self):
        r = self.children[0].next()
        while r is not None and not self.pred(r):
            r = self.children[0].next()
        return r


class Expand(Operator):
    """Expand(All): 对已绑定节点沿出边扩展一跳, 绑定关系/新节点变量.

    无索引邻接: 只读该节点的邻接表, 与全图大小无关.
    """
    def __init__(self, child, from_var, rel_var, rel_type, to_var):
        self.children = [child]
        self.from_var, self.rel_var = from_var, rel_var
        self.rel_type, self.to_var = rel_type, to_var
        self.detail = lambda: (
            f"({self.from_var})-[{self.rel_var}:{self.rel_type}]->({self.to_var})")

    def open(self, ctx):
        super().open(ctx)
        self.pending = []

    def next(self):
        while not self.pending:
            r = self.children[0].next()
            if r is None:
                return None
            for rid, dst in self.ctx["graph"]["out"].get(r[self.from_var], []):
                nr = Row(r)
                nr[self.rel_var], nr[self.to_var] = rid, dst
                self.pending.append(nr)
        return self.pending.pop(0)


class Projection(Operator):
    def __init__(self, child, expr):
        self.children = [child]
        self.expr = expr

    def next(self):
        r = self.children[0].next()
        return None if r is None else self.expr(r)


class Limit(Operator):
    def __init__(self, child, n):
        self.children = [child]
        self.n = n

    def open(self, ctx):
        super().open(ctx)
        self.left = self.n

    def next(self):
        if self.left <= 0:
            return None
        self.left -= 1
        return self.children[0].next()


class Sort(Operator):
    """Eager 算子: 必须收完全部输入(物化进缓冲)才能产出第一行."""
    def __init__(self, child, key):
        self.children = [child]
        self.key = key
        self.detail = lambda: "eager(排序需全量缓冲)"

    def open(self, ctx):
        super().open(ctx)
        self.buf = self.children[0].exec(ctx)  # 全量物化: 内存代价 O(行数)
        self.buf.sort(key=self.key)
        self.i = 0

    def next(self):
        if self.i < len(self.buf):
            r, self.i = self.buf[self.i], self.i + 1
            return r
        return None


# ---------- 二元算子(2 子) ----------

class CartesianProduct(Operator):
    """Join 风格: 两侧完整输入组合; LHS 逐行 × RHS 全部行."""
    def __init__(self, left, right):
        self.children = [left, right]

    def open(self, ctx):
        super().open(ctx)
        self.right_rows = self.children[1].exec(ctx)
        self.left_it = iter(self.children[0].exec(ctx))
        self.right_it = iter(())     # 空: 触发推进到下一左行
        self.cur_left = None

    def next(self):
        while True:
            rr = next(self.right_it, None)
            if rr is not None:
                return Row({**self.cur_left, **rr})
            self.cur_left = next(self.left_it, None)
            if self.cur_left is None:
                return None
            self.right_it = iter(self.right_rows)


class Apply(Operator):
    """Apply 风格: LHS 每行驱动 RHS 重新执行一次(嵌套循环).

    right_factory(bindings) 基于左行绑定构造新的 RHS 算子实例 ——
    这正是官方"RHS 对每个 LHS 行各运行一次, 且 LHS 变量绑定对 RHS 可见".
    """
    def __init__(self, left, right_factory, right_label="RHS"):
        self.left = left
        self.right_factory = right_factory
        self.children = []           # 右子随左行动态生成
        self._right_label = right_label

    def open(self, ctx):
        self.ctx = ctx
        self.left.open(ctx)          # 只 open 左子; 右子每行重建
        self.pending = []

    def next(self):
        while not self.pending:
            lr = self.left.next()
            if lr is None:
                return None
            rhs = self.right_factory(dict(lr))
            rhs.open(self.ctx)
            while True:
                rr = rhs.next()
                if rr is None:
                    break
                self.pending.append(Row({**lr, **rr}))
        return self.pending.pop(0)

    def describe(self, depth=0):
        return "\n".join([
            "  " * depth + f"Apply  ({self._right_label}: 每左行重启)",
            self.left.describe(depth + 1),
        ])

