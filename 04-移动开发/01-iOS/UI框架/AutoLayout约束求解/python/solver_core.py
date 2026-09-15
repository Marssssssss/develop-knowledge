"""Cassowary 型线性约束求解内核:表格式两阶段单纯形 + 误差变量。

被 cassowary_check.py(场景自检)引用。拆分只为守住「单源文件 ≤300 行」硬约束,
被搬运的代码逐字节未改;权威依据与实际读过的来源见同目录 README 的「参考资料」。
"""

from __future__ import annotations

TOL = 1e-9
# ---------------------------------------------------------------------------
# 1. 强度:Apple 的 1..1000 优先级 → Cassowary 的 required / 非 required
# ---------------------------------------------------------------------------
REQUIRED = 1000 * 10 ** 6 + 1000 * 10 ** 3 + 1000  # 1_001_001_000


def strength(a: float, b: float = 0.0, c: float = 0.0) -> float:
    """Cassowary 的符号强度压平成一个可比较的数(钳在 required 以下)。"""
    return min(REQUIRED - 1000, a * 10 ** 6 + b * 10 ** 3 + c)


STRONG, MEDIUM, WEAK = strength(1), strength(0, 1), strength(0, 0, 1)


def apple(priority: int) -> float:
    """Apple 的 UILayoutPriority(1..1000) → 本模型强度;1000 即 required。

    两个刻度只要求**序关系一致**:250 < 500 < 750 < 1000。相邻档的间隔要远大于
    单条约束的偏差量级,否则"750 档偏 1 点"会被"250 档偏 1e5 点"盖过。
    """
    return REQUIRED if priority >= 1000 else strength(priority)


# ---------------------------------------------------------------------------
# 2. 表达式与约束
# ---------------------------------------------------------------------------
class Var:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


def E(*terms: tuple[Var, float], const: float = 0.0) -> dict:
    """线性表达式 {Var: coeff};常量存在 None 键上。"""
    out: dict = {None: const}
    for v, k in terms:
        out[v] = out.get(v, 0.0) + k
    return out


def invert(e: dict, pivot: Var) -> dict:
    """方程反转:解出 pivot,乘数取倒数、常量取反(Apple 文档 Listing 3-2)。

    `height = 2.0 × width`(= `height - 2·width = 0`)反转成 `width = 0.5 × height`。
    推导:由 k·x_p + Σ_{j≠p} c_j x_j + c0 = 0 得 `x_p + Σ (c_j/k) x_j + c0/k = 0`。
    """
    k = e[pivot]
    out = {v: c / k for v, c in e.items() if v is not None and v is not pivot}
    out[pivot] = 1.0
    out[None] = e.get(None, 0.0) / k
    return out


class Constraint:
    """一条约束:`lhs op 0`,带强度。op ∈ {==, >=, <=}。"""

    __slots__ = ("lhs", "op", "strength", "name")

    def __init__(self, lhs: dict, op: str, st: float = REQUIRED, name: str = "") -> None:
        self.lhs, self.op, self.strength, self.name = lhs, op, st, name

    def __repr__(self) -> str:
        return f"{self.name or 'anon'} {self.op} @{self.strength:g}"


