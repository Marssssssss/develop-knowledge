"""nanite.py — Nanite 虚拟化几何关键机制的最小模型与自检.

事实来源（本机 curl 落地后实读）：
  * Brian Karis, «Nanite A Deep Dive», SIGGRAPH 2021 Advances in Real-Time Rendering
    (advances.realtimerendering.com/s2021/Karis_Nanite_SIGGRAPH_Advances_2021_final.pdf，155 页 PDF 逐页抽文本)
  * Epic — Nanite virtualized geometry (dev.epicgames.com/documentation/en-us/unreal-engine/nanite-virtualized-geometry-in-unreal-engine)

模型覆盖：128 三角形 cluster、group→merge→simplify 50%→split 的构建循环、
DAG 而非树、组内共享 unioned error/bounds、误差单调强制、
"parent 太粗 && 自己够细" 的可并行 LOD 选择、ParentError 剪枝、
visibility buffer（depth:InstanceID:TriangleID）、按需 streaming、cluster 级剔除。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

CLUSTER_TRIS = 128        # 原文："In our case a cluster is 128 triangles"
GROUP_SIZE = 4            # 一次把 4 个 cluster 分到一组
PIXEL_THRESHOLD = 1.0     # 原文：只画误差 < 1 像素的 cluster，TAA 把亚像素差异抹平
PROJ = 1000.0             # 演示用的投影系数（像素/单位距离）

_ASSERTIONS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _ASSERTIONS
    if not cond:
        raise AssertionError(f"{label} 失败: {detail}")
    _ASSERTIONS += 1
    print(f"ok {_ASSERTIONS:>2} {label}" + (f"  [{detail}]" if detail else ""))


@dataclass
class Cluster:
    cid: int
    level: int
    tris: int = CLUSTER_TRIS
    error: float = 0.0            # 简化器给出的物体空间误差（标量）
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius: float = 0.0
    children: List["Cluster"] = field(default_factory=list)
    parents: List["Cluster"] = field(default_factory=list)


def raw_error(level: int, idx: int, gen: int) -> float:
    """简化器给出的原始误差：量级随层数翻倍（每级简化 50%），并带不确定抖动
    （刻意让它不严格单调，用来验证"构建期强制单调"这一步是必要的）。"""
    jitter = 1.0 + ((level * 37 + idx * 17 + gen * 11) % 7) * 0.3
    return 0.001 * (2.0 ** level) * jitter


def sphere_union(cs: List[Cluster]) -> Tuple[Tuple[float, float, float], float]:
    """包围球取并：中心取包围盒中心，半径取到最远成员表面的距离。"""
    xs = [c.center[0] for c in cs]
    ys = [c.center[1] for c in cs]
    zs = [c.center[2] for c in cs]
    cx, cy, cz = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2
    r = 0.0
    for c in cs:
        d = ((c.center[0] - cx) ** 2 + (c.center[1] - cy) ** 2 + (c.center[2] - cz) ** 2) ** 0.5
        r = max(r, d + c.radius)
    return (cx, cy, cz), r


def build_dag(leaf_count: int = 64) -> Tuple[List[List[Cluster]], List[Cluster]]:
    """build 循环：cluster 原始三角形 → while NumClusters > 1: 分组 → 合并 → 简化 50% → 切成 128 三角的 cluster。"""
    levels: List[List[Cluster]] = []
    leaves = [Cluster(cid=i, level=0, tris=CLUSTER_TRIS, error=0.0,
                      center=(float(i % 8), float(i // 8), 0.0), radius=0.5)
              for i in range(leaf_count)]
    levels.append(leaves)
    while len(levels[-1]) > 1:
        cur = levels[-1]
        level = len(levels)
        nxt: List[Cluster] = []
        for gi in range(0, len(cur), GROUP_SIZE):
            group = cur[gi:gi + GROUP_SIZE]
            center, radius = sphere_union(group)
            union_err = max(c.error for c in group)          # 组内共享 unioned error
            for c in group:                                  # 同组必须做同样的 LOD 决策
                c.center, c.radius, c.error = center, radius, union_err
            merged = sum(c.tris for c in group)
            simplified = merged // 2                          # 简化到 50%
            new_count = simplified // CLUSTER_TRIS
            for j in range(new_count):
                raw = raw_error(level, len(nxt), gi)
                # 强制单调：parent 的 error 与 bounds 都必须 >= 孩子
                err = max(raw, union_err)
                pc = Cluster(cid=len(nxt), level=level, tris=CLUSTER_TRIS, error=err,
                             center=center, radius=max(radius, radius))
                pc.children = list(group)                     # 组内所有 cluster 都是它的孩子
                for c in group:
                    c.parents.append(pc)
                nxt.append(pc)
        levels.append(nxt)
    return levels, leaves


def view_error(c: Cluster, camera: Tuple[float, float, float] = (0.0, 0.0, 10.0)) -> float:
    """把物体空间误差投影到屏幕：在包围球内取投影误差最大的那一点。"""
    dx, dy, dz = (c.center[0] - camera[0], c.center[1] - camera[1], c.center[2] - camera[2])
    dist = (dx * dx + dy * dy + dz * dz) ** 0.5 - c.radius
    dist = max(dist, 0.05)
    return c.error * PROJ / dist


def parent_view_error(c: Cluster, camera=(0.0, 0.0, 10.0)) -> float:
    if not c.parents:
        return float("inf")          # root 的 parent 误差视为无穷大（永远"太粗"）
    return max(view_error(p, camera) for p in c.parents)


def select(all_clusters: List[Cluster], threshold: float = PIXEL_THRESHOLD,
           camera=(0.0, 0.0, 10.0)) -> List[Cluster]:
    """渲染条件：parent 误差 > 阈值 && 自身误差 <= 阈值。"""
    out = []
    for c in all_clusters:
        if parent_view_error(c, camera) > threshold and view_error(c, camera) <= threshold:
            out.append(c)
    return out


def select_with_cull(roots: List[Cluster], threshold: float = PIXEL_THRESHOLD,
                     camera=(0.0, 0.0, 10.0)) -> Tuple[List[Cluster], int]:
    """按 ParentError 建树剪枝：某节点 parent 误差已够小 ⇒ 整棵子树不可能被选中。"""
    selected: List[Cluster] = []
    evaluated = 0
    seen: set = set()

    def walk(c: Cluster) -> None:
        nonlocal evaluated
        if id(c) in seen:            # DAG：同一 cluster 会被多个 parent 引到
            return
        seen.add(id(c))
        evaluated += 1
        if parent_view_error(c, camera) > threshold and view_error(c, camera) <= threshold:
            selected.append(c)
        if not c.children:
            return
        # 剪枝判据用 ParentError 的最大值：子树里所有成员的 parent 误差都 <= 它
        bound = max(parent_view_error(ch, camera) for ch in c.children)
        if bound <= threshold:       # 整棵子树都不可能出现「parent 太粗」
            return
        for ch in c.children:
            walk(ch)

    for r in roots:
        walk(r)
    return selected, evaluated


def paths_to_leaves(roots: List[Cluster]) -> List[List[Cluster]]:
    out: List[List[Cluster]] = []

    def walk(c: Cluster, path: List[Cluster]) -> None:
        p = path + [c]
        if not c.children:
            out.append(p)
            return
        for ch in c.children:
            walk(ch, p)

    for r in roots:
        walk(r, [])
    return out


def frustum_cull(clusters: List[Cluster], camera=(0.0, 0.0, 10.0), half_width: float = 6.0):
    """演示：视锥（横向 ±half_width）+ 背面 + 简化 HZB 遮挡三类剔除。"""
    kept = []
    for c in clusters:
        if c.center[0] < -half_width or c.center[0] > half_width:   # 视锥外
            continue
        if c.center[2] > camera[2] - 0.5:                            # 相机背后
            continue
        kept.append(c)
    occluded = [c for c in kept if c.level <= 1 and c.center[1] > 3.0]  # 被 HZB 判为遮挡
    return [c for c in kept if c not in occluded], len(occluded)


class Streamer:
    """按需 streaming：任何 cut 都能被标成叶，更深的层不驻留；久未绘制则驱逐。"""

    def __init__(self) -> None:
        self.resident: set = set()
        self.last_draw: Dict[int, int] = {}
        self.frame = 0

    def load(self, cs: List[Cluster]) -> None:
        self.resident.update(id(c) for c in cs)

    def tick(self, drawn: List[Cluster], keep_frames: int = 2) -> List[int]:
        self.frame += 1
        for c in drawn:
            self.last_draw[id(c)] = self.frame
        evicted = []
        for cid in list(self.resident):
            if self.frame - self.last_draw.get(cid, 0) > keep_frames:
                self.resident.discard(cid)
                evicted.append(cid)
        return evicted

    def missing(self, cs: List[Cluster]) -> List[Cluster]:
        return [c for c in cs if id(c) not in self.resident]


def shade_cost(pixels: int, overdraw: List[int], mode: str) -> int:
    """材质求值次数：forward 按片段（含 overdraw），visibility buffer 按像素。"""
    if mode == "visibility_buffer":
        return pixels
    return sum(overdraw)

