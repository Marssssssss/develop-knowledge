#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
per-CPU 分配 与 NUMA 感知分配 的原理级模型。

依据（实际读过的原文）：
* Linux man-pages `mbind(2)`：
  - MPOL_DEFAULT 落到线程策略，线程策略也是 DEFAULT 时用**系统级默认**，
    即「分配在触发分配的那个 CPU 所属的节点上」
  - MPOL_BIND 严格限制在 nodemask 内；多节点时取**有足够空闲内存的最近节点**
    （Linux 2.6.26 之前是"从最小节点号开始"）
  - MPOL_INTERLEAVE 为带宽而非延迟优化，区域至少 1 MB 才有效；
    **单个页的访问仍受限于单个节点的带宽**
  - MPOL_WEIGHTED_INTERLEAVE（6.9+）按 /sys/.../weighted_interleave 的权重分配，例 4:7:9
  - MPOL_PREFERRED 首选节点，内存不足时回退；空 nodemask = 触发分配的 CPU 所在节点
  - MPOL_LOCAL（3.8+）"本地分配"
  - flags：MPOL_MF_STRICT（有页不在目标节点 -> EIO）、MPOL_MF_MOVE（只搬本进程独占的页）、
    MPOL_MF_MOVE_ALL（需 CAP_SYS_NICE）
  - EINVAL：mode 非 MPOL_BIND 时带 MPOL_F_NUMA_BALANCING；MPOL_DEFAULT 且 nodemask 非空；
    MPOL_BIND/MPOL_INTERLEAVE 且 nodemask 为空；同时指定 STATIC_NODES 与 RELATIVE_NODES
* Linux `core-api/this_cpu_ops`：
  - `this_cpu_read/write/add` **自带抢占/中断保护**；`__this_cpu_*` 要求调用方已关抢占
  - per-cpu 变量用「相对 per-cpu 区的偏移」寻址；不用 per-cpu 会带来 cacheline bouncing
