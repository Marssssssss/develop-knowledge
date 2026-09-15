#!/usr/bin/env python3
"""智能指针行为的模型化实现（Rust demo 4/5 · Python 侧）

Python 侧不能复刻 Rust 的编译期保证，但可以把 4 个智能指针的**运行期语义**建模清楚：

  BoxSim      间接层 → 让递归类型有确定大小（无间接则「无限大小」E0072）
  Deref 链     deref coercion 的编译期插入次数（运行期 0 开销）
  RcSim/WeakSim 强/弱引用计数 + 环检测 → 量化「泄漏」
  RefCellSim  运行期借用规则（违反抛 BorrowMutError，而不是 UB）

依据：The Rust Programming Language ch15-01 / ch15-04 / ch15-05 / ch15-06。
运行：python3 smart_ptr.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

WORD = 8  # 指针宽度（64 位）


# ==========================================================================
# 1) 递归类型的大小：没有间接层就是「无限大小」
# ==========================================================================
class InfiniteSize(Exception):
    """对应 rustc 的 E0072: recursive type has infinite size"""


def type_size(ty: str, indirection: bool, depth: int = 0) -> int:
    """按「枚举取最大变体」规则递归计算大小；无间接层时递归不会终止 → E0072"""
    if depth > 64:
        raise InfiniteSize(f"recursive type `{ty}` has infinite size（递归无间接层）")
    if ty == "i32":
        return 4
    if ty == "List":
        # enum List { Cons(i32, List), Nil }  —— 取最大变体
        tail = WORD if indirection else type_size("List", indirection, depth + 1)
        return align(4 + tail, WORD)
    raise KeyError(ty)


def align(n: int, to: int) -> int:
    return (n + to - 1) // to * to


# ==========================================================================
# 2) deref coercion：编译期插入 Deref::deref 直到类型匹配
# ==========================================================================
def deref_chain(start: str, target: str, table: Dict[str, str]) -> Tuple[List[str], int]:
    """返回（转换路径, 插入的 deref 次数）；无法到达则抛 TypeError"""
    path, cur, steps = [start], start, 0
    while cur != target:
        if cur not in table:
            raise TypeError(f"{start} 无法通过 deref coercion 变成 {target}")
        cur = table[cur]
        path.append(cur)
        steps += 1
    return path, steps


# ==========================================================================
# 3) 引用计数：Rc / Weak / 引用环
# ==========================================================================
@dataclass
class HeapObject:
    label: str
    strong: int = 0
    weak: int = 0
    freed: bool = False
    edges: Dict[str, str] = field(default_factory=dict)  # 字段名 → 目标对象 label


class Heap:
    """伪堆：strong 归 0 即释放；weak 计数不影响释放"""

    def __init__(self) -> None:
        self.objects: Dict[str, HeapObject] = {}
        self.freed: List[str] = []

    def alloc(self, label: str) -> HeapObject:
        obj = HeapObject(label)
        self.objects[label] = obj
        return obj

    def _release(self, label: str) -> None:
        obj = self.objects[label]
        obj.strong -= 1
        if obj.strong == 0:
            obj.freed = True
            self.freed.append(label)
            for tgt in obj.edges.values():  # 释放时递归释放其强引用边
                self._release(tgt)

    def rc_clone(self, label: str) -> str:
        self.objects[label].strong += 1
        return label

    def weak_upgrade(self, label: str) -> bool:
        obj = self.objects[label]
        if obj.freed:
            return False
        obj.strong += 1
        return True

    def rc_downgrade(self, label: str) -> str:
        self.objects[label].weak += 1
        return label

    def drop(self, label: str) -> None:
        self._release(label)

    def drop_weak(self, label: str) -> None:
        self.objects[label].weak -= 1


def build_cycle(heap: Heap) -> Tuple[str, str]:
    """x.next = y; y.next = x  —— 强引用成环"""
    x = heap.alloc("x")
    y = heap.alloc("y")
    heap.rc_clone("x")  # 变量 x 持有
    heap.rc_clone("y")  # 变量 y 持有
    x.edges["next"] = heap.rc_clone("y")  # x.next = Rc::clone(&y)
    y.edges["next"] = heap.rc_clone("x")  # y.next = Rc::clone(&x)
    return "x", "y"


# ==========================================================================
# 4) RefCell：运行期借用规则
# ==========================================================================
class BorrowError(Exception):
    def __str__(self) -> str:  # noqa: D105
        return "already borrowed"


class BorrowMutError(BorrowError):
    def __str__(self) -> str:  # noqa: D105
        return "already borrowed: BorrowMutError"


class RefCellSim:
    """boxes/refs 在编译期检查；RefCell 把同一套规则搬到运行期，违反即抛错"""

    def __init__(self, value: int) -> None:
        self.value = value
        self.shared = 0
        self.mutable = 0

    def borrow(self) -> int:
        if self.mutable:
            raise BorrowMutError()
        self.shared += 1
        return self.shared

    def borrow_mut(self) -> int:
        if self.mutable or self.shared:
            raise BorrowMutError()
        self.mutable += 1
        return self.mutable

    def release(self, mutable: bool = False) -> None:
        if mutable:
            self.mutable -= 1
        else:
            self.shared -= 1


def main() -> None:
    print("=== 1. 递归类型的大小：间接层决定「算不算得出来」===\n")
    try:
        type_size("List", indirection=False)
    except InfiniteSize as e:
        print(f"  无间接层（Cons(i32, List)）→ {e}")
    size = type_size("List", indirection=True)
    print(f"  有间接层（Cons(i32, Box<List>)）→ size_of::<List>() = {size} 字节")
    print(f"    计算过程：i32(4) + 指针({WORD}) = 12 → 对齐到 {size}")
    assert size == 16

    print("\n=== 2. deref coercion 的编译期插入次数 ===\n")
    table = {"MyBox<String>": "String", "String": "str", "MyBox<&str>": "&str"}
    for src, dst in (("MyBox<String>", "str"), ("MyBox<&str>", "&str")):
        path, steps = deref_chain(src, dst, table)
        print(f"  {src} → {dst}: {' → '.join(path)}，插入 Deref::deref {steps} 次")
    _, need = deref_chain("MyBox<String>", "str", table)
    assert need == 2, "&MyBox<String> → &String → &str 需要 2 次 deref"
    print(f"  → 转换在编译期确定（本 demo 里为 {need} 次）；Rust 不会留到运行期再解析")

    print("\n=== 3. Rc 强引用计数轨迹（Book Listing 15-19）===\n")
    heap = Heap()
    a = heap.alloc("a")
    heap.rc_clone("a")
    trace = [a.strong]
    heap.rc_clone("a")
    trace.append(a.strong)  # b = Rc::clone(&a)
    heap.rc_clone("a")
    trace.append(a.strong)  # c 在内层作用域
    heap.drop("a")
    trace.append(a.strong)  # c 离开作用域
    print(f"  strong_count 轨迹 = {trace}（Book 输出 [1, 2, 3, 2]）")
    assert trace == [1, 2, 3, 2]

    print("\n=== 4. RefCell：运行期借用规则 ===\n")
    c = RefCellSim(5)
    c.borrow()
    c.borrow()
    print(f"  两个共享借用后 borrow_mut() → {_try(lambda: c.borrow_mut())}")
    c.release()
    c.release()
    print(f"  释放后 borrow_mut() → 成功，mutable={c.borrow_mut()}")
    print(f"  持有可变借用时 borrow() → {_try(c.borrow)}")
    c.release(mutable=True)
    print(f"  释放后 borrow() → 成功，shared={c.borrow()}")

    print("\n=== 5. 引用环 → 永不释放 ===\n")
    heap2 = Heap()
    x, y = build_cycle(heap2)
    print(f"  成环后 strong: x={heap2.objects[x].strong} y={heap2.objects[y].strong}")
    heap2.drop(x)
    heap2.drop(y)
    print(f"  两个变量都离开作用域后 strong: x={heap2.objects[x].strong} y={heap2.objects[y].strong}")
    print(f"  已释放对象 = {heap2.freed or '无'} → 环上的对象永不回收（泄漏，但内存安全）")
    assert heap2.freed == [] and heap2.objects[x].strong == 1

    print("\n=== 6. Weak 破环：子→父用弱引用 ===\n")
    heap3 = Heap()
    leaf = heap3.alloc("leaf")
    heap3.rc_clone("leaf")
    heap3.alloc("leaf.parent")  # 空 Weak<Node>
    print(f"  leaf 建立后: strong={leaf.strong} weak={leaf.weak}，parent.upgrade()={None}")
    branch = heap3.alloc("branch")
    heap3.rc_clone("branch")
    branch.edges["children"] = heap3.rc_clone("leaf")  # 父持子：强
    heap3.rc_downgrade("branch")  # 子持父：弱
    print(
        f"  挂接后 branch: strong={branch.strong} weak={branch.weak} / "
        f"leaf: strong={leaf.strong} weak={leaf.weak}"
    )
    heap3.drop("branch")  # branch 离开作用域
    print(f"  branch 离开作用域后：已释放 = {heap3.freed}，leaf.strong={leaf.strong}")
    print(f"  leaf.parent.upgrade() → {heap3.weak_upgrade('branch')}（None：父已释放）")
    assert "branch" in heap3.freed, "branch 应被释放（weak 计数不阻止释放）"
    assert heap3.objects["leaf"].freed is False, "leaf 仍被变量持有，不应释放"

    print("\n=== 统计 ===")
    print(f"  引用环泄漏对象 {2} 个（x, y）；Weak 版本正确释放 branch")
    print("  关键差别：strong 计数决定释放，weak 计数只影响 upgrade 的返回值")
    print("\n断言全部通过。")


def _try(fn) -> str:
    try:
        fn()
        return "成功"
    except BorrowError as e:
        return f"抛出 {type(e).__name__}: {e}"


if __name__ == "__main__":
    main()
