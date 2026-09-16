#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BinDiff 匹配流水线自检:多阶段指纹匹配 + confidence/similarity(全部断言实跑)。

依据(BinDiff 官方 concepts.md 与 zynamics manual,本轮实读):
  * 总体策略:「An attribute is unique in both binaries → matched;appears several times in
    both → ambiguous,drill down;no match → kept in the unmatched set」;
    之后用 call graph 的 parents/children **缩小候选集**继续找唯一匹配,直到不再有新匹配。
  * 算法按 **match quality** 从高到低依次施加,**高置信算法产生的 fixed point 是后续算法的输入**。
  * 「Edge matching is the stronger criterion」,但 call graph 的边数随顶点数超线性增长,
    所以出现性能问题时应**先禁用基于边的匹配**。
  * confidence = 各算法置信度经 **sigmoid 压扁**后平均,不是简单平均。官方原文:
    『the average algorithm confidence (match quality) ... weighted by a sigmoid
    squashing function. The values aren't simply averaged because few single weak
    matches in an otherwise perfectly matched function/binary shouldn't drag the
    confidence down too much.』
    **口径差异**:官方对各算法只给定性等级,本 demo 映射为 0.98/0.80/0.60/0.45,
    仅为让流水线可跑可断言,不代表官方数值。
  * function similarity 权重(官方给出):flow graph 边 25% / 基本块 15% / 指令 10% /
    **flow graph MD index 差异 50%**;binary similarity 权重:边 35% / 基本块 25% /
    函数 10% / 指令 10% / **call graph MD index 差异 20%**;两者最后都**乘 confidence**。
  * binary similarity 只统计**非库函数**,否则"共用同一套运行库"会虚高相似度。
  * address sequence 匹配有两条额外约束:必须先与 relaxed MD index + flow graph MD index
    一致,且两侧等价函数集合**大小相等**(否则会不加区分地把所有函数配上)。

confidence / similarity 的公式与断言在 bindiff_metrics.py,这里只跑匹配流水线。