# ---------------------------------------------------------------------------
# 3. 表格式两阶段单纯形:min cᵀz s.t. Az = b, z ≥ 0
# ---------------------------------------------------------------------------
class Simplex:
    """表 T[0] = 第一阶段目标行,T[1] = 第二阶段目标行,T[2+i] = 第 i 条约束。

    右端项 b 单独存(不放进行尾),否则新增列会把它挤到不是最后一格。
    """

    def __init__(self) -> None:
        self.cost: list[float] = []
        self.rows: list[list[float]] = []
        self.b: list[float] = []
        self.basis: list[int] = []
        self.pivots = 0

    def col(self, cost: float = 0.0) -> int:
        for r in self.rows:
            r.append(0.0)
        self.cost.append(cost)
        return len(self.cost) - 1

    def row(self, coeffs: dict[int, float], rhs: float) -> None:
        r = [0.0] * len(self.cost)
        for c, v in coeffs.items():
            r[c] = v
        self.rows.append(r)
        self.b.append(rhs)
        self.basis.append(-1)

    def _pivot(self, r: int, c: int, objectives: list[list[float]]) -> None:
        self.pivots += 1
        piv = self.rows[r][c]
        self.rows[r] = [x / piv for x in self.rows[r]]
        self.b[r] /= piv
        for i in range(len(self.rows)):
            if i != r and abs(self.rows[i][c]) > 1e-12:
                f = self.rows[i][c]
                self.rows[i] = [x - f * y for x, y in zip(self.rows[i], self.rows[r])]
                self.b[i] -= f * self.b[r]
        for obj in objectives:
            if abs(obj[c]) > 1e-12:
                f = obj[c]
                obj[:] = [x - f * y for x, y in zip(obj, self.rows[r])]
        self.basis[r] = c

    def _run(self, objectives: list[list[float]], allowed: set[int]) -> str:
        while True:
            # 目标行存的是 reduced cost r_j = c_j - c_B·B⁻¹A_j;
            # 最小化时 r_j < 0 才意味着"让 x_j 进基能降成本"(Bland 规则取最小下标)。
            enter = next((j for j in sorted(allowed) if objectives[0][j] < -1e-9), -1)
            if enter < 0:
                return "optimal"
            leave, best = -1, None
            for i in range(len(self.rows)):
                a = self.rows[i][enter]
                if a > 1e-9:
                    ratio = self.b[i] / a
                    if best is None or ratio < best - 1e-12:
                        leave, best = i, ratio
            if leave < 0:
                return "unbounded"
            self._pivot(leave, enter, objectives)

    def solve(self) -> tuple[str, list[float], float]:
        m = len(self.rows)
        for i in range(m):                              # b ≥ 0
            if self.b[i] < 0:
                self.rows[i] = [-x for x in self.rows[i]]
                self.b[i] = -self.b[i]
        arts: list[int] = []
        for i in range(m):                              # 每个约束行一个人工变量
            c = self.col(0.0)
            self.rows[i][c] = 1.0
            self.basis[i] = c                           # 人工变量先当基
            arts.append(c)
        p1 = [0.0] * len(self.cost)
        for c in arts:
            p1[c] = 1.0
        p2 = list(self.cost)
        objectives = [p1, p2]
        for i in range(m):                              # 用基把目标行消成规范形
            for obj in objectives:
                if abs(obj[self.basis[i]]) > 1e-12:
                    f = obj[self.basis[i]]
                    obj[:] = [x - f * y for x, y in zip(obj, self.rows[i])]
        self._run(objectives, set(range(len(self.cost))))
        if sum(self.b[i] for i in range(m) if self.basis[i] in arts) > 1e-7:
            return "infeasible", [], 0.0
        allowed = {j for j in range(len(self.cost)) if j not in arts}
        for i in range(m):                              # 清除基里残留的人工变量
            if self.basis[i] in arts:
                sub = [j for j in sorted(allowed) if abs(self.rows[i][j]) > 1e-9]
                if sub:
                    self._pivot(i, sub[0], [objectives[0]])
        st = self._run([objectives[1]], allowed)
        z = [0.0] * len(self.cost)
        for i in range(m):
            z[self.basis[i]] = self.b[i]
        return st, z, sum(self.cost[j] * z[j] for j in range(len(self.cost)))


# ---------------------------------------------------------------------------
# 4. 建模层:约束集合 → LP
# ---------------------------------------------------------------------------
class Infeasible(Exception):
    """required 约束集本身矛盾(如 x == 10 与 x == 20 同时 required)。"""


class Solver:
    def __init__(self) -> None:
        self.vars: list[Var] = []
        self.cons: list[Constraint] = []
        self.pivots = 0

    def var(self, name: str) -> Var:
        v = Var(name)
        self.vars.append(v)
        return v

    def add(self, lhs: dict, op: str, st: float = REQUIRED, name: str = "") -> Constraint:
        c = Constraint(lhs, op, st, name)
        self.cons.append(c)
        return c

    def stay(self, v: Var, at: float, st: float = WEAK) -> Constraint:
        """stay 约束:weak 地把变量按在原值 —— Cassowary 用它表达"别乱动"。"""
        return self.add(E((v, 1.0), const=-at), "==", st, f"stay({v})")

    def solve(self) -> dict[Var, float]:
        sp = Simplex()
        up = {v: sp.col() for v in self.vars}          # x⁺
        un = {v: sp.col() for v in self.vars}          # x⁻,x = x⁺ - x⁻
        for c in self.cons:
            row: dict[int, float] = {}
            const = c.lhs.get(None, 0.0)
            for v, k in c.lhs.items():
                if v is not None:
                    row[up[v]] = row.get(up[v], 0.0) + k
                    row[un[v]] = row.get(un[v], 0.0) - k
            if c.strength >= REQUIRED:
                if c.op == ">=":
                    row[sp.col()] = -1.0               # 剩余变量 surplus
                elif c.op == "<=":
                    row[sp.col()] = 1.0                # 松弛变量 slack
                sp.row(row, -const)
                continue
            em, ep = sp.col(c.strength), sp.col(c.strength)
            row[em] = row.get(em, 0.0) + 1.0           # 误差变量 e⁻/e⁺
            row[ep] = row.get(ep, 0.0) - 1.0
            sp.row(row, -const)
        st, z, _ = sp.solve()
        self.pivots = sp.pivots
        if st == "infeasible":
            raise Infeasible("required 约束集不可满足")
        return {v: z[up[v]] - z[un[v]] for v in self.vars}


def residual(c: Constraint, values: dict[Var, float]) -> float:
    """约束的实际偏差(≥0;=0 表示这条约束被完全满足)。"""
    tot = c.lhs.get(None, 0.0)
    for v, k in c.lhs.items():
        if v is not None:
            tot += k * values[v]
    if c.op == "==":
        return abs(tot)
    if c.op == ">=":
        return max(0.0, -tot)
    return max(0.0, tot)

