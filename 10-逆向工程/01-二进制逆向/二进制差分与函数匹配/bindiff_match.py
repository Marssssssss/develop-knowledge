#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BinDiff 的两类结构指纹:prime signature 与 MD index。

依据(本轮实读):
  * BinDiff 官方 manual / 官方仓库 docs/concepts.md:
    - 「Every function gets a signature, based on the structure of the (normalized) flow graph.
       The signature consists of: Number of basic blocks / Number of edges between basic blocks /
       Number of calls to sub-functions.」
    - prime:「Each mnemonic gets assigned a unique small prime number. These primes are
       multiplied for all instructions of the function. This yields a structurally invariant,
       instruction order independent product.」
    - MD index 是多个匹配算法的公共基础;top-down 按入口点分层、bottom-up 按出口点分层,
      relaxed 版本不考虑拓扑序。
  * google/bindiff 源码 match/graph_util.h(官方仓库,经镜像读取)给出的**精确公式**:
      md_index(edge) = sqrt(w0)*in_deg(src) + sqrt(w1)*out_deg(src)
                     + sqrt(w2)*in_deg(tgt) + sqrt(w3)*out_deg(tgt)
                     + sqrt(w4)*level(src) + sqrt(w5)*level(tgt)
      返回 1.0 / md_index;默认节点权重 kDefaultWeightsNode = {2,3,5,7,0,0};
      「MD index for a vertex is defined as the sum of MD indices of all in- and out-edges
       of the vertex」,且求和**先排序**因为浮点加法不可交换。
  * 口径差异(如实记录):manual 用 product,而 call_graph.cc / concepts 走读资料写成
    「Function Prime = Sum of Basic Block Primes; Basic Block Prime = Sum of Instruction Primes」。
    本模块两种都实现,并在 README 标注口径分歧。

