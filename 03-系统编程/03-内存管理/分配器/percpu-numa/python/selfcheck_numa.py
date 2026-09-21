#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""selfcheck_numa.py —— NUMA 内存策略 + per-CPU 分配的断言集。

断言原则：只断言**确定量**（落到哪个节点、errno、计数器值、arena 编号），
不断言依赖负载的"哪个策略更快"。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import (  # noqa: E402
    NumaMachine, Node, PolicyError, SharedCounter, PerCpuCounter, jem_arena_of,
    MPOL_DEFAULT, MPOL_BIND, MPOL_INTERLEAVE, MPOL_WEIGHTED_INTERLEAVE,
    MPOL_PREFERRED, MPOL_PREFERRED_MANY, MPOL_LOCAL,
    MPOL_MF_STRICT, MPOL_MF_MOVE, MPOL_MF_MOVE_ALL,
    MPOL_F_STATIC_NODES, MPOL_F_RELATIVE_NODES, MPOL_F_NUMA_BALANCING,
)

PASS = [0]


def ok(cond, msg):
    assert cond, "FAIL: " + msg
    PASS[0] += 1
    print("  ok  %s" % msg)


def eq(a, b, msg):
    ok(a == b, "%s  (got %r, want %r)" % (msg, a, b))


def fresh(nfree=1000, ncpu=8, nnodes=3):
    return NumaMachine(ncpu, [Node(i, nfree) for i in range(nnodes)], threads_per_core=2)


# ------------------------------------------------------------ 1. 默认策略
def t_default():
    print("[1] MPOL_DEFAULT / MPOL_LOCAL：落在触发分配的 CPU 所属节点")
    m = fresh()
    eq([m.cpu_node[c] for c in (0, 2, 5, 7)], [0, 1, 2, 2], "8 CPU / 3 节点的 cpu->node 映射")
    eq(m.allocate(5, 4, MPOL_DEFAULT, []), [2, 2, 2, 2], "cpu5 属于 node2 -> 4 页全在 node2")
    eq(m.allocate(0, 3, MPOL_LOCAL, []), [0, 0, 0], "MPOL_LOCAL 同样是本地分配")
    eq(m.allocate(5, 2, MPOL_PREFERRED, []), [2, 2],
       "MPOL_PREFERRED 且 nodemask 为空 -> 本地（mbind(2) 原文）")


# ------------------------------------------------------------ 2. BIND
def t_bind():
    print("[2] MPOL_BIND：严格限制 + 取有空闲的最近节点")
    m = NumaMachine(8, [Node(0, 100), Node(1, 2), Node(2, 100)], threads_per_core=2)
    eq(m.allocate(0, 3, MPOL_BIND, [1, 2]), [1, 1, 2],
       "cpu0(node0)：先取更近的 node1（2 页）再退到 node2")
    # 触发 CPU 在 node2 时，nodemask {0,1} 里离它更近的是**节点号更大**的 node1；
    # Linux 2.6.26 之前的"从最小节点号开始"会选 node0 —— 正是这条规则的对照组
    m2 = NumaMachine(8, [Node(0, 100), Node(1, 100), Node(2, 100)], threads_per_core=2)
    eq(m2.allocate(5, 2, MPOL_BIND, [0, 1]), [1, 1],
       "nodemask 内取「最近」而非「最小节点号」：node2 上触发时选 node1")
    m3 = NumaMachine(8, [Node(0, 0), Node(1, 0)], threads_per_core=2)
    try:
        m3.allocate(0, 1, MPOL_BIND, [0, 1])
        ok(False, "BIND 下无空闲内存应报错")
    except PolicyError as e:
        eq(e.errno, "ENOMEM", "MPOL_BIND 是严格策略：nodemask 内无空闲 -> ENOMEM")


# ------------------------------------------------------------ 3. interleave
def t_interleave():
    print("[3] MPOL_INTERLEAVE / WEIGHTED_INTERLEAVE")
    m = fresh()
    eq(m.allocate(0, 6, MPOL_INTERLEAVE, [0, 2]), [0, 2, 0, 2, 0, 2],
       "逐页轮转（为带宽而非延迟优化）")
    w = m.allocate(0, 20, MPOL_WEIGHTED_INTERLEAVE, [0, 1, 2], {0: 4, 1: 7, 2: 9})
    eq({n: w.count(n) for n in (0, 1, 2)}, {0: 4, 1: 7, 2: 9},
       "按 sysfs 权重 4:7:9 分配 20 页")
    eq(len(w), 20, "共 20 页")


