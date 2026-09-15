#!/usr/bin/env python3
"""AOI 九宫格 (Area Of Interest, grid-based) 最小实现与自检.

算法要点(见 README「原理详解」):
  1. 地图切成等大正方形格子, 每个格子用一个列表存当前在格内的实体;
  2. 实体的视野 = 所在格子 + 周围一圈 = 3x3 九宫格(VIEW=1);
  3. 事件只有一个入口: 位置变化时对「旧九宫格集合 old_nb」与
     「新九宫格集合 new_nb」求差集:
         (old_nb - new_nb) -> Leave   (这些格子里的人从此看不见我)
         (new_nb - old_nb) -> Enter   (这些格子里的人开始看得见我)
         (old_nb & new_nb) -> Move    (一直在视野内, 只同步位置变化)
  4. 九宫格的「边界抖动」: 实体在视野边缘来回时 Enter/Leave 反复触发,
     工程上用滞回(hysteresis)区分进入阈值与离开阈值来抑制 ——
     即「进入绿色范围才开始同步, 离开黄色范围才算离开视野」。
     本文件用 HysteresisAOI 量化该收益。

坐标约定: 世界坐标直接用「格」为单位(1.0 = 一格), 因此
  cell = (int(x), int(y)), 让注意力留在 AOI 逻辑而不是坐标换算上。
"""

from __future__ import annotations

import random
from collections import defaultdict

GRID_W = 10          # 地图宽(格)
GRID_H = 8           # 地图高(格)
VIEW = 1             # 视野半径(格); 1 -> 3x3 九宫格
KINDS = ("Enter", "Leave", "Move")


# =====================================================================
# 一、纯九宫格
# =====================================================================
class GridAOI:
    """九宫格 AOI: 无滞回, 完全由格子归属决定视野。"""

    def __init__(self, w: int = GRID_W, h: int = GRID_H, view: int = VIEW) -> None:
        self.w, self.h, self.view = w, h, view
        self.cells: dict[int, list[int]] = defaultdict(list)
        self.pos: dict[int, tuple[float, float]] = {}
        self.cell: dict[int, int] = {}
        self.log: list[tuple[str, int, int]] = []

    # ---------- 坐标 / 格子 ----------
    def idx_of(self, cx: int, cy: int) -> int:
        return cy * self.w + cx

    def cell_of(self, eid: int) -> tuple[int, int]:
        x, y = self.pos[eid]
        return int(x), int(y)

    def nb(self, cx: int, cy: int, r: int | None = None) -> list[int]:
        """半径 r 的方形邻域格子索引; 出界部分被裁剪(地图边界即视野边界)。"""
        r = self.view if r is None else r
        out: list[int] = []
        for yy in range(cy - r, cy + r + 1):
            if 0 <= yy < self.h:
                for xx in range(cx - r, cx + r + 1):
                    if 0 <= xx < self.w:
                        out.append(self.idx_of(xx, yy))
        return out

    def _ents_in(self, idxs) -> set[int]:
        res: set[int] = set()
        for idx in idxs:
            res.update(self.cells.get(idx, ()))
        return res

    def _send(self, kind: str, src: int, targets) -> None:
        for t in targets:
            self.log.append((kind, src, t))

    # ---------- 生命周期 ----------
    def enter(self, eid: int, x: float, y: float) -> None:
        self.pos[eid] = (x, y)
        cx, cy = int(x), int(y)
        idx = self.idx_of(cx, cy)
        self.cell[eid] = idx
        self.cells[idx].append(eid)
        self._send("Enter", eid, self._neighbors(eid))   # 我出现 -> 通知九宫格内的人

    def leave(self, eid: int) -> None:
        self._send("Leave", eid, self._neighbors(eid))
        self.cells[self.cell[eid]].remove(eid)
        del self.pos[eid], self.cell[eid]

    def move(self, eid: int, x: float, y: float) -> dict[str, set[int]]:
        """返回本次移动的三类「格子集合」差集; 未跨格时三者皆空。"""
        ox, oy = self.cell_of(eid)
        nx, ny = int(x), int(y)
        if (nx, ny) == (ox, oy):
            self.pos[eid] = (x, y)
            self._send("Move", eid, self._neighbors(eid))   # 未跨格: 只同步位置
            return {"leave": set(), "enter": set(), "common": set()}

        old_nb, new_nb = set(self.nb(ox, oy)), set(self.nb(nx, ny))
        leave, enter, common = old_nb - new_nb, new_nb - old_nb, old_nb & new_nb

        # 先摘旧格再插新格, 否则自己会被当成邻居算进 common
        self.cells[self.cell[eid]].remove(eid)
        self.pos[eid] = (x, y)
        nidx = self.idx_of(nx, ny)
        self.cell[eid] = nidx
        self.cells[nidx].append(eid)

        for idx in leave:
            self._send("Leave", eid, self.cells.get(idx, ()))
        # enter 集合可能包含自己刚进入的那一格(跨 >=2 格时), common 一定包含 -> 都要剔除自己
        for idx in enter:
            self._send("Enter", eid, [e for e in self.cells.get(idx, ()) if e != eid])
        for idx in common:
            self._send("Move", eid, [e for e in self.cells.get(idx, ()) if e != eid])
        return {"leave": leave, "enter": enter, "common": common}

    def _neighbors(self, eid: int) -> set[int]:
        cx, cy = self.cell_of(eid)
        return self._ents_in(self.nb(cx, cy)) - {eid}


