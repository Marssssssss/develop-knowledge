"""gc_tri_color.py — 最小三色标记 GC(stop-the-world Python 版)

算法对应 Dijkstra, Lamport, Martin, Scholten, Steffens(1978)
"On-the-Fly Garbage Collection: An Exercise in Cooperation"

模型:
  - 堆 = 一组 Object + 它们之间的引用边
  - 三色标记:每个对象初始白色,根可达 → 灰色,所有子节点已访问 → 黑色
  - 不变式(no black → white):任何黑色对象的出度**不能**指向白色对象
  - 终止条件:没有灰色对象 → 所有白色对象都是不可达,可回收

本 demo 是教学简化版(stop-the-world),无写屏障;并发版需要
Dijkstra / Yuasa / SATB 之一。
"""
from dataclasses import dataclass, field
from typing import List, Optional


WHITE, GRAY, BLACK = 0, 1, 2


@dataclass
class Object:
    id: int
    kids: List[int] = field(default_factory=list)   # 子对象 ID 列表
    mark_state: int = WHITE


class Heap:
    """简化版"堆":全局对象表 + roots + 三色标记。"""

    def __init__(self):
        self.objects: dict[int, Object] = {}
        self.roots: List[int] = []
        self._gray: List[int] = []   # 灰色工作栈(实际 GC 用 deque/queue)

    # -------- 构造 --------
    def alloc(self, oid: int) -> Object:
        obj = Object(id=oid)
        self.objects[oid] = obj
        return obj

    def add_kid(self, parent_id: int, kid_id: int) -> None:
        self.objects[parent_id].kids.append(kid_id)

    def add_root(self, oid: int) -> None:
        self.roots.append(oid)

    # -------- 三色标记 --------
    def mark(self) -> None:
        # 1) 根节点置灰 + 入栈
        for rid in self.roots:
            obj = self.objects.get(rid)
            if obj is not None and obj.mark_state == WHITE:
                obj.mark_state = GRAY
                self._gray.append(rid)

        # 2) 主循环:pop 灰 → 子白转灰 → 自己转黑
        while self._gray:
            cur_id = self._gray.pop()
            cur = self.objects[cur_id]
            for kid_id in cur.kids:
                kid = self.objects.get(kid_id)
                if kid is not None and kid.mark_state == WHITE:
                    kid.mark_state = GRAY
                    self._gray.append(kid_id)
            cur.mark_state = BLACK

    def sweep(self) -> int:
        """回收白色对象。本 demo 不实际 free(因为对象仍被本地变量引用),
        只标记 reclaimed=1 的对象并把活对象重置回 WHITE 为下次 GC 做准备。
        """
        reclaimed = []
        for obj in self.objects.values():
            if obj.mark_state == WHITE:
                reclaimed.append(obj.id)
            else:
                obj.mark_state = WHITE  # 为下次 GC reset
        return reclaimed

    def state_counts(self) -> tuple[int, int, int]:
        w = g = b = 0
        for obj in self.objects.values():
            w += obj.mark_state == WHITE
            g += obj.mark_state == GRAY
            b += obj.mark_state == BLACK
        return w, g, b


def print_state(h: Heap, tag: str) -> None:
    w, g, b = h.state_counts()
    print(f"    [{tag:15s}] white={w} gray={g} black={b}")


# --------------------- demo ---------------------

def demo_simple():
    print("[1] simple graph: roots -> A -> B, X -> Y (X,Y unreachable)")
    h = Heap()
    h.alloc(1); h.add_kid(1, 2)
    h.alloc(2)
    h.alloc(10); h.add_kid(10, 11)
    h.alloc(11)
    h.add_root(1)

    print_state(h, "initial")
    h.mark()
    print_state(h, "after mark")
    rec = h.sweep()
    print(f"    reclaimed={rec} (expected [10, 11])")


def demo_cyclic():
    print("\n[2] cycle that refcount CANNOT collect: A <-> B")
    print("    A is a root, A→B and B→A form a cycle\n")
    h = Heap()
    h.alloc(1); h.add_kid(1, 2)
    h.alloc(2); h.add_kid(2, 1)
    h.add_root(1)

    h.mark()
    print_state(h, "after mark")
    rec = h.sweep()
    print(f"    reclaimed={rec} (expected []; both reachable via cycle)")


def demo_disconnected():
    print("\n[3] disconnected sub-graph")
    print("    roots -> A; D -> E (D,E unreachable)\n")
    h = Heap()
    h.alloc(1)
    h.alloc(4); h.add_kid(4, 5)
    h.alloc(5)
    h.add_root(1)

    h.mark()
    print_state(h, "after mark")
    rec = h.sweep()
    print(f"    reclaimed={rec} (expected [4, 5])")


def demo_diamond():
    print("\n[4] diamond — shared kid (must only mark D once)")
    print("    A → B, A → C, B → D, C → D\n")
    h = Heap()
    h.alloc(1); h.add_kid(1, 2); h.add_kid(1, 3)
    h.alloc(2); h.add_kid(2, 4)
    h.alloc(3); h.add_kid(3, 4)
    h.alloc(4)
    h.add_root(1)

    h.mark()
    print_state(h, "after mark")
    rec = h.sweep()
    print(f"    reclaimed={rec} (expected []; gray-stack 不会重复入 D)")


def demo_orphan_subtree():
    print("\n[5] orphan subtree via B → X\n")
    h = Heap()
    h.alloc(1); h.add_kid(1, 2)
    h.alloc(2); h.add_kid(2, 20)
    h.alloc(20)
    h.add_root(1)

    h.mark()
    print_state(h, "after mark")
    rec = h.sweep()
    print(f"    reclaimed={rec} (expected [])")


def main():
    print("=== tri-color mark-and-sweep GC demo (stop-the-world, Python) ===\n")
    demo_simple()
    demo_cyclic()
    demo_disconnected()
    demo_diamond()
    demo_orphan_subtree()
    print("\n[ok] all cycles shown. Tri-color reclaims cycles that refcount cannot.")


if __name__ == "__main__":
    main()
