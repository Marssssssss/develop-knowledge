"""ecs_demo.py — 最小 sparse set ECS（Entity Component System）演示.

存储：每类组件一个 sparse set
    sparse : entity id -> dense 下标（无该组件则不含 key）
    dense  : dense 下标 -> entity id
    comps  : dense 下标 -> 组件数据（与 dense 一一对应）

演示：8 实体（偶数号 P+V，奇数号仅 P）-> 3 帧 movement ->
      销毁实体 2（swap-remove）-> 打印内部布局
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

NUM_ENTITIES = 8
FRAMES = 3
DT = 0.016  # 帧间隔（秒），教学用固定值

Entity = int


@dataclass
class Position:
    x: float
    y: float


@dataclass
class Velocity:
    x: float
    y: float


C = TypeVar("C")


class SparseSet(Generic[C]):
    """单类组件的 sparse set 存储（EnTT 路线）。"""

    def __init__(self) -> None:
        self.sparse: dict[Entity, int] = {}  # entity -> dense 下标
        self.dense: list[Entity] = []        # dense 下标 -> entity
        self.comps: list[C] = []             # dense 下标 -> 组件

    def __len__(self) -> int:
        return len(self.dense)

    def has(self, e: Entity) -> bool:
        return e in self.sparse

    def add(self, e: Entity, comp: C) -> None:
        """追加组件；若已存在则覆盖。"""
        if e in self.sparse:
            self.comps[self.sparse[e]] = comp
            return
        self.sparse[e] = len(self.dense)
        self.dense.append(e)
        self.comps.append(comp)

    def remove(self, e: Entity) -> None:
        """O(1) swap-remove：尾元素填补空洞并回写其 sparse 索引。"""
        idx = self.sparse.pop(e)
        last = len(self.dense) - 1
        moved = self.dense[last]
        self.dense[idx] = moved
        self.comps[idx] = self.comps[last]
        self.dense.pop()
        self.comps.pop()
        if idx != last:
            self.sparse[moved] = idx

    def get(self, e: Entity) -> C:
        return self.comps[self.sparse[e]]


class World:
    """实体分配 + 各类组件集合（教学版，无 generation）。"""

    def __init__(self) -> None:
        self.next_id = 0
        self.alive: set[Entity] = set()
        self.positions = SparseSet[Position]()
        self.velocities = SparseSet[Velocity]()

    def create(self) -> Entity:
        e = self.next_id
        self.next_id += 1
        self.alive.add(e)
        return e

    def destroy(self, e: Entity) -> None:
        if self.positions.has(e):
            self.positions.remove(e)
        if self.velocities.has(e):
            self.velocities.remove(e)
        self.alive.discard(e)


def movement_system(w: World) -> None:
    """System：查询 P+V 组合，遍历较短的集合做成员测试。"""
    p, v = w.positions, w.velocities
    base = v if len(v) < len(p) else p  # 取最小集合
    for e in list(base.dense):
        if not (p.has(e) and v.has(e)):
            continue
        pos = p.get(e)
        vel = v.get(e)
        pos.x += vel.x * DT
        pos.y += vel.y * DT


def print_layout(name: str, s: SparseSet) -> None:
    sparse_repr = [s.sparse.get(e, "X") for e in range(NUM_ENTITIES)]
    print(f"  {name}: dense={s.dense} sparse={sparse_repr}")


def print_positions(w: World, tag: str) -> None:
    print(tag)
    for e in range(NUM_ENTITIES):
        if e not in w.alive:
            print(f"  e{e}: <destroyed>")
        elif not w.positions.has(e):
            print(f"  e{e}: (no position)")
        else:
            p = w.positions.get(e)
            tag2 = "  [P+V]" if w.velocities.has(e) else "  [P]"
            print(f"  e{e}: pos=({p.x:.2f}, {p.y:.2f}){tag2}")


def main() -> None:
    w = World()

    # 偶数号实体挂 P+V，奇数号只挂 P
    for e in range(NUM_ENTITIES):
        w.create()
        w.positions.add(e, Position(float(e), 0.0))
        if e % 2 == 0:
            w.velocities.add(e, Velocity(1.0, 2.0))

    print_positions(w, "--- frame 0 (initial) ---")
    for f in range(1, FRAMES + 1):
        movement_system(w)
        print(f"--- after frame {f} ---")
        for e in range(0, NUM_ENTITIES, 2):  # 只看带速度的
            p = w.positions.get(e)
            print(f"  e{e}: pos=({p.x:.2f}, {p.y:.2f})")

    # 销毁实体 2：触发 swap-remove，观察 dense 紧凑性
    print("--- destroy e2 (swap-remove) ---")
    w.destroy(2)
    print_positions(w, "positions after destroy:")
    print("sparse set internals:")
    print_layout("Position ", w.positions)
    print_layout("Velocity", w.velocities)


if __name__ == "__main__":
    main()
