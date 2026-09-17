#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go 内存模型：happens-before 关系的可执行模型。

依据 https://go.dev/ref/mem（2022-06-06 版）原文：
  * 内存操作分 read-like（read / atomic read / mutex lock / channel receive）与
    write-like（write / atomic write / mutex unlock / channel send / channel close），
    另有一类 **synchronizing operation**（原子访问、mutex 操作、channel 操作）。
  * `synchronized before` 由映射 W 导出：若同步读 r 观察到同步写 w，则 w synchronized before r。
  * `happens before` = `sequenced before` ∪ `synchronized before` 的**传递闭包**。
  * 数据竞争 = 同一内存位置上两个操作、至少一个非同步、且在 happens-before 下不可比。
  * **DRF-SC**：无数据竞争的程序，其行为等价于各 goroutine 顺序执行的某个交错。
被 python/main.py 以 `from memory_model import *` 使用。
"""

READ_LIKE = ("read", "atomic_read", "lock", "recv")
WRITE_LIKE = ("write", "atomic_write", "unlock", "send", "close")


class Op:
    __slots__ = ("gid", "label", "kind", "loc", "sync")

    def __init__(self, gid, label, kind, loc=None):
        self.gid = gid          # 所在 goroutine
        self.label = label      # 人类可读名字
        self.kind = kind        # read / write / send / recv / close / lock / unlock ...
        self.loc = loc          # 访问的内存位置（None 表示不访问内存）
        self.sync = kind in READ_LIKE + WRITE_LIKE and kind not in ("read", "write")

    @property
    def read_like(self):
        return self.kind in READ_LIKE

    @property
    def write_like(self):
        return self.kind in WRITE_LIKE

    def __repr__(self):
        return self.label


class Execution:
    """一次程序执行：若干 goroutine 的操作序列 + 同步边。"""

    def __init__(self):
        self.ops = []           # 按加入顺序，即 sequenced before 的近似（同 goroutine 即成立）
        self.sync_edges = []    # (a, b)：a synchronized before b
        self.notes = []

    def op(self, gid, label, kind, loc=None):
        o = Op(gid, label, kind, loc)
        self.ops.append(o)
        return o

    def sync(self, a, b, why=""):
        self.sync_edges.append((a, b))
        if why:
            self.notes.append("%s  synchronized before  %s   （%s）" % (a.label, b.label, why))
        return self

    # -------------------------------------------------- 关系计算
    def sequenced_before(self, a, b):
        """同一 goroutine 内、按程序顺序先出现。"""
        return a.gid == b.gid and a is not b and self.ops.index(a) < self.ops.index(b)

    def happens_before(self):
        """sequenced ∪ synchronized 的传递闭包（Floyd–Warshall 于操作集合上）。"""
        n = len(self.ops)
        idx = {id(o): i for i, o in enumerate(self.ops)}
        hb = [[False] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i != j and self.sequenced_before(self.ops[i], self.ops[j]):
                    hb[i][j] = True
        for a, b in self.sync_edges:
            hb[idx[id(a)]][idx[id(b)]] = True
        for k in range(n):
            for i in range(n):
                if hb[i][k]:
                    row_k = hb[k]
                    row_i = hb[i]
                    for j in range(n):
                        if row_k[j]:
                            row_i[j] = True
        return hb, idx

    def ordered(self, a, b):
        hb, idx = self.happens_before()
        i, j = idx[id(a)], idx[id(b)]
        return hb[i][j] or hb[j][i]

    def is_race(self, a, b):
        """同一位置、至少一个非同步、且 HB 不可比 → 数据竞争。"""
        if a.loc is None or a.loc != b.loc:
            return False
        if not (a.read_like or a.write_like) or not (b.read_like or b.write_like):
            return False
        if a.read_like and b.read_like:
            return False                     # 读-读不构成竞争
        if a.sync and b.sync:
            return False                     # 两个同步操作之间不算竞争
        return not self.ordered(a, b)

    def races(self):
        out = []
        for i, a in enumerate(self.ops):
            for b in self.ops[i + 1:]:
                if self.is_race(a, b):
                    out.append((a.label, b.label))
        return out

    def visible_writes(self, rd):
        """按 Requirement 3：可见写 = 发生在 r 之前、且没有被别的发生在 r 之前的写覆盖。"""
        hb, idx = self.happens_before()
        i = idx[id(rd)]
        cands = [self.ops[k] for k in range(len(self.ops))
                 if hb[k][i] and self.ops[k].write_like and self.ops[k].loc == rd.loc]
        vis = []
        for w in cands:
            j = idx[id(w)]
            if not any(hb[j][idx[id(w2)]] for w2 in cands if w2 is not w):
                vis.append(w)
        return vis