运行: python bindiff_pipeline.py     退出码 0 表示全部断言通过。
"""

import sys

from bindiff_match import (FULL_WEIGHTS, byte_hash, flowgraph_hash, md_index_node,
                           md_of_functions, prime_signature, relaxed_md_index,
                           structural_signature)

FAIL = []


def check(cond, label, detail=""):
    if not cond:
        FAIL.append(label)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  <- " + str(detail)) if detail else ""))
    return cond


# --------------------------------------------------------------- 测试数据

def make_binaries():
    """两个版本的样本,每一对都对应一种指纹的作用范围。

    B 相对 A 的改动:改一个立即数 / 函数改名 / 加一条指令(但字符串引用不变)/
    删一个函数 / 加一个函数。
    """
    fn = lambda insns, blocks, strings, calls, **kw: dict(
        insns=insns, blocks=blocks, strings=strings, calls=calls, **kw)
    A = {
        "main": fn({"b0": ["stp", "mov", "bl", "ldr", "ret"]},
                   {"b0": ["end"], "end": []}, set(), {"parse": 1}),
        "parse": fn({"b0": ["ldr", "cmp", "b.ne", "bl", "b"], "b1": ["bl", "ret"]},
                    {"b0": ["b1", "end"], "b1": ["end"], "end": []}, set(), {"check": 1}),
        "check": fn({"b0": ["ldr", "mov", "cmp", "mov", "ret"]},
                    {"b0": []}, set(), {}),
        "hash": fn({"b0": ["ldr", "eor", "eor", "eor", "str", "ret"]},
                   {"b0": []}, set(), {}),
        "log_error": fn({"b0": ["adrp", "add", "bl", "ret"]},
                        {"b0": []}, {"invalid input"}, {}),
        "helper_add": fn({"b0": ["add", "ret"]}, {"b0": []}, set(), {}),
    }
    B = {
        "main": fn(A["main"]["insns"], A["main"]["blocks"], set(), {"parse": 1}),
        "parse": fn(A["parse"]["insns"], A["parse"]["blocks"], set(), {"check": 1}),
        # 只改了一个立即数:字节级哈希失配,但助记符序列完全一致 → prime signature 命中
        "check": fn(A["check"]["insns"], A["check"]["blocks"], set(), {}, imm_changed=True),
        # 函数被改名 + 改了立即数 → 两个高置信指纹都失配,prime signature 补上
        "hash_alias": fn(A["hash"]["insns"], A["hash"]["blocks"], set(), {},
                         imm_changed=True),
        # 指令数变了(多一条 adrp)→ 前两档都失配,只剩"引用了同一个字符串"这一条线索
        "log_error": fn({"b0": ["adrp", "adrp", "add", "bl", "ret"]},
                        {"b0": []}, {"invalid input"}, {}),
        "validate": fn({"b0": ["ldr", "cbz", "ret"]}, {"b0": []}, set(), {}),  # 新增函数
    }
    return A, B


# --------------------------------------------------------------- 流水线

def staged_match(A, B, algos, lib=("libc_start",)):
    """按置信度从高到低施加指纹;只接受「在两侧候选集中都唯一」的签名。

    官方原文:A match is created if a signature occurs once (and only once) in both
    examined subsets of signatures. 歧义 → 留给下一个算法(drill down)。
    返回 None 的算法(例:函数不引用任何字符串)表示该函数**不参与**这一档,必须过滤掉,
    否则所有「无字符串的函数」会彼此"相等"而被错误配对。官方的 string references 算法
    原文就是「used as the matching attribute if at least one string is referenced」;
    loop count 算法同样「Only applied if at least one loop is present」。
    """
    matched, log, cum = {}, [], 0
    for name, conf, sig in algos:
        used_b = {v[0] for v in matched.values()}
        sa, sb = {}, {}
        for f in A:
            if f not in matched and f not in lib:
                v = sig(A, f)
                if v is not None:
                    sa[f] = v
        for f in B:
            if f not in used_b and f not in lib:
                v = sig(B, f)
                if v is not None:
                    sb[f] = v
        hits = []
        for val in set(sa.values()) & set(sb.values()):
            fa = [f for f, v in sa.items() if v == val]
            fb = [f for f, v in sb.items() if v == val]
            if len(fa) == 1 and len(fb) == 1:          # 两侧都唯一才接受
                matched[fa[0]] = (fb[0], name, conf)
                hits.append((fa[0], fb[0]))
        cum += len(hits)
        left_a = len([f for f in A if f not in matched and f not in lib])
        left_b = len([f for f in B if f not in {v[0] for v in matched.values()}
                      and f not in lib])
        log.append((name, conf, hits, cum, (left_a, left_b)))
    return matched, log


def propagate(A, B, matched, lib=("libc_start",)):
    """官方:匹配后再对「已匹配函数的 parents/children」做 drill down 缩小候选集。

    只有当两侧都恰好剩一个未匹配 callee 时才敢下结论 —— 这正是"缩小候选集"的含义。
    """
    added = []
    for a in list(matched):
        b = matched[a][0]
        kids_a = [f for f in A[a]["calls"] if f not in matched and f not in lib]
        kids_b = [f for f in B[b]["calls"] if f not in {v[0] for v in matched.values()}
                  and f not in lib]
        if len(kids_a) == 1 and len(kids_b) == 1:
            matched[kids_a[0]] = (kids_b[0], "drilldown", matched[a][2])
            added.append((kids_a[0], kids_b[0]))
    return matched, added


# --------------------------------------------------------------- 自检

def main():
    A, B = make_binaries()
    algos = [
        ("hash", 0.98, lambda D, f: byte_hash(D[f])),
        ("prime_signature", 0.80,
         lambda D, f: prime_signature(D[f]["insns"]["b0"], mode="product")),
        ("string_references", 0.60,
         lambda D, f: tuple(sorted(D[f]["strings"])) or None),
    ]

    print("== 1. 结构签名(官方三元组) ==")
    sig = structural_signature(A["parse"])
    check(sig == (3, 3, 1), "parse 签名 =(基本块 3, 边 3, 调用 1)", sig)
    check(structural_signature(A["helper_add"]) == (1, 0, 0), "helper_add 无出边无调用")

    print("== 2. 为什么哈希要配一个 prime signature ==")
    check(byte_hash(A["check"]) != byte_hash(B["check"]),
          "改了一个立即数 → 字节级哈希立刻失配")
    check(flowgraph_hash(A["check"]) == flowgraph_hash(B["check"]),
          "同一改动下,CFG/助记符层面的摘要不变 —— 这正是 prime 能补位的前提")
    p1 = prime_signature(A["check"]["insns"]["b0"])
    p2 = prime_signature(B["check"]["insns"]["b0"])
    check(p1 == p2, "助记符序列不变 → prime signature 相同", (p1, p2))
    check(prime_signature(["a", "b"], mode="product") ==
          prime_signature(["b", "a"], mode="product"),
          "prime 与指令顺序无关(乘法交换律)")
    check(prime_signature(["add", "ret"], mode="sum") ==
          prime_signature(["ret", "add"], mode="sum"), "sum 口径同样与顺序无关")
    check(prime_signature(["a"] * 60, mode="product") > 2 ** 64,
          "product 口径数值迅速溢出 64 位 —— 实现改成求和(见 README 口径分歧)",
          prime_signature(["a"] * 60, mode="product"))

    print("== 3. 多阶段匹配:高置信先跑,低置信只补空缺 ==")
    matched, log = staged_match(A, B, algos)
    # matched[A 的函数名] = (B 的函数名, 命中的算法, 置信度)
    check(matched["main"][0] == "main" and matched["main"][1] == "hash",
          "main 由 hash 匹配(最高置信 0.98)", matched["main"])
    check(matched["parse"][1] == "hash", "parse 同样由 hash 匹配", matched["parse"])
    check(matched["check"][1] == "prime_signature" and matched["check"][2] == 0.80,
          "改了立即数的 check → hash 失配,由 prime_signature 以 0.80 补上",
          matched["check"])
    check(matched["hash"][0] == "hash_alias" and matched["hash"][1] == "prime_signature",
          "改名 + 改立即数的函数 → prime_signature 补上,指纹不认识名字",
          matched["hash"])
    check(matched["log_error"][1] == "string_references" and matched["log_error"][2] == 0.60,
          "指令数变了的 log_error → 只剩字符串引用这条线索,置信度 0.60",
          matched["log_error"])
    check("helper_add" not in matched and "validate" not in matched,
          "被删/新增的函数没有对应项")
    cum = [c for _, _, _, c, _ in log]
    check(all(cum[i] <= cum[i + 1] for i in range(len(cum) - 1)),
          "已匹配函数数随算法推进单调不减(每档只在剩余集合上做)", cum)
    check([v for _, _, _, _, v in log][-1] == (1, 1),
          "三档跑完后两侧各剩 1 个未匹配函数(A 的被删 / B 的新增)",
          [(n, v) for n, _, _, _, v in log])

    print("== 3b. 再加最弱的一档会引入假匹配 ==")
    weak = algos + [("structural_signature", 0.45, lambda D, f: structural_signature(D[f]))]
    m2, _ = staged_match(A, B, weak)
    check(m2.get("helper_add", (None,))[0] == "validate" and m2["helper_add"][2] == 0.45,
          "剩余集合里两个孤立函数结构签名都是 (1,0,0) → 被错误配上",
          m2.get("helper_add"))
    check(m2["helper_add"][2] == min(a[1] for a in weak),
          "这个假匹配的置信度正是全场最低 —— 这就是 confidence 存在的意义")

    print("== 4. drill down:用调用关系解开指纹歧义 ==")
    fn = lambda insns: dict(insns={"b0": insns}, blocks={"b0": []}, strings=set(), calls={})
    d_entry = dict(insns={"b0": ["bl", "ret"]}, blocks={"b0": []}, strings=set(),
                   calls={"worker": 1})
    d_entry2 = dict(insns={"b0": ["bl", "ret"]}, blocks={"b0": []}, strings=set(),
                    calls={"worker2": 1})
    DA = {"entry": d_entry, "worker": fn(["add", "mul", "ret"]), "twin": fn(["add", "mul", "ret"])}
    DB = {"entry": d_entry2, "worker2": fn(["add", "mul", "ret"]), "twin": fn(["add", "mul", "ret"])}
    m, _ = staged_match(DA, DB, algos, lib=())
    check(m.get("entry", (None,))[0] == "entry", "entry 在两侧都唯一 → 直接匹配")
    check("worker" not in m and "twin" not in m,
          "worker 与 twin 指纹完全相同 → 两侧都歧义,任何指纹算法都不匹配")
    m, added = propagate(DA, DB, m)
    check(added == [("worker", "worker2")],
          "drill down:已匹配函数的唯一 callee → 补上", added)
    check("twin" not in m, "没有调用关系可依托的 twin 仍留在未匹配集合", sorted(m))

    print("== 5. MD index:公式、方向性与「不考虑拓扑序」 ==")
    cg = {k: sorted(A[k]["calls"]) for k in A}        # 调用图必须用邻接表表达
    top = md_of_functions(cg, inverted=False)
    bot = md_of_functions(cg, inverted=True)
    check(top["main"] > 0 and top["parse"] > 0,
          "有边的顶点 MD index > 0(1/量 的倒数是正数)")
    check(top["helper_add"] == 0.0,
          "孤顶点没有关联边 → MD index = 0(官方:顶点值 = 其所有关联边之和)")
    check(all(abs(top[k] - bot[k]) < 1e-12 for k in top),
          "默认权重 kDefaultWeightsNode={2,3,5,7,0,0} 的拓扑层系数为 0 → "
          "top-down 与 bottom-up 结果完全一致")
    top2 = md_of_functions(cg, weights=FULL_WEIGHTS)
    bot2 = md_of_functions(cg, inverted=True, weights=FULL_WEIGHTS)
    check(any(abs(top2[k] - bot2[k]) > 1e-12 for k in top2),
          "把拓扑层系数调成非零,两个方向才分道扬镳",
          [k for k in top2 if abs(top2[k] - bot2[k]) > 1e-12])
    check(max(top.values()) <= 1.0, "倒数形式把边的强度压在有界区间内(<= 1)")
    check(abs(sum(top2.values()) - sum(top.values())) > 1e-9,
          "加入拓扑层权重后 MD index 数值改变")
    fg = A["parse"]["blocks"]                         # 同一公式也用于单个函数的 flow graph
    check(md_index_node(fg, "b0", roots=["b0"])[0] > 0,
          "对 flow graph 顶点同样可算 MD index(与调用图共用公式)")
    rel = {b: relaxed_md_index(fg, b, roots=["b0"]) for b in fg}
    full_fg = {b: md_index_node(fg, b, weights=FULL_WEIGHTS, roots=["b0"])[0] for b in fg}
    check(any(abs(rel[b] - full_fg[b]) > 1e-12 for b in rel),
          "relaxed(丢掉拓扑层)与带拓扑层的完整 MD index 不同",
          [(b, round(rel[b] - full_fg[b], 6)) for b in sorted(rel)])
    check(all(abs(rel[b] - md_index_node(fg, b, roots=["b0"])[0]) < 1e-12 for b in rel),
          "但 relaxed 与默认权重下的结果一致 —— 「不考虑拓扑序」在层系数为 0 时等价")

    print("== 8. 库函数不参与统计 ==")
    libfn = lambda: dict(insns={"b0": ["ret"]}, blocks={"b0": []}, strings=set(), calls={})
    A2, B2 = dict(A, libc_start=libfn()), dict(B, libc_start=libfn())
    m3, _ = staged_match(A2, B2, algos, lib=("libc_start",))
    check("libc_start" not in m3, "库函数被排除在匹配集合之外")
    m4, _ = staged_match(A2, B2, algos, lib=())
    check(m4.get("libc_start", (None,))[0] == "libc_start",
          "不排除时会匹配上 —— 官方因此规定只统计非库函数", m4.get("libc_start"))

    print("\n结果: %d 项失败" % len(FAIL))
    for x in FAIL:
        print("  FAIL: %s" % x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