* jemalloc(3)：`opt.percpu_arena`（disabled/percpu/phycpu）按线程**当前所在 CPU** 绑定 arena
"""

# ---------------------------------------------------------------- 策略常量
MPOL_DEFAULT = 0
MPOL_BIND = 1
MPOL_INTERLEAVE = 2
MPOL_WEIGHTED_INTERLEAVE = 3
MPOL_PREFERRED = 4
MPOL_PREFERRED_MANY = 5
MPOL_LOCAL = 6

MPOL_MF_STRICT = 1 << 0
MPOL_MF_MOVE = 1 << 1
MPOL_MF_MOVE_ALL = 1 << 2
MPOL_F_STATIC_NODES = 1 << 3
MPOL_F_RELATIVE_NODES = 1 << 4
MPOL_F_NUMA_BALANCING = 1 << 5


class PolicyError(Exception):
    def __init__(self, errno, msg):
        Exception.__init__(self, "%s: %s" % (errno, msg))
        self.errno = errno


class Node:
    def __init__(self, nid, free_pages, latency=100):
        self.nid = nid
        self.free = free_pages
        self.latency = latency          # 本地访问延迟（ns）

    def take(self, n=1):
        if self.free < n:
            return False
        self.free -= n
        return True


class NumaMachine:
    """一台 NUMA 机器：CPU 分属不同节点，节点间访问有额外跳数。"""

    def __init__(self, ncpu, nodes, threads_per_core=1, hop_latency=60):
        self.ncpu = ncpu
        self.nodes = nodes
        self.threads_per_core = threads_per_core
        self.hop_latency = hop_latency
        per = max(1, ncpu // len(nodes))
        self.cpu_node = {c: min(len(nodes) - 1, c // per) for c in range(ncpu)}
        # 节点距离：|i-j| 跳
        self.dist = {(a.nid, b.nid): abs(a.nid - b.nid) for a in nodes for b in nodes}

    def node(self, nid):
        for n in self.nodes:
            if n.nid == nid:
                return n
        raise KeyError(nid)

    def latency(self, from_nid, to_nid):
        return self.node(to_nid).latency + self.dist[(from_nid, to_nid)] * self.hop_latency

    # ------------------------------------------------------------ 分配
    def allocate(self, cpu, npages, mode, nodemask, weights=None):
        """返回分配到的节点号列表；严格策略无法满足时抛 PolicyError(ENOMEM)。"""
        local = self.cpu_node[cpu]
        mask = sorted(nodemask)
        out = []

        if mode in (MPOL_DEFAULT, MPOL_LOCAL):
            # 系统级默认 / 本地分配：落在触发分配的 CPU 所属节点
            return self._take_many([local] * npages)

        if mode == MPOL_BIND:
            if not mask:
                raise PolicyError("EINVAL", "MPOL_BIND 需要非空 nodemask")
            for _ in range(npages):
                cands = [n for n in mask if self.node(n).free >= 1]
                if not cands:
                    raise PolicyError("ENOMEM", "MPOL_BIND 严格策略：nodemask 内无空闲内存")
                # 有足够空闲内存的**最近**节点；立刻扣减，后续页才看得到新的空闲量
                best = min(cands, key=lambda n: self.dist[(local, n)])
                self.node(best).take(1)
                out.append(best)
            return out

        if mode == MPOL_INTERLEAVE:
            if not mask:
                raise PolicyError("EINVAL", "MPOL_INTERLEAVE 需要非空 nodemask")
            for i in range(npages):
                out.append(mask[i % len(mask)])
            return self._take_many(out)

        if mode == MPOL_WEIGHTED_INTERLEAVE:
            # 权重来自 /sys/kernel/mm/mempolicy/weighted_interleave/nodeN
            w = weights or {}
            seq = []
            for n in mask:
                seq.extend([n] * int(w.get(n, 1)))
            if not seq:
                raise PolicyError("EINVAL", "weighted_interleave 的 nodemask 为空")
            for i in range(npages):
                out.append(seq[i % len(seq)])
            return self._take_many(out)

        if mode in (MPOL_PREFERRED, MPOL_PREFERRED_MANY):
            if not mask:
                return self._take_many([local] * npages)     # 空集 = 本地
            for _ in range(npages):
                pref = [n for n in mask if self.node(n).free >= 1]
                pick = pref[0] if pref else local
                self.node(pick).take(1)
                out.append(pick)
            return out

        raise PolicyError("EINVAL", "未知 mode %r" % mode)

    def _take_many(self, nids):
        for n in nids:
            self.node(n).take(1)
        return list(nids)

    # ------------------------------------------------------------ 参数校验
    @staticmethod
    def validate(mode, nodemask, flags=0, has_cap_sys_nice=False):
        """返回 errno 字符串或 None。"""
        if flags & MPOL_F_STATIC_NODES and flags & MPOL_F_RELATIVE_NODES:
            return "EINVAL"
        if flags & MPOL_F_NUMA_BALANCING and mode != MPOL_BIND:
            return "EINVAL"
        if mode == MPOL_DEFAULT and nodemask:
            return "EINVAL"
        if mode in (MPOL_BIND, MPOL_INTERLEAVE) and not nodemask:
            return "EINVAL"
        if flags & MPOL_MF_MOVE_ALL and not has_cap_sys_nice:
            return "EPERM"
        return None

    # ------------------------------------------------------------ mbind
    def mbind(self, pages, nodemask, flags=0, has_cap_sys_nice=False):
        """pages: [(node, private_bool)] —— 返回 ('ok'|errno, 迁移后的 pages)。"""
        if flags & MPOL_MF_MOVE_ALL and not has_cap_sys_nice:
            return "EPERM", pages
        mask = sorted(nodemask)
        if flags & MPOL_MF_STRICT:
            bad = [p for p in pages if p[0] not in mask]
            if bad:
                return "EIO", pages
        out = []
        for n, priv in pages:
            if n in mask:
                out.append((n, priv))
                continue
            if flags & MPOL_MF_MOVE_ALL:
                out.append((mask[0], priv))            # 连共享页一起搬
            elif flags & MPOL_MF_MOVE and priv:
                out.append((mask[0], priv))            # 只搬本进程独占的页
            else:
                out.append((n, priv))                  # 原地不动
        return "ok", out


# ---------------------------------------------------------------- per-CPU 计数器
class SharedCounter:
    """不用 per-cpu：所有 CPU 抢同一条 cacheline。"""

    def __init__(self):
        self.v = 0
        self.invalidations = 0
        self.owner = None

    def add(self, cpu, delta=1):
        if self.owner is not None and self.owner != cpu:
            self.invalidations += 1
        self.owner = cpu
        self.v += delta


class PerCpuCounter:
    """per-cpu 计数器：每个 CPU 一份，无需同步。"""

    def __init__(self, ncpu):
        self.v = [0] * ncpu
        self.invalidations = 0

    def this_cpu_add(self, cpu, delta=1):
        """this_cpu_add：自带抢占保护，RMW 之间不会被迁移。"""
        self.v[cpu] += delta

    def unsafe_this_cpu_add(self, cpu, delta=1, migrate_to=None):
        """__this_cpu_add：无保护；RMW 之间若被抢占并迁移，更新会落到错误的 CPU。"""
        tmp = self.v[cpu]                       # read（在旧 CPU 上）
        if migrate_to is None:
            self.v[cpu] = tmp + delta
            return
        self.v[migrate_to] = tmp + delta        # write 落到新 CPU 的副本上

    def total(self):
        return sum(self.v)


def jem_arena_of(cpu, threads_per_core, percpu_arena, ncpu, narenas_base=None):
    """jemalloc：按线程**当前所在 CPU** 选 arena。"""
    if percpu_arena == "percpu":
        return cpu
    if percpu_arena == "phycpu":
        return cpu // threads_per_core
    base = narenas_base if narenas_base else max(1, 4 * ncpu)
    return cpu % base


if __name__ == "__main__":
    m = NumaMachine(8, [Node(0, 1000), Node(1, 1000), Node(2, 1000)], threads_per_core=2)
    print("cpu->node:", m.cpu_node)
    print("DEFAULT on cpu5:", m.allocate(5, 4, MPOL_DEFAULT, []))
    print("INTERLEAVE {0,2}:", m.allocate(0, 6, MPOL_INTERLEAVE, [0, 2]))
    w = m.allocate(0, 20, MPOL_WEIGHTED_INTERLEAVE, [0, 1, 2], {0: 4, 1: 7, 2: 9})
    print("WEIGHTED 4:7:9 分布:", {n: w.count(n) for n in (0, 1, 2)})