def counts(log) -> dict[str, int]:
    c = {k: 0 for k in KINDS}
    for kind, _, _ in log:
        c[kind] += 1
    return c


# =====================================================================
# 二、自检 1: 差集规则 —— 格子集合基数
# =====================================================================
def check_diff_rule() -> None:
    aoi = GridAOI(w=20, h=20)
    eid = 5 * 20 + 5                           # 位于 (5.5, 5.5) 的唯一实体
    aoi.enter(eid, 5.5, 5.5)
    aoi.log.clear()

    r = aoi.move(eid, 6.5, 5.5)                # ① 水平跨 1 格
    assert (len(r["leave"]), len(r["enter"]), len(r["common"])) == (3, 3, 6), r
    assert aoi.log == [], aoi.log              # 场上没有别人 -> 一条消息都不该发
    print("[自检 1] 水平跨 1 格: 格子集 Leave=3 Enter=3 Common=6  (期望 3/3/6)  OK")

    aoi.log.clear()
    r = aoi.move(eid, 7.5, 6.5)                # ② 再对角跨 1 格
    assert (len(r["leave"]), len(r["enter"]), len(r["common"])) == (5, 5, 4), r
    assert aoi.log == [], aoi.log
    print("[自检 1] 对角跨 1 格: 格子集 Leave=5 Enter=5 Common=4  (期望 5/5/4)  OK")


# =====================================================================
# 三、自检 2: 三类格子各自把消息发给谁
# =====================================================================
def check_message_routing() -> None:
    aoi = GridAOI(w=20, h=20)
    eid = 5 * 20 + 5                           # (5.5, 5.5) -> 跨格到 (6,5)
    aoi.enter(eid, 5.5, 5.5)
    aoi.enter(1000, 4.5, 5.5)                  # 落在 leave 组 (4,5)
    aoi.enter(1001, 7.5, 5.5)                  # 落在 enter 组 (7,5)
    aoi.enter(1002, 6.5, 5.5)                  # 落在 common 组 (6,5), 与 eid 同格
    aoi.log.clear()

    aoi.move(eid, 6.5, 5.5)
    assert aoi.log == [("Leave", eid, 1000),
                       ("Enter", eid, 1001),
                       ("Move", eid, 1002)], aoi.log
    print("[自检 2] 三类格子各自路由到正确对象: Leave->(4,5) / Enter->(7,5) / "
          "Move->(6,5), 且不含自己  OK")

    # 同格多实体: 一条变化必须发给格内所有人
    aoi2 = GridAOI(w=6, h=6)
    for i in range(5):
        aoi2.enter(i, 2.1 + i * 0.1, 2.1)      # 5 个实体全在格子 (2,2)
    aoi2.log.clear()
    aoi2.move(0, 2.15, 2.1)                    # 同格内微动
    c = counts(aoi2.log)
    assert c["Move"] == 4 and c["Enter"] == 0 and c["Leave"] == 0, c
    print(f"[自检 3] 同格 5 实体微动: Move={c['Move']} (期望 4 = 格内其他人)  OK")


