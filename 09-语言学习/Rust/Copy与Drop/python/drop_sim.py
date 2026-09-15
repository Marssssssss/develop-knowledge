#!/usr/bin/env python3
"""drop 时机与顺序的模型化实现（Rust demo 2/5 · Python 侧）

Rust Reference 的 *Destructors* 一节用 4 条规则决定了析构顺序：

  1. 变量：同一作用域内按「声明顺序的**逆序**」drop
  2. 作用域：同时离开多个作用域时「由内向外」
  3. 类型内部：struct/枚举字段按**声明顺序**、元组按顺序、数组按**首→尾**
  4. 赋值/部分初始化：覆盖赋值立刻 drop 旧值；部分 move 后只 drop 剩余字段

本脚本把这 4 条规则实现成一个可执行的作用域机，输入是事件序列，输出是 drop 日志，
并额外统计「被 mem::forget 拦掉的析构」（泄漏是内存安全的，但对象永不回收）。

依据：Rust Reference *Destructors*（见 README 参考资料）。
运行：python3 drop_sim.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


# --------------------------------------------------------------------------
# 值：只有「怎么 drop」是重要的
# --------------------------------------------------------------------------
@dataclass
class Scalar:
    label: str


@dataclass
class Fields:
    """struct / enum 变体 / 元组：字段按声明顺序 drop"""

    name: str
    items: List[str]


@dataclass
class Elements:
    """数组 / owned slice：元素首→尾 drop"""

    name: str
    items: List[str]


Value = object  # Scalar | Fields | Elements


@dataclass
class Binding:
    var: str
    value: Value
    forgotten: bool = False
    moved_out: List[int] = field(default_factory=list)


@dataclass
class Scope:
    name: str
    bindings: List[Binding] = field(default_factory=list)


class DropSim:
    """作用域机：按 Rust Reference 的 4 条规则执行析构"""

    def __init__(self) -> None:
        self.log: List[str] = []
        self.leaked: List[str] = []
        self.scopes: List[Scope] = [Scope("<entire function>")]
        self.left_scopes: List[str] = []

    # -- 事件 ---------------------------------------------------------------
    def scope_in(self, name: str) -> None:
        self.scopes.append(Scope(name))

    def scope_out(self) -> None:
        scope = self.scopes.pop()
        self.left_scopes.append(scope.name)
        for b in reversed(scope.bindings):  # 规则 1：逆声明序
            self._drop_binding(b)

    def declare(self, var: str, value: Value) -> None:
        self.scopes[-1].bindings.append(Binding(var, value))

    def declare_pattern(self, vars_and_values: Sequence[Tuple[str, Value]]) -> None:
        """`let (a, b, ..) = ..`：绑定按模式内声明顺序进入作用域，
        离开时由规则 1 的逆序自然变成「模式内逆序」。"""
        for var, value in vars_and_values:
            self.declare(var, value)

    def move_to(self, src: str, dst: str) -> None:
        """所有权转移（move）：值换了个绑定，析构仍然只发生一次"""
        b = self._find(src)
        for scope in reversed(self.scopes):
            if b in scope.bindings:
                scope.bindings.remove(b)
                break
        self.scopes[-1].bindings.append(Binding(dst, b.value))

    def assign(self, var: str, value: Value) -> None:
        """规则 4：赋值会运行左操作数（旧值）的析构函数"""
        for scope in reversed(self.scopes):
            for b in scope.bindings:
                if b.var == var:
                    self.log.append(f"{self._flat(b.value)}（覆盖赋值 → 立即 drop）")
                    b.value = value
                    b.forgotten = False
                    b.moved_out = []
                    return
        raise LookupError(var)

    def forget(self, var: str) -> None:
        """mem::forget：析构被抑制（不泄漏内存安全，但资源不回收）"""
        b = self._find(var)
        b.forgotten = True
        self.leaked.append(self._flat(b.value))

    def partial_forget(self, var: str, index: int) -> None:
        """mem::forget(v.1)：部分 move —— 只有剩余字段会在作用域结束时 drop"""
        b = self._find(var)
        b.moved_out.append(index)
        self.leaked.append(self._flat(b.value, only=index))

    # -- 内部 ---------------------------------------------------------------
    def _find(self, var: str) -> Binding:
        for scope in reversed(self.scopes):
            for b in scope.bindings:
                if b.var == var:
                    return b
        raise LookupError(var)

    @staticmethod
    def _flat(value: Value, only: Optional[int] = None) -> str:
        if isinstance(value, Scalar):
            return value.label
        items = value.items
        return items[only] if only is not None else "[" + ", ".join(items) + "]"

    def _drop_binding(self, b: Binding) -> None:
        if b.forgotten:
            return
        if isinstance(b.value, Scalar):
            self.log.append(b.value.label)
            return
        # 规则 3：struct/元组字段按声明顺序、数组元素首→尾
        for i, label in enumerate(b.value.items):
            if i in b.moved_out:
                continue  # 已被 move 走，这里不再 drop
            self.log.append(label)

    # -- 可视化 -------------------------------------------------------------
    def diagram(self) -> str:
        rows = ["  drop scope 嵌套（内层先离开）："]
        for i, s in enumerate(self.scopes):
            rows.append("    " + "  " * i + f"└─ {s.name}  ({len(s.bindings)} 个绑定)")
        rows.append("  已离开的作用域顺序：" + " → ".join(self.left_scopes))
        return "\n".join(rows)


# --------------------------------------------------------------------------
# 事件序列（与 Rust 侧 main.rs 的场景一一对应）
# --------------------------------------------------------------------------
def sc_reverse_declaration(sim: DropSim) -> None:
    sim.scope_in("block")
    for name in ("a", "b", "c"):
        sim.declare(name, Scalar(name))
    sim.scope_out()


def sc_inner_block_first(sim: DropSim) -> None:
    sim.scope_in("outer")
    sim.declare("outer-1", Scalar("outer-1"))
    sim.scope_in("inner")
    sim.declare("inner", Scalar("inner"))
    sim.scope_out()  # 规则 2：内层先离开
    sim.declare("outer-2", Scalar("outer-2"))
    sim.scope_out()


def sc_overwrite(sim: DropSim) -> None:
    sim.scope_in("block")
    sim.declare("v", Scalar("旧值-old"))
    sim.assign("v", Scalar("新值-new"))
    sim.scope_out()


def sc_move_drops_once(sim: DropSim) -> None:
    sim.scope_in("block")
    sim.declare("moved", Scalar("move 不触发析构"))
    sim.move_to("moved", "taken")  # 所有权转移 → 不析构、不重复析构
    sim.scope_out()


def sc_struct_fields(sim: DropSim) -> None:
    sim.scope_in("block")
    sim.declare("t", Fields("Triple", ["field-a", "field-b", "field-c"]))
    sim.scope_out()


def sc_array_elements(sim: DropSim) -> None:
    sim.scope_in("block")
    sim.declare("arr", Elements("[..]", ["elem-0", "elem-1", "elem-2"]))
    sim.scope_out()


def sc_tuple_pattern(sim: DropSim) -> None:
    sim.scope_in("block")
    sim.declare_pattern(
        [("pat-first", Scalar("pat-first")), ("pat-last", Scalar("pat-last"))]
    )
    sim.scope_out()


def sc_forget_and_partial_move(sim: DropSim) -> None:
    sim.scope_in("block")
    sim.declare("forgotten", Scalar("forgotten：永不 drop"))
    sim.forget("forgotten")
    sim.declare("partial", Fields("(_, _)", ["partial-0", "partial-1"]))
    sim.partial_forget("partial", 1)
    sim.scope_out()  # 只 drop partial-0


SCENARIOS = [
    ("同一作用域：逆声明序", sc_reverse_declaration, ["c", "b", "a"]),
    ("离开多层作用域：由内向外", sc_inner_block_first, ["inner", "outer-2", "outer-1"]),
    (
        "覆盖赋值：旧值立即 drop",
        sc_overwrite,
        ["旧值-old（覆盖赋值 → 立即 drop）", "新值-new"],
    ),
    ("move 不触发析构（只 drop 一次）", sc_move_drops_once, ["move 不触发析构"]),
    ("struct 字段：声明顺序", sc_struct_fields, ["field-a", "field-b", "field-c"]),
    ("数组元素：首 → 尾", sc_array_elements, ["elem-0", "elem-1", "elem-2"]),
    ("模式内绑定：逆声明序", sc_tuple_pattern, ["pat-last", "pat-first"]),
    ("forget + 部分 move", sc_forget_and_partial_move, ["partial-0"]),
]


def main() -> None:
    print("=== drop 时机与顺序（Rust Reference · Destructors）===\n")
    ok = 0
    total_leaked: List[str] = []
    for name, fn, expect in SCENARIOS:
        sim = DropSim()
        fn(sim)
        got = sim.log
        passed = got == expect
        ok += int(passed)
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        print(f"        实际 drop 序 = {got}")
        print(f"        期望 drop 序 = {expect}")
        if sim.leaked:
            print(f"        被 forget 抑制的析构 = {sim.leaked}（对象永不回收，但仍是内存安全的）")
        total_leaked.extend(sim.leaked)

    print("\n=== drop scope 嵌套示意（以「内层块先离开」为例）===")
    sim = DropSim()
    sim.scope_in("function body block")
    sim.declare("outer-1", Scalar("outer-1"))
    sim.scope_in("inner block")
    sim.declare("inner", Scalar("inner"))
    print(sim.diagram())  # 此刻两层都还在
    sim.scope_out()
    sim.scope_out()
    print(sim.diagram())

    print("=== 统计 ===")
    print(f"  场景 {len(SCENARIOS)} 个（{ok} 通过 / {len(SCENARIOS) - ok} 失败）")
    print(f"  被 forget 抑制析构的对象 {len(total_leaked)} 个：{total_leaked}")
    print("  规则命中：逆声明序 / 由内向外 / 字段声明序 / 元素首→尾 / 模式内逆序 / 覆盖即 drop / 部分 move")

    assert ok == len(SCENARIOS), "drop 顺序与 Rust Reference 不符"
    print("\n断言全部通过。")


if __name__ == "__main__":
    main()
