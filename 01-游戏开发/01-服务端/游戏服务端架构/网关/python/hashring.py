#!/usr/bin/env python3
"""一致性哈希环 + 重映射率 / 负载均衡度量(被 gateway.py 引用).

与 c/hashring.{h,c}、go/hashring.go 一一对应:
  uid 取模:实现最简单, 但节点数一变几乎全员重映射;
  一致性哈希:节点与虚拟节点按哈希排成环, 取顺时针后继, 增删节点只影响相邻区间(约 1/N)。
"""

from __future__ import annotations

import bisect
from collections import defaultdict

VNODES = 512   # 一致性哈希的虚拟节点数(每物理节点)


# =====================================================================
# 二、实例选择: uid 取模 vs 一致性哈希
# =====================================================================
def fnv1a32(s: str) -> int:
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


class ModuloPicker:
    """uid % n: 实现最简单, 但节点数一变几乎全员重映射。"""

    def __init__(self, n: int) -> None:
        self.n = n

    def pick(self, uid: int) -> int:
        return uid % self.n


class RingPicker:
    """一致性哈希: 节点与虚拟节点按哈希排成环, 取 uid 的顺时针后继。

    虚拟节点把每个物理节点的环上区间打散, 从而让负载更均匀、
    并在增删节点时只影响**相邻区间**的 key。
    """

    def __init__(self, nodes: list[str], vnodes: int = VNODES) -> None:
        self.vnodes = vnodes
        self.nodes = list(nodes)
        self.set_nodes(self.nodes)

    def set_nodes(self, nodes: list[str]) -> None:
        self.nodes = list(nodes)
        ring: list[tuple[int, str]] = []
        for node in self.nodes:
            for v in range(self.vnodes):
                ring.append((fnv1a32(f"{node}#{v}"), node))
        ring.sort()
        self.ring = [h for h, _ in ring]
        self.owners = [n for _, n in ring]

    def owner(self, uid: int) -> str:
        """取环上顺时针第一个虚拟节点所属的物理节点。"""
        h = fnv1a32(f"uid:{uid}")
        i = bisect.bisect_left(self.ring, h) % len(self.ring)
        return self.owners[i]

    def pick(self, uid: int) -> int:
        """返回物理节点下标, 便于与 ModuloPicker 直接比较。"""
        return self.nodes.index(self.owner(uid))


def remap_rate(n_before: int, n_after: int, uids: int = 20000) -> dict:
    """扩容/缩容前后, 有多少比例的 uid 换了实例。"""
    before = [f"inst{i}" for i in range(n_before)]
    after = [f"inst{i}" for i in range(n_after)]
    mp_a, mp_b = ModuloPicker(n_before), ModuloPicker(n_after)
    rp_a = RingPicker(before)
    rp_b = RingPicker(after)
    mod_moved = sum(1 for u in range(uids) if mp_a.pick(u) != mp_b.pick(u))
    ring_moved = sum(1 for u in range(uids) if rp_a.pick(u) != rp_b.pick(u))
    return {"uids": uids,
            "modulo": mod_moved / uids, "ring": ring_moved / uids,
            "ideal": 1.0 / max(n_before, n_after)}


def ring_load(nodes: int, uids: int = 20000) -> dict:
    names = [f"inst{i}" for i in range(nodes)]
    rp = RingPicker(names)
    mp = ModuloPicker(nodes)
    ring_cnt: dict[int, int] = defaultdict(int)
    mod_cnt: dict[int, int] = defaultdict(int)
    for u in range(uids):
        ring_cnt[rp.pick(u)] += 1
        mod_cnt[mp.pick(u)] += 1
    mean = uids / nodes
    ring_dev = max(abs(c - mean) for c in ring_cnt.values()) / mean
    mod_dev = max(abs(c - mean) for c in mod_cnt.values()) / mean
    return {"nodes": nodes, "mean": mean, "ring_max_dev": ring_dev,
            "modulo_max_dev": mod_dev}

