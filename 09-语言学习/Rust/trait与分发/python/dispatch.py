#!/usr/bin/env python3
"""trait 静态分发 vs 动态分发的模型化实现（Rust demo 3/5 · Python 侧）

Rust 的 trait 同时扮演三个角色，本脚本把它们拆成可计数、可断言的三件事：

  1. **接口**：方法签名集合（谁能被放进 `Vec<Box<dyn Trait>>`）
  2. **约束**：trait bound 决定哪些具体类型能进单态化
  3. **分发开关**：静态（单态化，0 次间接跳转）↔ 动态（vtable，1 次间接跳转）

另外实现两条编译期规则的校验器：
  · coherence / 孤儿规则：trait 或类型至少一个本地
  · 返回位 impl Trait：只能单一具体类型（多类型须走 trait object）

依据：The Rust Programming Language ch10-01 / ch10-02 / ch18-02、
std 文档 `core::keyword::dyn`（dyn Trait 引用含两个指针：数据 + vtable）。
运行：python3 dispatch.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

WORD_BYTES = 8  # 64 位目标：一个指针宽度


# --------------------------------------------------------------------------
# 1) 静态分发：单态化（monomorphization）
# --------------------------------------------------------------------------
@dataclass
class StaticDispatch:
    """`fn notify<T: Summary>(item: &T)`：每种用于调用的具体类型生成一份实体"""

    name: str
    call_types: List[str] = field(default_factory=list)
    bodies: Dict[str, str] = field(default_factory=dict)

    def call(self, concrete: str) -> str:
        self.call_types.append(concrete)
        self.bodies[concrete] = f"{self.name}_mono_for_{concrete}"
        return self.bodies[concrete]

    def report(self) -> str:
        return (
            f"  静态分发 {self.name}: 调用 {len(self.call_types)} 次 → "
            f"单态化实体 {len(self.bodies)} 份 {sorted(self.bodies)}，间接跳转 0 次"
        )


# --------------------------------------------------------------------------
# 2) 动态分发：胖指针 + vtable
# --------------------------------------------------------------------------
@dataclass
class DynDispatch:
    """`fn notify(item: &dyn Summary)`：只有一份实体，调用时查 vtable"""

    trait: str
    impls: Dict[str, str] = field(default_factory=dict)  # 具体类型 → 方法实现名
    call_steps: int = 0
    indirections: int = 0

    def implement(self, concrete: str, body: str) -> None:
        self.impls[concrete] = body

    def vtable(self) -> Dict[str, str]:
        return {f"slot[0]={self.trait}::summarize": "fn_ptr", **{f"impl:{k}": v for k, v in self.impls.items()}}

    def call(self, concrete: str) -> str:
        if concrete not in self.impls:
            raise TypeError(f"{concrete} 未实现 {self.trait}，不能放进 trait object")
        # 胖指针 = (data_ptr, vtable_ptr)；调用 = 取 vtable 槽 + 间接调用
        self.call_steps += 2  # ① 查表 ② 间接跳转
        self.indirections += 1
        return self.impls[concrete]

    def report(self) -> str:
        return (
            f"  动态分发 &dyn {self.trait}: 实现者 {len(self.impls)} 个 → "
            f"实体 1 份（与实现者数量无关），调用 {self.call_steps // 2} 次，"
            f"间接跳转 {self.indirections} 次"
        )


def fat_pointer_table() -> List[Tuple[str, int]]:
    """trait object 是两字宽的胖指针：数据指针 + vtable 指针"""
    return [
        ("&NewsArticle（具体类型引用，瘦指针）", 1 * WORD_BYTES),
        ("&dyn Summary（trait object，胖指针）", 2 * WORD_BYTES),
        ("Box<dyn Summary>", 2 * WORD_BYTES),
    ]


# --------------------------------------------------------------------------
# 3) coherence / 孤儿规则校验器
# --------------------------------------------------------------------------
def can_impl(trait_local: bool, type_local: bool) -> bool:
    """The Book ch10-02: 只要 trait 或类型之一是本地 crate 的就可以 impl"""
    return trait_local or type_local


COHERENCE_CASES: Sequence[Tuple[bool, bool, bool, str]] = [
    (True, True, True, "本地 trait + 本地类型"),
    (True, False, True, "本地 trait + 外部类型（如为 Vec<T> 实现自己的 trait）"),
    (False, True, True, "外部 trait + 本地类型（如为自己的类型实现 Display）"),
    (False, False, False, "外部 trait + 外部类型 → E0117 孤儿规则拒绝"),
]


# --------------------------------------------------------------------------
# 4) 返回位 impl Trait：只能单一具体类型
# --------------------------------------------------------------------------
class ImplTraitReturn:
    """模拟编译器的 opaque type 检查：所有 return 语句必须是同一具体类型"""

    def __init__(self, trait: str) -> None:
        self.trait = trait
        self.concrete = None

    def returns(self, concrete: str) -> str:
        if self.concrete is None:
            self.concrete = concrete
            return f"opaque impl {self.trait} := {concrete}"
        if concrete != self.concrete:
            raise TypeError(
                f"E0308: `if` and `else` have incompatible types "
                f"(expected opaque type `impl {self.trait}`, found `{concrete}`）"
            )
        return f"opaque impl {self.trait} := {concrete}"


def main() -> None:
    print("=== 静态分发：单态化按具体类型复制实体 ===\n")
    st = StaticDispatch(name="notify_static")
    for t in ("NewsArticle", "SocialPost"):
        st.call(t)
    print(st.report())

    print("\n=== 动态分发：一份实体 + 每次调用查 vtable ===\n")
    dy = DynDispatch(trait="Summary")
    dy.implement("NewsArticle", "NewsArticle::summarize")
    dy.implement("SocialPost", "SocialPost::summarize")
    dy.implement("Podcast", "Podcast::summarize")
    print(f"  vtable($dyn Summary) = {dy.vtable()}")
    for t in ("NewsArticle", "SocialPost", "Podcast"):
        print(f"  call(&{t:<12}) → {dy.call(t)}")
    print(dy.report())

    print("\n=== 胖指针尺寸 ===\n")
    for name, size in fat_pointer_table():
        print(f"  {name:<38} {size} 字节")

    print("\n=== 同一规模下的取舍（实现者 N=3）===")
    print(f"  {'维度':<26}{'静态 <T: Summary>':<24}{'动态 &dyn Summary'}")
    rows = [
        ("代码实体数量", f"{len(st.bodies)} 份（按具体类型）", f"{1} 份（与 N 无关）"),
        ("调用点间接跳转", "0 次（可内联）", f"{dy.indirections} 次（每个调用点 1 次）"),
        ("是否能内联", "可以（callee 编译期已知）", "通常不能"),
        ("异构集合 Vec<..>", "不支持（Vec<T> 只能一种 T）", "支持"),
        ("新增实现者的编译成本", "每次新类型都要重新单态化", "无（只多一个 impl 实体）"),
    ]
    for a, b, c in rows:
        print(f"  {a:<26}{b:<24}{c}")

    print("\n=== coherence / 孤儿规则（trait 或类型至少一个本地）===")
    for trait_local, type_local, expect, desc in COHERENCE_CASES:
        got = can_impl(trait_local, type_local)
        print(f"  [{'OK ' if got == expect else 'BAD'}] {desc:<44} 允许={got}")
        assert got == expect

    print("\n=== 返回位 impl Trait 只能单一具体类型 ===")
    r = ImplTraitReturn("Summary")
    print("  " + r.returns("SocialPost"))
    print("  " + r.returns("SocialPost"))
    try:
        r.returns("NewsArticle")
    except TypeError as e:
        print(f"  第二个不同类型 → 被拒绝：{e}")

    print("\n=== 统计 ===")
    print(f"  单态化实体 {len(st.bodies)} 份 vs 动态实体 1 份，"
          f"体现「代码体积 ↔ 间接跳转」的取舍")
    print(f"  trait object 尺寸 = {2 * WORD_BYTES} 字节 = 2 × 指针宽度({WORD_BYTES})")

    assert 2 * WORD_BYTES == 16
    print("\n断言全部通过。")


if __name__ == "__main__":
    main()