# =====================================================================
# 四、场景 3: 广播量对比(九宫格 vs 朴素全场景广播)
# =====================================================================
def measure_broadcast(n_ent: int = 300, steps: int = 400, seed: int = 7) -> None:
    rnd = random.Random(seed)
    aoi = GridAOI(w=40, h=30)
    pos: dict[int, tuple[float, float]] = {}
    for i in range(n_ent):
        pos[i] = (rnd.uniform(0, 39.9), rnd.uniform(0, 29.9))
        aoi.enter(i, *pos[i])
    aoi.log.clear()

    naive = 0
    for _ in range(steps):
        for i in range(n_ent):
            x = min(39.9, max(0.0, pos[i][0] + rnd.uniform(-0.6, 0.6)))
            y = min(29.9, max(0.0, pos[i][1] + rnd.uniform(-0.6, 0.6)))
            pos[i] = (x, y)
            aoi.move(i, x, y)
            naive += n_ent - 1
    c = counts(aoi.log)
    total = sum(c.values())
    per_step = total / steps / n_ent
    print(f"[广播量] {n_ent} 实体 x {steps} 步 @ 40x30 格: 朴素全广播 {naive} 条, "
          f"AOI {total} 条 (E={c['Enter']} L={c['Leave']} M={c['Move']}), "
          f"降幅 {1 - total / naive:.2%}")
    print(f"         平均每实体每步 {per_step:.3f} 条 (朴素 = {(naive / steps / n_ent):.3f})")
    assert total < naive * 0.2, (total, naive)
    assert c["Move"] > c["Enter"] > 0


# =====================================================================
# 五、场景 4: 边界抖动 —— 滞回对 Enter/Leave 抖动量的影响
# =====================================================================
class HysteresisAOI:
    """带滞回的视野判定: d <= IN 才进入视野, d > OUT 才离开视野。

    真实工程里用九宫格/链表把候选集缩小到 O(邻居), 这里为了把语义讲清楚
    直接两两比较(实体数很小)。注意「进入阈值 < 离开阈值」是滞回的本质。
    """

    def __init__(self, enter_dist: float, leave_dist: float) -> None:
        self.enter_dist, self.leave_dist = enter_dist, leave_dist
        self.pos: dict[int, tuple[float, float]] = {}
        self.watchers: dict[int, set[int]] = defaultdict(set)   # 谁看得见我
        self.log: list[tuple[str, int, int]] = []

    def add(self, eid: int, x: float, y: float) -> None:
        self.pos[eid] = (x, y)
        self._refresh()

    def move(self, eid: int, x: float, y: float) -> None:
        self.pos[eid] = (x, y)
        self._refresh()

    def _refresh(self) -> None:
        ids = list(self.pos)
        for a in ids:
            ax, ay = self.pos[a]
            for b in ids:
                if a == b:
                    continue
                bx, by = self.pos[b]
                d = max(abs(ax - bx), abs(ay - by))
                if d <= self.enter_dist:
                    if a not in self.watchers[b]:
                        self.watchers[b].add(a)
                        self.log.append(("Enter", b, a))
                    else:
                        self.log.append(("Move", b, a))
                elif d > self.leave_dist and a in self.watchers[b]:
                    self.watchers[b].discard(a)
                    self.log.append(("Leave", b, a))


def measure_boundary_churn(osc: int = 400) -> None:
    """主角在 B 的视野边缘(距离 1.0 格)来回抖动, 看 Enter/Leave 抖动次数。"""
    out = {}
    for gap in (0, 1):
        aoi = HysteresisAOI(enter_dist=1.0, leave_dist=1.0 + gap)
        aoi.add(1, 5.5, 5.5)                    # 固定背景实体
        aoi.add(2, 4.55, 5.5)                   # 主角: 距 B = 0.95 (在视野内)
        aoi.log.clear()
        for i in range(osc):
            x = 4.45 if i % 2 == 0 else 4.55     # 抖动使其切过 d=1.0 边界
            aoi.move(2, x, 5.5)
        c = counts(aoi.log)
        out[gap] = c
        print(f"[边界抖动] gap={gap} (进 <= 1.0 格 / 出 > {1.0 + gap:.1f} 格): "
              f"抖动 {osc} 次 -> Enter={c['Enter']} Leave={c['Leave']} Move={c['Move']}")
    a, b = out[0], out[1]
    assert a["Enter"] + a["Leave"] > 0 and b["Enter"] + b["Leave"] == 0, out
    print(f"[边界抖动] 滞回把 Enter+Leave 从 {a['Enter'] + a['Leave']} 次降到 "
          f"{b['Enter'] + b['Leave']} 次  OK")


if __name__ == "__main__":
    check_diff_rule()
    check_message_routing()
    measure_broadcast()
    measure_boundary_churn()
    print("\n全部自检通过。")