# ------------------------------------------------------------ 4. preferred
def t_preferred():
    print("[4] MPOL_PREFERRED：首选节点耗尽后回退")
    m = NumaMachine(8, [Node(0, 100), Node(1, 2)], threads_per_core=2)
    eq(m.allocate(0, 4, MPOL_PREFERRED, [1]), [1, 1, 0, 0],
       "node1 只有 2 页 -> 后 2 页回退到本地 node0（BIND 此时会 ENOMEM）")
    m2 = fresh()
    eq(m2.allocate(0, 3, MPOL_PREFERRED, [2, 1]), [1, 1, 1],
       "nodmask 含多个节点时取**掩码中第一个**（编号最小）的作为首选（mbind(2) 原文）")
    eq(m2.allocate(0, 3, MPOL_PREFERRED_MANY, [2, 1]), [1, 1, 1],
       "PREFERRED_MANY 同样从候选集合里取首个有空闲的节点")


# ------------------------------------------------------------ 5. 参数校验
def t_validate():
    print("[5] mbind/set_mempolicy 的参数校验（man page 的 EINVAL/EPERM 清单）")
    V = NumaMachine.validate
    eq(V(MPOL_DEFAULT, [0]), "EINVAL", "MPOL_DEFAULT 必须配空 nodemask")
    eq(V(MPOL_DEFAULT, []), None, "MPOL_DEFAULT + 空集 -> 合法")
    eq(V(MPOL_BIND, []), "EINVAL", "MPOL_BIND + 空 nodemask -> EINVAL")
    eq(V(MPOL_INTERLEAVE, []), "EINVAL", "MPOL_INTERLEAVE + 空 nodemask -> EINVAL")
    eq(V(MPOL_INTERLEAVE, [0, 2]), None, "INTERLEAVE 配非空集合 -> 合法")
    eq(V(MPOL_BIND, [0], MPOL_F_NUMA_BALANCING), None,
       "MPOL_F_NUMA_BALANCING 只允许用在 MPOL_BIND 上")
    eq(V(MPOL_INTERLEAVE, [0], MPOL_F_NUMA_BALANCING), "EINVAL",
       "非 BIND 模式带 MPOL_F_NUMA_BALANCING -> EINVAL")
    eq(V(MPOL_BIND, [0], MPOL_F_STATIC_NODES | MPOL_F_RELATIVE_NODES), "EINVAL",
       "STATIC_NODES 与 RELATIVE_NODES 不可同时指定")
    eq(V(MPOL_BIND, [0], MPOL_MF_MOVE_ALL), "EPERM",
       "MPOL_MF_MOVE_ALL 需要 CAP_SYS_NICE")
    eq(V(MPOL_BIND, [0], MPOL_MF_MOVE_ALL, has_cap_sys_nice=True), None,
       "有 CAP_SYS_NICE 时 MPOL_MF_MOVE_ALL 合法")


# ------------------------------------------------------------ 6. mbind 迁移
def t_mbind():
    print("[6] mbind 的 flags：STRICT / MOVE / MOVE_ALL")
    m = fresh()
    st, _ = m.mbind([(0, True), (1, True)], [1], MPOL_MF_STRICT)
    eq(st, "EIO", "MPOL_MF_STRICT 且有页不在目标节点 -> EIO")
    st, _ = m.mbind([(1, True), (1, True)], [1], MPOL_MF_STRICT)
    eq(st, "ok", "全部已就位 -> 成功")

    _, out = m.mbind([(0, True), (0, False)], [1], MPOL_MF_MOVE)
    eq(out, [(1, True), (0, False)],
       "MPOL_MF_MOVE 只搬本进程独占的页；共享页原地不动")
    st, _ = m.mbind([(0, True), (0, False)], [1], MPOL_MF_MOVE_ALL)
    eq(st, "EPERM", "MPOL_MF_MOVE_ALL 无 CAP_SYS_NICE -> EPERM")
    _, out = m.mbind([(0, True), (0, False)], [1], MPOL_MF_MOVE_ALL, has_cap_sys_nice=True)
    eq(out, [(1, True), (1, False)], "有权限时连共享页一起搬")
    _, out = m.mbind([(0, True)], [1], 0)
    eq(out, [(0, True)], "不带 MOVE 类 flag 时只改策略，不迁移已有页")


