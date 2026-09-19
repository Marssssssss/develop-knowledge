"""archetype.py — archetype（原型）存储的最小实现与自检.

Unity Entities 官方定义（docs.unity3d.com/Packages/com.unity.entities@1.0/manual/concepts-archetypes.html）：

  * archetype 是「同一世界中拥有相同组件类型组合的实体」的唯一标识；
    增删组件会把实体搬到对应的 archetype，不存在则新建。
  * 同一 archetype 的实体与组件存放在 **chunk** 里，每个 chunk **16KiB**，
    能放多少实体取决于该 archetype 组件的数量与大小。
  * chunk 内每个组件类型一个数组，外加一个存 entity id 的数组；数组**紧密排布**，
    新实体放第一个空位，删除时把 **chunk 内最后一个实体搬来填空**。
  * 所有 chunk 满 → 新建 chunk；chunk 内最后一个实体被移走 → 销毁 chunk。

本 demo 用 Python 复刻这套布局，并把「增删组件 = 跨 archetype 搬移全部组件」的代价量化出来。
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

CHUNK_SIZE = 16 * 1024            # 16 KiB —— Unity 文档原话
ENTITY_ID_SIZE = 4                # demo 约定：entity id 占 4 字节（非 Unity 官方数值）
COMPONENT_SIZE = {"Position": 8, "Velocity": 8, "Health": 4}

Key = FrozenSet[str]

_ASSERTIONS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _ASSERTIONS
    if not cond:
        raise AssertionError(f"{label} 失败: {detail}")
    _ASSERTIONS += 1
    print(f"ok {_ASSERTIONS:>2} {label}" + (f"  [{detail}]" if detail else ""))


def key_of(names: Iterable[str]) -> Key:
    return frozenset(names)


def entity_size(key: Key) -> int:
    return ENTITY_ID_SIZE + sum(COMPONENT_SIZE[c] for c in key)


def chunk_capacity(key: Key) -> int:
    return CHUNK_SIZE // entity_size(key)


class Chunk:
    """一个 16KiB 的均匀内存块：每组件一个数组 + 一个 entity id 数组。"""

    def __init__(self, key: Key) -> None:
        self.key = key
        self.capacity = chunk_capacity(key)
        self.arrays: Dict[str, List[Optional[float]]] = {
            c: [None] * self.capacity for c in sorted(key)
        }
        self.ids: List[Optional[int]] = [None] * self.capacity
        self.count = 0

    def __repr__(self) -> str:  # pragma: no cover - 仅调试
        return f"<Chunk {sorted(self.key)} {self.count}/{self.capacity}>"


class Archetype:
    def __init__(self, key: Key) -> None:
        self.key = key
        self.chunks: List[Chunk] = []

    def free_chunk(self) -> Chunk:
        for ch in self.chunks:
            if ch.count < ch.capacity:
                return ch
        ch = Chunk(self.key)
        self.chunks.append(ch)
        return ch


class World:
    def __init__(self) -> None:
        self.archetypes: Dict[Key, Archetype] = {}
        self.loc: Dict[int, Tuple[Key, Chunk, int]] = {}
        self.comps: Dict[int, Dict[str, float]] = {}
        self.next_id = 0
        self.copies = 0            # 结构变更中「写入 chunk 的组件个数」累计
        self.arch_version = 0      # 新建 archetype 时 +1（查询缓存据此失效）

    # ---- 内部 -------------------------------------------------------
    def _archetype(self, key: Key) -> Archetype:
        arch = self.archetypes.get(key)
        if arch is None:
            arch = Archetype(key)
            self.archetypes[key] = arch
            self.arch_version += 1
        return arch

    def _place(self, eid: int, arch: Archetype) -> None:
        ch = arch.free_chunk()
        idx = ch.count
        ch.ids[idx] = eid
        for c, arr in ch.arrays.items():
            arr[idx] = self.comps[eid][c]
            self.copies += 1
        ch.count += 1
        self.loc[eid] = (arch.key, ch, idx)

    def _unplace(self, eid: int) -> None:
        _key, ch, idx = self.loc.pop(eid)
        last = ch.count - 1
        moved = ch.ids[last]
        if moved != eid:                       # swap-remove：chunk 内最后一个实体填空
            ch.ids[idx] = moved
            for c, arr in ch.arrays.items():
                arr[idx] = arr[last]
            self.loc[moved] = (ch.key, ch, idx)
        ch.ids[last] = None
        for arr in ch.arrays.values():
            arr[last] = None
        ch.count -= 1

    # ---- 公开 API ---------------------------------------------------
    def create(self, **comps: float) -> int:
        eid = self.next_id
        self.next_id += 1
        self.comps[eid] = dict(comps)
        self._place(eid, self._archetype(key_of(comps)))
        return eid

    def destroy(self, eid: int) -> None:
        _key, ch, _idx = self.loc[eid]
        self._unplace(eid)
        self.comps.pop(eid, None)
        if ch.count == 0:
            arch = self.archetypes[ch.key]
            arch.chunks.remove(ch)

    def add(self, eid: int, name: str, value: float) -> None:
        self.comps[eid][name] = value
        self._unplace(eid)
        self._place(eid, self._archetype(key_of(self.comps[eid])))

    def remove(self, eid: int, name: str) -> None:
        self.comps[eid].pop(name)
        self._unplace(eid)
        self._place(eid, self._archetype(key_of(self.comps[eid])))

    def archetype_of(self, eid: int) -> Key:
        return self.loc[eid][0]

    def query(self, *names: str) -> List[int]:
        want = set(names)
        out: List[int] = []
        for key, arch in self.archetypes.items():
            if not want <= set(key):
                continue
            for ch in arch.chunks:
                for i in range(ch.count):
                    out.append(ch.ids[i])          # type: ignore[arg-type]
        return out


class Query:
    """缓存匹配 archetype 列表；新建 archetype（arch_version 变化）时重算。"""

    def __init__(self, world: World, *names: str) -> None:
        self.world = world
        self.want = set(names)
        self.version = -1
        self.recomputes = 0
        self.matched: List[Archetype] = []

    def archetypes(self) -> List[Archetype]:
        if self.version != self.world.arch_version:
            self.matched = [a for k, a in self.world.archetypes.items() if self.want <= set(k)]
            self.version = self.world.arch_version
            self.recomputes += 1
        return self.matched

    def run(self) -> List[int]:
        out: List[int] = []
        for arch in self.archetypes():
            for ch in arch.chunks:
                out.extend(ch.ids[: ch.count])     # type: ignore[misc]
        return out


def main() -> None:
    cap_pv = chunk_capacity(key_of(["Position", "Velocity"]))
    check("chunk 容量 = 16KiB / 单实体字节数", cap_pv == CHUNK_SIZE // (4 + 8 + 8),
          f"capacity={cap_pv}")
    check("空 archetype 容量最大", chunk_capacity(key_of([])) == CHUNK_SIZE // ENTITY_ID_SIZE,
          f"{chunk_capacity(key_of([]))}")
    check("组件越多容量越小", chunk_capacity(key_of(["Position", "Velocity", "Health"])) < cap_pv)

    w = World()
    ids = [w.create(Position=float(i), Velocity=1.0) for i in range(cap_pv + 1)]
    arch_pv = w.archetypes[key_of(["Position", "Velocity"])]
    check("超过容量才新建 chunk", len(arch_pv.chunks) == 2, f"chunks={len(arch_pv.chunks)}")
    check("首个 chunk 是满的", arch_pv.chunks[0].count == cap_pv, f"{arch_pv.chunks[0].count}")
    check("chunk 内紧密排布（id 连续）", arch_pv.chunks[0].ids[:3] == ids[:3],
          f"{arch_pv.chunks[0].ids[:3]}")
    check("组件数组与 id 数组同下标对齐",
          arch_pv.chunks[0].arrays["Position"][5] == 5.0 and arch_pv.chunks[0].ids[5] == ids[5])

    # swap-remove 只在 chunk 内部发生（不是跨 chunk 搬）
    first_chunk_ids_before = list(arch_pv.chunks[0].ids[: arch_pv.chunks[0].count])
    victim = arch_pv.chunks[0].ids[0]
    filler = first_chunk_ids_before[-1]
    w.destroy(victim)
    check("删除后由本 chunk 最后一个实体填空", arch_pv.chunks[0].ids[0] == filler,
          f"ids[0]={arch_pv.chunks[0].ids[0]} filler={filler}")
    check("被搬移者的 loc 已回写", w.loc[filler][2] == 0, f"idx={w.loc[filler][2]}")
    check("被删实体已无定位", victim not in w.loc)
    check("count 递减、数组无空洞",
          arch_pv.chunks[0].count == cap_pv - 1
          and all(arch_pv.chunks[0].ids[i] is not None for i in range(arch_pv.chunks[0].count)))

    # chunk 空了就销毁
    w2 = World()
    solo = w2.create(Position=1.0)
    w2.destroy(solo)
    check("chunk 内最后一个实体被移走即销毁 chunk",
          len(w2.archetypes[key_of(["Position"])].chunks) == 0)

    # 结构变更：加/删组件 = 换 archetype 并搬走全部组件
    w3 = World()
    e = w3.create(Position=0.0, Velocity=2.0)
    before = w3.copies
    w3.add(e, "Health", 10.0)
    check("加组件后换了 archetype", w3.archetype_of(e) == key_of(["Position", "Velocity", "Health"]))
    check("旧组件数据保留", w3.comps[e]["Position"] == 0.0 and w3.comps[e]["Velocity"] == 2.0)
    check("搬移代价 = 新组合的组件数", w3.copies - before == 3, f"copies={w3.copies - before}")
    key3 = key_of(["Position", "Velocity", "Health"])
    check("组件值在新 chunk 里已落盘",
          w3.archetypes[key3].chunks[0].arrays["Health"][0] == 10.0)
    before = w3.copies
    w3.remove(e, "Health")
    check("删组件回到原 archetype", w3.archetype_of(e) == key_of(["Position", "Velocity"]))
    check("删组件同样按新组合搬移", w3.copies - before == 2, f"copies={w3.copies - before}")

    # archetype 唯一性 / 稳定性
    w4 = World()
    w4.create(Position=1.0, Velocity=1.0)
    w4.create(Velocity=1.0, Position=2.0)
    check("组件顺序不影响 archetype", len(w4.archetypes) == 1)
    n_arch = len(w4.archetypes)
    for i in range(50):
        w4.create(Position=float(i), Velocity=1.0)
    check("archetype 集合很快稳定", len(w4.archetypes) == n_arch, f"{len(w4.archetypes)}")

    # 查询：按「archetype 组件集合是查询的超集」匹配
    w5 = World()
    a = w5.create(Position=1.0)
    b = w5.create(Position=2.0, Velocity=2.0)
    w5.create(Health=3.0)
    check("查询单组件命中所有超集", sorted(w5.query("Position")) == sorted([a, b]),
          f"{w5.query('Position')}")
    check("查询两组件只命中同时拥有者", w5.query("Position", "Velocity") == [b])
    check("查询不到的组合返回空", w5.query("Position", "Health") == [])

    q = Query(w5, "Position")
    q.run()
    r1 = q.recomputes
    w5.create(Position=9.0)                      # 已有 archetype，不触发重算
    q.run()
    check("已有 archetype 不使查询缓存失效", q.recomputes == r1, f"{q.recomputes}")
    w5.create(Position=8.0, Health=1.0)          # 新 archetype → 缓存失效
    q.run()
    check("新建 archetype 使查询缓存失效", q.recomputes == r1 + 1, f"{q.recomputes}")
    check("缓存查询结果与全量扫描一致", sorted(q.run()) == sorted(w5.query("Position")))

    # 迭代连续性：查询输出按 chunk 紧密排列
    arch = w5.archetypes[key_of(["Position"])]
    check("迭代输出无空洞",
          all(arch.chunks[0].ids[i] is not None for i in range(arch.chunks[0].count)))

    print(f"\n全部 {_ASSERTIONS} 条断言通过")


if __name__ == "__main__":
    main()
