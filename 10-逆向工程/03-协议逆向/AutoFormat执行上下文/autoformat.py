#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AutoFormat（NDSS 2008）最小复现：上下文感知执行监视 → 协议字段树 → 并行/顺序字段。

来源：Z. Lin, X. Jiang, D. Xu, X. Zhang, *Automatic Protocol Format Reverse
Engineering Through Context-Aware Monitored Execution*, NDSS 2008。

复现内容：
  §3.1 上下文感知执行监视的污点传播规则（mov 只传播、算术/逻辑指令做并集、
       未标记源 → 取消目的标记），以及被读时落盘的 <o, c, s, l> 记录
  §3.2.1 Algorithm 1 协议字段树生成（连续偏移 + 相同调用栈 → 合并）
  §3.2.2 Algorithm 2 并行字段识别（最低偏移的执行历史 + 共享前缀 ≥ h%，h=80）
       以及顺序字段的前序遍历
"""

from collections import namedtuple

Rec = namedtuple("Rec", "o c s l")     # 偏移 / 内容 / 调用栈 / 指令地址
H_SIMILAR = 80                         # §3.2.2 脚注 3：实验取 h = 80


# ------------------------------------------------- §3.1 上下文感知执行监视
class Monitor:
    """按论文的污点传播规则维护寄存器/内存的偏移标注，并在被读时落记录。"""

    def __init__(self, msg):
        self.msg = msg
        self.taint = {}                # 位置 -> set(offsets)
        self.log = []

    def mark_input(self, loc, offsets):
        """拦截 recv/read 类系统调用后给输入打标记（每个字节带自身偏移）。"""
        self.taint[loc] = frozenset(offsets)

    def mov(self, dst, src):
        """数据搬移：源被标记 → 目的继承其标注；源未标记 → 取消目的标记。

        论文原文：mov 不"处理"操作数，因此**不产生 chunk**（Tupni 侧同理）。
        """
        if src in self.taint:
            self.taint[dst] = self.taint[src]
        else:
            self.taint.pop(dst, None)

    def arith(self, dst, a, b):
        """算术/逻辑指令：两个被标记的操作数 → 标注取**并集**。"""
        ta, tb = self.taint.get(a), self.taint.get(b)
        if ta and tb:
            self.taint[dst] = frozenset(ta | tb)
        elif ta or tb:
            self.taint[dst] = frozenset(ta or tb)
        else:
            self.taint.pop(dst, None)

    def read(self, loc, stack):
        """读被标记的内存 → 落一条 <o, c, s, l> 记录（o 为其在报文中的偏移）。"""
        for off in sorted(self.taint.get(loc, ())):
            self.log.append(Rec(off, self.msg[off], tuple(stack), loc))


def dedup(log):
    """§3.2.1 预处理：连续的完全相同的记录只保留一条。"""
    out = []
    for r in log:
        if not out or r != out[-1]:
            out.append(r)
    return out


# ------------------------------------------- §3.2.1 Algorithm 1 字段树生成
def _subsumes(a, b):
    return a["lo"] <= b["lo"] and b["hi"] <= a["hi"]


def _find_parent(node, v):
    for c in node["children"]:
        if _subsumes(c, v):
            return _find_parent(c, v)
    return node


def _insert(root, p):
    v = {"lo": min(p), "hi": max(p) + 1, "children": [], "parallel": False}
    u = _find_parent(root, v)
    for c in list(u["children"]):
        if _subsumes(v, c):
            u["children"].remove(c)
            v["children"].append(c)
    u["children"].append(v)
    u["children"].sort(key=lambda x: x["lo"])
    v["children"].sort(key=lambda x: x["lo"])
    return v


def build_field_tree(log, msglen):
    """Algorithm 1：连续偏移 + 相同调用栈 → 合并成一个节点。"""
    root = {"lo": 0, "hi": msglen, "children": [], "parallel": False}
    assert log, "空日志无法建树"
    p = [log[0].o]
    for i in range(1, len(log)):
        if log[i].o == log[i - 1].o + 1 and log[i].s == log[i - 1].s:
            p.append(log[i].o)
        else:
            _insert(root, p)
            p = [log[i].o]
    # 论文 Algorithm 1 的伪代码在第 20 行直接 Return，**没有冲刷最后一段**；
    # 不补这一句，最后一个字段会整体丢失。此处补齐。
    _insert(root, p)
    return root


# -------------------------------------- §3.2.2 Algorithm 2 并行字段识别
def history_of(log, off):
    """某个偏移的执行历史 = 该偏移被读时记录的调用栈序列。"""
    return [r.s for r in log if r.o == off]


def similar(h1, h2, h=H_SIMILAR):
    """脚注 3：共享前缀 ≥ 整个历史的 h%。

    论文未说明「整个历史」取长的一侧还是短的一侧；本 demo 取**较长的一侧**
    （更保守：要求共享前缀把更长的那条也基本覆盖住）。
    """
    n = min(len(h1), len(h2))
    k = 0
    while k < n and h1[k] == h2[k]:
        k += 1
    if not h1 or not h2:
        return False
    return k * 100 >= h * max(len(h1), len(h2))


def lowest(node):
    return node["children"][0]["lo"] if node["children"] else node["lo"]


def mark_parallel(root, log, h=H_SIMILAR):
    """Algorithm 2：逐层 BFS，把执行历史相似的子节点合并成并行字段。"""
    queue = [root]
    while queue:
        v = queue.pop(0)
        kids = v["children"]
        if len(kids) < 2:
            queue.extend(kids)
            continue
        hists = [history_of(log, lowest(k)) for k in kids]
        groups, used = [], [False] * len(kids)
        for i in range(len(kids)):
            if used[i]:
                continue
            grp = [i]
            used[i] = True
            for j in range(i + 1, len(kids)):
                if not used[j] and similar(hists[i], hists[j], h):
                    grp.append(j)
                    used[j] = True
            groups.append(grp)
        newkids = []
        for grp in groups:
            if len(grp) >= 2:
                # 论文：被判为并行的子节点之上插入一个层次节点代表这个并行字段
                par = {"lo": min(kids[i]["lo"] for i in grp),
                       "hi": max(kids[i]["hi"] for i in grp),
                       "children": [kids[i] for i in grp], "parallel": True}
                newkids.append(par)
            else:
                newkids.append(kids[grp[0]])
        v["children"] = sorted(newkids, key=lambda x: x["lo"])
        queue.extend(kids)
    return root


def _walk(node):
    """前序遍历 node 的子树，只列叶子与「并行字段」节点。

    遇到并行字段节点**列入后不再下钻**——它的内部候选由外层对它的子树
    再走一遍 traverse 得到（§3.2.2 原文："Recursively, the same traversal is
    performed on the sub-trees each rooted at a hierarchical node that
    represents a parallel field"）。
    """
    lst, st = [], list(reversed(node["children"]))
    while st:
        n = st.pop()
        if not n["children"] or n["parallel"]:
            lst.append(n)
        if not n["parallel"]:
            st.extend(reversed(n["children"]))
    return lst


def _collect_parallel(node, acc):
    for c in node["children"]:
        if c["parallel"]:
            acc.append(c)
        _collect_parallel(c, acc)
    return acc


def sequential_fields(root):
    """§3.2.2：先对整棵树前序遍历得到一组顺序字段；
    再对**每个代表并行字段的层次节点**的子树递归走一遍，
    得到「该并行字段内部的候选列表」。返回 [[字段...], ...]。
    """
    out = [_walk(root)]
    for p in _collect_parallel(root, []):
        out.append(_walk(p))
    return out


def render(root, depth=0):
    lines = ["%s[%d,%d)%s" % ("  " * depth, root["lo"], root["hi"],
                              " *PARALLEL*" if root["parallel"] else "")]
    for c in root["children"]:
        lines.extend(render(c, depth + 1))
    return lines