# ------------------------------------------------------------ 7. per-CPU
def t_percpu_counter():
    print("[7] per-CPU 计数器 vs 共享计数器")
    s = SharedCounter()
    for _ in range(3):
        for c in range(4):
            s.add(c)
    eq(s.v, 12, "共享计数器：4 CPU × 3 轮 = 12")
    eq(s.invalidations, 11, "共享计数器产生 11 次 cacheline 失效（12 次写入 - 首次）")

    p = PerCpuCounter(4)
    for _ in range(3):
        for c in range(4):
            p.this_cpu_add(c)
    eq(p.v, [3, 3, 3, 3], "per-CPU：每个 CPU 各计 3")
    eq(p.total(), 12, "汇总值同样是 12")
    eq(p.invalidations, 0, "per-CPU 全程零失效")

    # __this_cpu_* 无保护：RMW 之间被抢占并迁移
    u = PerCpuCounter(2)
    u.v[0] = 5
    u.unsafe_this_cpu_add(0, 1, migrate_to=1)
    eq(u.v, [5, 6], "更新落到新 CPU 的副本：旧 CPU 仍是 5，新 CPU 变成 6")
    eq(u.total(), 11, "总数变成 11（应为 6）—— 这就是缺少抢占保护的后果")
    t = PerCpuCounter(2)
    t.v[0] = 5
    t.this_cpu_add(0, 1)
    eq((t.v[0], t.total()), (6, 6), "this_cpu_add 自带抢占保护，结果正确")


# ------------------------------------------------------------ 8. arena 绑定
def t_arena_binding():
    print("[8] jemalloc percpu_arena：按线程**当前**所在 CPU 绑定")
    eq(jem_arena_of(3, 2, "percpu", 8), 3, "percpu: cpu3 -> arena 3")
    eq(jem_arena_of(5, 2, "percpu", 8), 5, "线程从 cpu3 迁到 cpu5 -> arena 变 5")
    eq(jem_arena_of(3, 2, "phycpu", 8), 1, "phycpu: cpu3 -> arena 1")
    eq(jem_arena_of(2, 2, "phycpu", 8), 1, "phycpu: cpu2 与 cpu3 同核 -> 同一 arena")
    eq(jem_arena_of(2, 2, "phycpu", 8), jem_arena_of(3, 2, "phycpu", 8),
       "同核两个超线程共享一个 arena")
    eq(jem_arena_of(5, 2, "phycpu", 8), 2, "cpu5 -> arena 2")
    eq(jem_arena_of(5, 2, "disabled", 8), 5, "disabled 时按 4×CPU=32 取模 -> arena 5")


# ------------------------------------------------------------ 9. 延迟
def t_latency():
    print("[9] 本地 vs 远程访问的延迟代价")
    m = fresh()
    eq(m.latency(2, 2), 100, "本地访问 = 节点自身延迟 100 ns")
    eq(m.latency(2, 0), 100 + 2 * 60, "跨 2 跳 = 100 + 2×60 = 220 ns")
    local = sum(m.latency(2, n) for n in m.allocate(5, 4, MPOL_DEFAULT, []))
    inter = sum(m.latency(2, n) for n in m.allocate(5, 4, MPOL_INTERLEAVE, [0, 2]))
    eq(local, 400, "4 页全本地 = 400 ns")
    eq(inter, 640, "交错到 {0,2} 时 2 页远程 + 2 页本地 = 640 ns")
    ok(inter > local, "交错换带宽的代价是平均延迟上升（原文：interleaves for bandwidth, not latency）")


def main():
    t_default(); t_bind(); t_interleave(); t_preferred(); t_validate()
    t_mbind(); t_percpu_counter(); t_arena_binding(); t_latency()
    print("\nALL PASS: %d assertions" % PASS[0])


if __name__ == "__main__":
    main()