运行: python bindiff_check.py     退出码 0 表示全部断言通过。
"""

import math
from collections import deque

# 助记符 → 唯一小素数(取前 26 个素数,只演示原理)
PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
          73, 79, 83, 89, 97, 101]


def mnemonic_prime(mn, table=None):
    """把助记符映射到唯一小素数(真实实现用固定表,这里按首次出现顺序分配)。"""
    table = table if table is not None else MNEMONIC_TABLE
    if mn not in table:
        table[mn] = PRIMES[len(table) % len(PRIMES)]
    return table[mn]


MNEMONIC_TABLE = {}


def prime_signature(mnemonics, mode="product", table=None):
    """指令序列 → 结构不变量指纹。

    mode="product": manual 原文口径,对顺序不敏感(乘法交换律),但数值会迅速变大;
    mode="sum"    : 源码走读口径,同样顺序不敏感且数值可控 —— 这正是实现改成求和的动机。
    """
    vals = [mnemonic_prime(m, table) for m in mnemonics]
    if mode == "product":
        out = 1
        for v in vals:
            out *= v
        return out
    return sum(vals)


# --------------------------------------------------------------- 图与 MD index

def bfs_levels(graph, roots, reverse=False):
    """按拓扑分层:top-down 从入口点、bottom-up 从出口点(对应 inverted 参数)。

    真实实现把 BFS 序号存在顶点属性 bfs_top_down_ / bfs_bottom_up_ 上,
    MD index 通过 inverted 开关选用其中一套。
    """
    level = {}
    q = deque()
    for r in roots:
        if r not in level:
            level[r] = 0
            q.append(r)
    while q:
        n = q.popleft()
        outs = graph.get(n, [])
        if reverse:
            outs = [u for u in graph if n in graph[u]]
        for m in outs:
            if m not in level:
                level[m] = level[n] + 1
                q.append(m)
    return level


def md_index_edge(graph, u, v, level, weights):
    """官方公式逐项实现:六项加权(权重取平方根),最后取倒数。"""
    if len(weights) == 4:                      # proximity/relaxed 变体:不含拓扑层
        w = list(weights) + [0.0, 0.0]
    else:
        w = list(weights)
    indeg = lambda x: sum(1 for p in graph if x in graph[p])
    outdeg = lambda x: len(graph.get(x, []))
    total = (math.sqrt(w[0]) * indeg(u) + math.sqrt(w[1]) * outdeg(u) +
             math.sqrt(w[2]) * indeg(v) + math.sqrt(w[3]) * outdeg(v) +
             math.sqrt(w[4]) * level.get(u, 0) + math.sqrt(w[5]) * level.get(v, 0))
    return 1.0 / total if total else 0.0


DEFAULT_NODE_WEIGHTS = (2.0, 3.0, 5.0, 7.0, 0.0, 0.0)   # kDefaultWeightsNode
FULL_WEIGHTS = (2.0, 3.0, 5.0, 7.0, 11.0, 13.0)         # 加入拓扑层权重
# relaxed/proximity 变体:只保留四项局部度权重,拓扑层系数补 0 —— 这正是"不考虑拓扑序"
PROXIMITY_WEIGHTS = (2.0, 3.0, 5.0, 7.0)


def md_index_node(graph, v, inverted=False, weights=DEFAULT_NODE_WEIGHTS, roots=None):
    """顶点 MD index = 其所有入边与出边 MD index 之和(**先排序再求和**)。

    注意 graph 必须是**邻接表**(顶点 → 后继列表)。传成 {"b0": {...}} 这类字典时,
    `for m in graph.get(v, [])` 会退化成遍历字典的键,把块名当成边 —— 见 README 坑 3。
    """
    level = bfs_levels(graph, roots if roots is not None else [next(iter(graph))],
                       reverse=inverted)
    vals = []
    for p in graph:
        if v in graph[p]:
            vals.append(md_index_edge(graph, p, v, level, weights))
    for m in graph.get(v, []):
        vals.append(md_index_edge(graph, v, m, level, weights))
    vals.sort()                                # 官方注释:Summation is not commutative for doubles
    return sum(vals), len(vals), level


def relaxed_md_index(graph, v, inverted=False, roots=None):
    """relaxed 版本:不计拓扑序,只看局部入/出度(proximity 用两跳邻域)。"""
    return md_index_node(graph, v, inverted=inverted, weights=PROXIMITY_WEIGHTS,
                         roots=roots)[0]


def md_of_functions(callgraph, inverted=False, weights=DEFAULT_NODE_WEIGHTS):
    """对调用图整体算一遍顶点 MD index(等价于 CallGraph::GetMdIndex)。"""
    roots = [n for n in callgraph if not any(n in callgraph[p] for p in callgraph)]
    return {n: md_index_node(callgraph, n, inverted=inverted, weights=weights,
                             roots=roots)[0] for n in callgraph}


# --------------------------------------------------------------- 结构签名

def structural_signature(flowgraph):
    """官方 signature 三元组:基本块数 / 块间边数 / 调用子函数次数。"""
    blocks = len(flowgraph["blocks"])
    edges = sum(len(s) for s in flowgraph["blocks"].values())
    calls = sum(flowgraph.get("calls", {}).values())
    return (blocks, edges, calls)


def flowgraph_hash(flowgraph):
    """「函数原始字节的哈希」的模型:块内指令序列与 CFG 边都一致才算字节级相同。"""
    h = []
    for b in sorted(flowgraph["blocks"]):
        h.append("%s:%s" % (b, ",".join(flowgraph["insns"].get(b, []))))
    for b in sorted(flowgraph["blocks"]):
        h.append("%s>%s" % (b, ",".join(sorted(flowgraph["blocks"][b]))))
    return "|".join(h)


def byte_hash(flowgraph):
    """「函数原始字节的哈希」的模型:除了 CFG/助记符,还包含立即数。

    这是流水线里**置信度最高**的一档指纹,但它最脆:改一个立即数就整体失配。
    真实 BinDiff 里对应「函数规范化字节序列的哈希」,配合 prime signature 一起用 ——
    前者精确、后者耐改,两者互补正是本 demo 第 2 节要展示的点。
    """
    h = flowgraph_hash(flowgraph)
    if flowgraph.get("imm_changed"):
        h += "|imm+1"
    return h


def block_hashes(flowgraph):
    """每个基本块的哈希 + 指令条数(官方:块哈希与 prime 匹配要求 ≥4 条指令)。"""
    out = {}
    for b in flowgraph["blocks"]:
        insns = flowgraph["insns"].get(b, [])
        out[b] = (hash(tuple(insns)) & 0xFFFFFFFF,
                  prime_signature(insns, mode="product"), len(insns))
    return out
