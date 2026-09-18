#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tupni（CCS 2008）最小复现：chunk → 字段 → 记录序列 → 记录类型 → 约束。

来源：W. Cui, M. Peinado, K. Chen, H. J. Wang, L. Irun-Briz, *Tupni: Automatic
Reverse Engineering of Input Formats*, ACM CCS 2008。

复现内容：
  §3.3  chunk 识别（操作数内连续被污染字节的最长序列；忽略 mov；权重 = 访问指令数）
  §3.3.1 纠错：加权 Maximum k-Set Packing 的贪心解；空隙补 virtual field
  §3.4.1 循环识别（CFG 上的单入口环；按入口点切分迭代）
  §3.4.2 迭代相关指令 Ii（末次迭代常只是终止检查 → In 为空则按 n-1 处理）
  §3.4.3 Figure 4 FindRecordBoundaries
  §3.5   记录类型：Qi → Q'i（子循环折叠成虚指令）→ Q'i == Q'j 判同类
  §3.6   约束：符号谓词（单值 / 函数式）、跨报文依赖、长度字段
"""

# ------------------------------------------------------- §3.3 chunk 与权重
def chunk_from_offsets(offs):
    """操作数内**连续**被污染字节的最长序列 → 一个 chunk (lo, hi)。"""
    offs = sorted(offs)
    best = cur = [offs[0], offs[0] + 1]
    for o in offs[1:]:
        if o == cur[1]:
            cur[1] = o + 1
        else:
            if cur[1] - cur[0] > best[1] - best[0]:
                best = cur
            cur = [o, o + 1]
    if cur[1] - cur[0] > best[1] - best[0]:
        best = cur
    return tuple(best)


def build_chunks(trace):
    """trace: [(eip, [offsets])]，**只含非 mov 指令**（mov 不贡献字段信息）。"""
    w = {}
    for eip, offs in trace:
        for c in _all_chunks(offs):
            w[c] = w.get(c, 0) + 1
    return w


def _all_chunks(offs):
    """一个操作数里可能有多个不连续段 → 每段各成一个 chunk。"""
    offs = sorted(offs)
    out, cur = [], [offs[0], offs[0] + 1]
    for o in offs[1:]:
        if o == cur[1]:
            cur[1] = o + 1
        else:
            out.append(tuple(cur))
            cur = [o, o + 1]
    out.append(tuple(cur))
    return out


# -------------------------------------- §3.3.1 加权 Maximum k-Set Packing
def greedy_packing(weights):
    """贪心：按权重降序取互不重叠的 chunk（Chandra & Halldórsson 的简化版）。

    论文原文：找「互不重叠且权重之和最大」的一致子集 —— 这是加权 Maximum
    k-Set Packing，最坏情况甚至不可近似；真实轨迹里重叠很少，贪心就够。
    """
    chosen = []
    for c, _ in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0][0])):
        if all(c[1] <= x[0] or x[1] <= c[0] for x in chosen):
            chosen.append(c)
    return sorted(chosen)


def virtual_fields(fields, msglen):
    """未被程序访问的区间 → contiguous gap 标记为 virtual field。"""
    out, pos = [], 0
    for lo, hi in fields:
        if lo > pos:
            out.append((pos, lo, True))
        out.append((lo, hi, False))
        pos = hi
    if pos < msglen:
        out.append((pos, msglen, True))
    return out


def field_of(fields, off):
    """偏移落在哪个字段（用于把指令映射到字段）。"""
    for i, (lo, hi) in enumerate(fields):
        if lo <= off < hi:
            return i
    return None


# --------------------------------------- §3.4.1-§3.4.2 循环与迭代相关指令
def split_iterations(loop_trace, entry_eip):
    """按循环唯一入口点把一条循环子序列切成各次迭代。

    loop_trace: 该循环对应的 [(eip, [offsets])] 子序列。
    """
    iters, cur = [], []
    for rec in loop_trace:
        if rec[0] == entry_eip and cur:
            iters.append(cur)
            cur = []
        cur.append(rec)
    if cur:
        iters.append(cur)
    return iters


def iteration_dependent(iters, fields):
    """Ii = 在第 i 次迭代里访问了「其他迭代都没访问的字段」的指令集合。

    返回 (I, n)；论文：若 I_n 为空则把循环当作只有 n-1 次迭代
    （最后一次往往只是终止条件检查，不是真正的迭代）。
    """
    n = len(iters)
    touched = [set() for _ in iters]
    for i, it in enumerate(iters):
        for eip, offs in it:
            for o in offs:
                f = field_of(fields, o)
                if f is not None:
                    touched[i].add((eip, f))
    per_inst = [{} for _ in iters]
    for i, it in enumerate(iters):
        for eip, _ in it:
            per_inst[i].setdefault(eip, set())
        for eip, offs in it:
            for o in offs:
                f = field_of(fields, o)
                if f is not None:
                    per_inst[i][eip].add(f)
    I = []
    for i in range(n):
        others = set()
        for j in range(n):
            if j != i:
                others |= touched[j]
        I.append({eip for eip, fs in per_inst[i].items()
                  if any((eip, f) not in others for f in fs)})
    if I and I[-1] == set():
        n -= 1
        I = I[:n]
    return I, n


# --------------------------------------------- §3.4.3 Figure 4 记录边界
def find_record_boundaries(n, I, fields, iters):
    """Figure 4：s_j / e_j。iters[i] 是第 i+1 次迭代的 [(eip, [offsets])]。"""
    s = [-1] * n
    inst_field = []            # inst_field[i][eip] = 该迭代里 eip 访问的字段下标
    for i in range(n):
        d = {}
        for eip, offs in iters[i]:
            for o in offs:
                f = field_of(fields, o)
                if f is not None:
                    d.setdefault(eip, set()).add(f)
        inst_field.append(d)

    def start_of(eip, i, pool):
        fs = inst_field[i].get(eip, set()) & pool if pool else inst_field[i].get(eip, set())
        return min(fields[f][0] for f in fs) if fs else None

    for j in range(n):
        if s[j] != -1:
            continue
        s[j] = min((start_of(eip, j, None) for eip in I[j]
                    if start_of(eip, j, None) is not None), default=-1)
        Icur = {eip for eip in I[j]
                if start_of(eip, j, None) == s[j]}     # 落在记录首字段的指令
        for i in range(j + 1, n):
            hit = Icur & I[i]
            if hit:
                vals = [start_of(eip, i, None) for eip in hit]
                s[i] = min(v for v in vals if v is not None)
    e = [0] * n
    for j in range(n - 1):
        e[j] = s[j + 1] - 1
    last = [fields[f][1] for f in set().union(*inst_field[n - 1].values())] \
        if inst_field[n - 1] else [0]
    e[n - 1] = max(last)
    return s, e


# --------------------------------------------------------- §3.5 记录类型
def collapse_child_loops(Qi, child_segments):
    """把 Qi 里属于某个子循环执行的整段折叠成一条虚指令（virtual EIP）。

    child_segments: [(start_idx, end_idx, loop_id)]，下标针对 Qi 这个序列。
    """
    out, i = [], 0
    while i < len(Qi):
        hit = None
        for s0, e0, vid in child_segments:
            if s0 <= i < e0:
                hit = (e0, vid)
                break
        if hit:
            out.append(("V", hit[1]))
            i = hit[0]
        else:
            out.append(Qi[i])
            i += 1
    return out


def qi_of(iters_i, fields, record, child_segments=()):
    """Qi = Ii 中访问了第 i 条记录内字段的指令（保持出现顺序，便于折叠）。"""
    seq = []
    for eip, offs in iters_i:
        if any(record[0] <= o < record[1] for o in offs) and eip not in seq:
            seq.append(eip)
    return collapse_child_loops(seq, child_segments)


# ------------------------------------------------------------- §3.6 约束
def single_value_constraints(preds):
    """单值约束：f(input[i]) {<,=,>,≠} y，且 f 只依赖输入值与硬编码常量。"""
    out = []
    for field, op, rhs, depends_only_on_input in preds:
        if depends_only_on_input:
            out.append((field, op, rhs))
    return out


def functional_constraints(fields, funcs):
    """函数式约束：input[x] = f(input[y], input[z], …) —— 能抓住多数校验和。"""
    return [(x, tuple(sorted(srcs))) for x, srcs in funcs]


def determine_length(n, eq_checks, cond_fields):
    """§3.4.4 记录序列长度由什么决定：(a) 终止记录 (b) 长度字段 (c) 隐式固定。"""
    consts = {}
    for it, const, ok in eq_checks:
        consts.setdefault(const, []).append((it, ok))
    for const, checks in consts.items():
        m = dict(checks)
        if all(not m.get(i, False) for i in range(1, n)) and m.get(n, False):
            return "a"
    if cond_fields:
        return "b"
    return "c"
