#!/usr/bin/env python3
"""生命周期省略规则与存活约束的模型化实现（Rust demo 5/5 · Python 侧）

两部分：

  A. **elision 引擎** —— 按 The Book ch10-03 的三条规则，判断一个签名是否还需要显式标注；
     规则 1：每个引用参数各自获得一个生命周期参数
     规则 2：恰好一个输入生命周期 → 赋给所有输出
     规则 3：多个输入但其中有 &self / &mut self（仅方法）→ 输出取 self 的
     三条都不适用且有引用输出 → E0106（必须显式标注）

  B. **存活约束检查器** —— 每个绑定的「存活区间」= 声明点到其所在块的结束；
     引用在任一使用点都要求被引用对象仍存活，否则 E0597。

依据：The Rust Programming Language ch10-03、Rust Reference *Destructors*（常量提升）。
运行：python3 lifetime_solver.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

LIFETIMES = ["'a", "'b", "'c", "'d", "'e"]


# ==========================================================================
# A. elision 引擎
# ==========================================================================
@dataclass
class Param:
    name: str
    kind: str  # "ref" | "ref_mut" | "self" | "self_mut" | "owned"


@dataclass
class Signature:
    name: str
    params: List[Param]
    returns_ref: bool
    is_method: bool = False


def elide(sig: Signature) -> Tuple[Optional[str], str]:
    """返回（推断出的输出生命周期 或 None, 结论说明）"""
    if not sig.returns_ref:
        return None, "输出不含引用 → 无需任何标注"

    # 规则 1：每个引用参数（含 self）各自获得一个生命周期参数
    ref_params = [p for p in sig.params if p.kind in ("ref", "ref_mut", "self", "self_mut")]
    input_lt = {p.name: LIFETIMES[i] for i, p in enumerate(ref_params)}

    # 规则 3：多个输入且其中一个是 &self / &mut self（仅方法）→ 输出取 self 的
    self_param = next((p for p in ref_params if p.kind in ("self", "self_mut")), None)
    if len(ref_params) > 1 and self_param is not None and sig.is_method:
        return input_lt[self_param.name], f"规则 3：输出取 self 的 {input_lt[self_param.name]}"

    # 规则 2：恰好一个输入生命周期 → 赋给所有输出
    if len(ref_params) == 1:
        only = ref_params[0]
        return input_lt[only.name], f"规则 2：唯一输入 {only.name} 的生命周期 {input_lt[only.name]} 赋给输出"

    if len(ref_params) == 0:
        return None, "无引用参数却有引用输出 → 报 E0106（除非返回 'static）"

    return None, "规则 1/2/3 均不适用（自由函数 + 多个引用入参）→ E0106：必须显式标注"


SIGNATURES: Sequence[Signature] = [
    Signature("first_word", [Param("s", "ref")], True),
    Signature("longest", [Param("x", "ref"), Param("y", "ref")], True),
    Signature("describe", [Param("x", "ref"), Param("y", "ref")], False),
    Signature("ImportantExcerpt::level", [Param("self", "self")], False, is_method=True),
    Signature(
        "ImportantExcerpt::announce_and_return_part",
        [Param("self", "self"), Param("announcement", "ref")],
        True,
        is_method=True,
    ),
    Signature("get_or_default", [Param("s", "ref")], True),
    Signature("make_static", [], True),
]


# ==========================================================================
# B. 存活约束检查器
# ==========================================================================
@dataclass
class Binding:
    name: str
    scope_id: int
    decl_at: int
    static_: bool = False
    referent: Optional[str] = None  # 若这是引用，指向哪个绑定


@dataclass
class Scope:
    sid: int
    end_at: int = -1
    parent: Optional[int] = None


class LifetimeChecker:
    """E0597：被引用对象在引用的某个使用点已经离开作用域"""

    def __init__(self) -> None:
        self.bindings: Dict[str, Binding] = {}
        self.scopes: List[Scope] = [Scope(0)]
        self.at = 0
        self.findings: List[str] = []

    # -- 事件 ---------------------------------------------------------------
    def scope_in(self) -> None:
        self.scopes.append(Scope(len(self.scopes), parent=self.scopes[-1].sid))

    def scope_out(self) -> None:
        self.scopes[-1].end_at = self.at

    def declare(self, name: str, static_: bool = False) -> None:
        self.bindings[name] = Binding(name, self.scopes[-1].sid, self.at, static_)

    def borrow(self, ref_name: str, target: str) -> None:
        self.bindings[ref_name] = Binding(ref_name, self.scopes[-1].sid, self.at, referent=target)

    def use(self, name: str) -> None:
        self.at += 1
        b = self.bindings[name]
        if b.referent is None:
            return
        t = self.bindings[b.referent]
        if not self.alive(t, self.at):
            self.findings.append(
                f"E0597: `{t.name}` does not live long enough —— 在 `{b.name}` 的使用点 "
                f"（step {self.at}）已离开作用域"
            )

    def tick(self) -> None:
        self.at += 1

    # -- 判定 ---------------------------------------------------------------
    def alive(self, b: Binding, at: int) -> bool:
        if b.static_:
            return True  # 'static：整个程序期间都存活
        end = self.scopes[b.scope_id].end_at
        if end < 0:  # 作用域尚未结束 → 仍存活
            end = 10**9
        return b.decl_at <= at <= end

    def region(self, name: str, total: int) -> str:
        b = self.bindings[name]
        if b.static_:
            return "[" + "=" * total + "]  'static"
        end = self.scopes[b.scope_id].end_at
        end = total - 1 if end < 0 else end
        return " " * b.decl_at + "[" + "=" * max(0, end - b.decl_at) + "]"


def check_e0597_fixture(bad: bool) -> LifetimeChecker:
    ck = LifetimeChecker()
    ck.declare("r")  # 外层变量：想来活到函数末尾
    ck.tick()
    ck.scope_in()
    ck.declare("x")
    ck.tick()
    ck.borrow("r", "x")  # r = &x
    ck.tick()
    if not bad:
        ck.use("r")  # ✅ 在 x 仍存活时使用
    ck.scope_out()  # x 在此 drop
    ck.tick()
    if bad:
        ck.use("r")  # ❌ x 已经 drop → E0597
    return ck


def main() -> None:
    print("=== A. elision 引擎：这个签名还需要显式标注吗？===\n")
    need_explicit = 0
    for sig in SIGNATURES:
        lt, why = elide(sig)
        mark = "✅ 可省略" if lt is not None or not sig.returns_ref else "❌ 需显式标注"
        if mark.startswith("❌"):
            need_explicit += 1
        params = ", ".join(f"{p.name}: {p.kind}" for p in sig.params)
        print(f"  {mark}  {sig.name}({params}) -> 引用={sig.returns_ref}")
        print(f"              {why}" + (f"，推断输出 = {lt}" if lt else ""))

    print("\n=== B. 生存区间与 E0597 ===\n")
    for bad in (False, True):
        ck = check_e0597_fixture(bad)
        label = "❌ 在 x 作用域外使用 r" if bad else "✅ 在 x 作用域内使用 r"
        print(f"  {label}")
        print(f"    r 的生存区间 | {ck.region('r', 6)}")
        print(f"    x 的生存区间 | {ck.region('x', 6)}")
        print(f"    判定 = {ck.findings or ['合法']}")
    good = check_e0597_fixture(False).findings
    badc = check_e0597_fixture(True).findings
    assert good == [], good
    assert len(badc) == 1 and badc[0].startswith("E0597"), badc

    print("\n=== C. 'static 与常量提升 ===\n")
    ck = LifetimeChecker()
    ck.declare("LITERAL", static_=True)
    ck.declare("PROMOTED_AND_NONE", static_=True)
    for name in ("LITERAL", "PROMOTED_AND_NONE"):
        print(f"  {name:<20} 生存区间 | {ck.region(name, 6)}")
    print("  依据：字符串字面量直接编进二进制；`&None` 这类可常量求值的表达式会被提升到 'static 槽位")

    print("\n=== 统计 ===")
    print(f"  签名 {len(SIGNATURES)} 个，其中 {need_explicit} 个必须显式标注"
          f"（自由函数 + 多个引用入参：规则 1/2/3 都不适用）")
    print(f"  省略规则命中：规则 2 ×{sum(1 for s in SIGNATURES if elide(s)[1].startswith('规则 2'))}、"
          f"规则 3 ×{sum(1 for s in SIGNATURES if elide(s)[1].startswith('规则 3'))}、"
          f"无需标注 ×{sum(1 for s in SIGNATURES if '无需任何标注' in elide(s)[1])}")
    print("  结论：生命周期标注只写进签名（契约），不改变任何值的实际存活时间。")

    assert need_explicit == 2, need_explicit
    print("\n断言全部通过。")


if __name__ == "__main__":
    main()
