#!/usr/bin/env python3
"""所有权与借用检查的模型化实现（Rust demo 1/5 · Python 侧）

Rust 的借用检查是编译期分析，非法片段不能编译。本脚本用 Python 复刻同一套判定，
把「借用的有效期」画成时间轴，直观看到 NLL（non-lexical lifetimes）到底改变了什么：

  - 词法作用域模型：引用的有效期 = 它所在的花括号块
  - NLL 模型：引用的有效期 = 从创建处到**它的最后一次使用**

同一个片段在两种模型下判定不同，就是 NLL 的全部价值。

依据：The Rust Programming Language ch04-01 / ch04-02（见 README 参考资料）。
运行：python3 borrow_checker.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple


# --------------------------------------------------------------------------
# 抽象语句：只保留与所有权/借用判定相关的动作
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Declare:
    """let v = <owned>; 或重赋值 —— 该变量此刻持有所有权"""

    var: str


@dataclass(frozen=True)
class Borrow:
    """let r = &v; / let r = &mut v;  kind ∈ {SHARED, MUT}"""

    borrower: str
    target: str
    kind: str


@dataclass(frozen=True)
class Use:
    """读 v 或 r"""

    var: str


@dataclass(frozen=True)
class MoveOut:
    """把 v 的所有权移走（赋给别的变量 / 传参 / 返回）"""

    var: str


@dataclass(frozen=True)
class Mutate:
    """通过引用改数据（r.push_str(..) / *r = ..）"""

    ref: str


@dataclass(frozen=True)
class ReturnLocalRef:
    """返回指向函数内局部变量的引用"""

    var: str


SHARED, MUT = "&", "&mut"


@dataclass
class Finding:
    code: str
    at: int
    what: str


@dataclass
class Loan:
    borrower: str
    target: str
    kind: str
    start: int
    lexical_end: int  # 词法模型下的区间终点（块的结束）
    nll_end: Optional[int] = None  # NLL 模型下的区间终点（最后一次使用）


# --------------------------------------------------------------------------
# 检查器
# --------------------------------------------------------------------------
class BorrowChecker:
    """实现 5 类 rustc 报错的判定；lexical=True 时退化为 NLL 之前的模型"""

    def __init__(self, steps: Sequence[object], lexical: bool = False) -> None:
        self.steps: List[object] = list(steps)
        self.lexical = lexical

    # -- 工具 ---------------------------------------------------------------
    def _last_use(self, name: str) -> Optional[int]:
        """NLL：引用 r 的最后一次使用 —— Use(r) 或借 r 去改数据。"""
        for i in range(len(self.steps) - 1, -1, -1):
            s = self.steps[i]
            if isinstance(s, Use) and s.var == name:
                return i
            if isinstance(s, Mutate) and s.ref == name:
                return i
        return None

    def loans(self) -> List[Loan]:
        out: List[Loan] = []
        n = len(self.steps)
        for i, s in enumerate(self.steps):
            if isinstance(s, Borrow):
                out.append(
                    Loan(
                        borrower=s.borrower,
                        target=s.target,
                        kind=s.kind,
                        start=i,
                        lexical_end=n - 1,  # 词法模型：块结束（本 demo 里即程序末尾）
                    )
                )
        for loan in out:
            loan.nll_end = self._last_use(loan.borrower)
        return out

    def regions(self, loan: Loan) -> Tuple[int, int]:
        """返回该借用在当前模型下『活跃』的闭区间 [lo, hi]；未使用则返回空区间。"""
        if self.lexical:
            return loan.start, loan.lexical_end
        if loan.nll_end is None:
            return loan.start, loan.start - 1  # 空区间：创建后从未使用
        return loan.start, loan.nll_end

    # -- 主判定 -------------------------------------------------------------
    def check(self) -> List[Finding]:
        f: List[Finding] = []

        def add(code: str, at: int, what: str) -> None:
            if not any(x.code == code and x.at == at for x in f):
                f.append(Finding(code, at, what))

        # 规则 1：E0382 —— move 之后再使用
        moved: List[Tuple[str, int]] = []
        for i, s in enumerate(self.steps):
            if isinstance(s, Declare):
                moved = [(n, j) for (n, j) in moved if n != s.var]
            elif isinstance(s, MoveOut):
                moved.append((s.var, i))
            elif isinstance(s, Use):
                hit = next(((n, j) for (n, j) in moved if n == s.var), None)
                if hit:
                    add(
                        "E0382",
                        i,
                        f"`{s.var}` 在第 {hit[1]} 步被 move 走后仍被使用（borrow of moved value）",
                    )

        # 规则 2：E0499 / E0502 —— 借用区间重叠
        loans = self.loans()
        for ai, a in enumerate(loans):
            a_lo, a_hi = self.regions(a)
            for b in loans[ai + 1 :]:
                if a.target != b.target:
                    continue
                b_lo, _ = self.regions(b)
                if a_lo <= b_lo <= a_hi:  # b 创建时 a 仍活跃
                    if a.kind == MUT and b.kind == MUT:
                        add(
                            "E0499",
                            b_lo,
                            f"`{a.target}` 在第 {a.start} 步已是 &mut 借用，此处又创建第二个可变借用"
                            "（cannot borrow as mutable more than once at a time）",
                        )
                    elif a.kind != b.kind:
                        add(
                            "E0502",
                            b_lo,
                            f"`{a.target}` 的 {a.kind} 借用（第 {a.start} 步，活跃到第 {a_hi} 步）"
                            f"与 {b.kind} 借用共存"
                            "（cannot borrow as mutable because it is also borrowed as immutable）",
                        )

        # 规则 3：E0596 —— 通过共享引用改数据
        for i, s in enumerate(self.steps):
            if isinstance(s, Mutate):
                loan = next((l for l in loans if l.borrower == s.ref), None)
                if loan and loan.kind == SHARED:
                    add("E0596", i, f"`{s.ref}` 是共享借用 &{loan.target}，其指向的数据不能可变借用")

        # 规则 4：E0106 —— 返回局部变量的引用
        for i, s in enumerate(self.steps):
            if isinstance(s, ReturnLocalRef):
                add("E0106", i, f"返回类型含借用值 &{s.var}，但没有可供借用的入参（missing lifetime specifier）")

        f.sort(key=lambda x: (x.at, x.code))
        return f

    # -- 可视化 -------------------------------------------------------------
    def timeline(self, title: str) -> str:
        loans = self.loans()
        lines = [f"  借用时间轴（{len(self.steps)} 个抽象语句）：{title}"]
        for l in loans:
            lo, hi = self.regions(l)
            if hi < lo:
                bar = "o"  # 空区间：只在创建点存在
            else:
                bar = " " * lo + "[" + "=" * max(0, hi - lo) + "]"
            lines.append(f"    {l.borrower:<4}{l.kind:<5}&{l.target:<3}| {bar}")
        lines.append("    " + " " * 17 + "|" + "".join(str(i % 10) for i in range(len(self.steps))))
        return "\n".join(lines)
