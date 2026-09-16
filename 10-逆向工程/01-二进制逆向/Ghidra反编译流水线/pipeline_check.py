#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ghidra 反编译流水线自检:SSA 构造 → 死代码消除 → 项重写(全部断言实跑)。

对应 Ghidra 官方「Main Work Flow」的 06 The Main Simplification Loop:
  a. Generate SSA Form      → `pcode_ssa.py`(支配树 + 支配边界 + phi + 重命名)
  b. Eliminate Dead Code    → `dce()`(官方:反编译器**精确到 bit** 地判死代码,
                              因为很多机器指令会留下与当前点无关的副作用)
  d. Perform Term Rewriting → `rewrite()`(官方:目标不是优化而是**简化与规范化**,
                              让分析师能读懂;所有 INT_SUB 在主循环里被规范化为
                              「取二进制补码再相加」,到 07 阶段再转回减法)

运行: python pipeline_check.py       退出码 0 表示全部断言通过。
"""

import sys

from pcode_ssa import Block, Func, Op, const, fmt, is_const, rv
from ssa_verify import dump, verify_ssa

# 有副作用的操作:死代码消除时不可删(对应 p-code 的 STORE/CALL/RETURN/分支)
SIDE_EFFECT = {"STORE", "CALL", "RETURN", "BRANCH", "CBRANCH", "BRANCHIND"}

FAIL = []


def check(cond, label, detail=""):
    if not cond:
        FAIL.append(label)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  <- " + str(detail)) if detail else ""))
    return cond


# --------------------------------------------------------------- 样例函数

def sum_loop():
    """while (i < n) { t += i; i++; } return t;   —— 带回边,需要 phi 处理循环变量。"""
    sp = rv("sp")
    return Func([
        Block("B0", [
            Op("COPY", rv("t"), [const(0)]),
            Op("COPY", rv("i"), [const(0)]),
        ], ["B1"]),
        Block("B1", [
            Op("INT_LESS", rv("cc"), [rv("i"), rv("n")]),
            Op("CBRANCH", None, [rv("cc")]),
        ], ["B2", "B3"]),
        Block("B2", [
            Op("INT_ADD", rv("t"), [rv("t"), rv("i")]),
            Op("INT_ADD", rv("i"), [rv("i"), const(1)]),
            Op("COPY", rv("dead"), [rv("t")]),          # 无人使用 → 待 DCE 删除
            Op("STORE", None, [sp, const(8), rv("t")]),  # 有副作用 → 不可删
        ], ["B1"]),
        Block("B3", [
            Op("RETURN", None, [rv("t")]),
        ], []),
    ], "B0")


def diamond():
    """if (a > b) m = a; else m = b; return m;  —— 经典菱形汇合点。"""
    return Func([
        Block("B0", [
            Op("LOAD", rv("a"), [rv("sp"), const(8)]),
            Op("LOAD", rv("b"), [rv("sp"), const(16)]),
            Op("INT_LESS", rv("cc"), [rv("b"), rv("a")]),
            Op("CBRANCH", None, [rv("cc")]),
        ], ["B1", "B2"]),
        Block("B1", [Op("COPY", rv("m"), [rv("a")])], ["B3"]),
        Block("B2", [Op("COPY", rv("m"), [rv("b")])], ["B3"]),
        Block("B3", [Op("COPY", rv("r"), [rv("m")]), Op("RETURN", None, [rv("r")])], []),
    ], "B0")


# --------------------------------------------------- 06b 死代码消除

def dce(func):
    """反复消除「输出无人使用且无副作用」的操作,直到不动点。

    官方说明:死代码消除对反编译器**至关重要**,因为很大比例的机器指令对机器状态
    有副作用(如设置标志位),而这些副作用在代码的特定点上与该函数无关;
    真正的难点是分不清临时/局部/全局变量,所以要**精确到 bit** 地判死。
    本 demo 用「输出 varnode 是否出现在任何 use 列表」作为 bit 级的保守近似。
    """
    removed, changed = [], True
    while changed:
        changed = False
        used = set()
        for n in func.order:
            for op in func.blocks[n].all_ops():
                if op.op == "MULTIEQUAL":
                    used.update(v for v in op.ins if v)
                used.update(v for v in op.ins if v is not None)
        for n in func.order:
            b = func.blocks[n]
            keep = []
            for op in b.ops:
                live = op.op in SIDE_EFFECT or op.out is None or op.out in used
                if live:
                    keep.append(op)
                else:
                    removed.append((n, op.text()))
                    changed = True
            b.ops = keep
    return removed


# --------------------------------------------------- 06d 项重写

def rewrite(func, fold_rounds=3):
    """项重写:复制传播 + 常量折叠 + 代数恒等式,并演示 INT_SUB 的规范化往返。

    官方原文:主简化循环把**所有减法规范化为「对二进制补码的加法」**,
    目的是让项重写规则集更小;到 07「Perform Final P-code Transformations」
    再把这种形式的加法转回 SUB,以免输出里出现 `x + -y` 这种反人类的表达式。
    """
    stats = {"copy": 0, "const": 0, "ident": 0}
    for _ in range(fold_rounds):
        changed = False
        # 1) 复制传播:out 只被 COPY 定义时,把所有 use 直接换成源
        alias = {}
        for n in func.order:
            for op in func.blocks[n].all_ops():
                if op.op == "COPY" and op.out is not None:
                    alias[op.out] = op.ins[0]
        for n in func.order:
            for op in func.blocks[n].all_ops():
                new_ins = [alias.get(v, v) for v in op.ins]
                if new_ins != op.ins:
                    op.ins = new_ins
                    stats["copy"] += 1
                    changed = True
        # 2) 常量折叠 + 代数恒等式(加法/乘法/异或的单位元与吸收元)
        for n in func.order:
            for op in func.blocks[n].all_ops():
                if op.out is None or len(op.ins) != 2 or not is_const(op.ins[0]):
                    continue
                a, b = op.ins
                for x, y in ((op.ins[0], op.ins[1]), (op.ins[1], op.ins[0])):
                    if not is_const(x):
                        continue
                    val, other = x[1], y
                    if op.op == "INT_ADD" and val == 0:
                        op.op, op.ins = "COPY", [other]
                        stats["ident"] += 1
                        changed = True
                    elif op.op == "INT_MULT" and val == 1:
                        op.op, op.ins = "COPY", [other]
                        stats["ident"] += 1
                        changed = True
                    elif op.op == "INT_XOR" and val == 0:
                        op.op, op.ins = "COPY", [other]
                        stats["ident"] += 1
                        changed = True
                    elif op.op in ("INT_ADD", "INT_MULT") and is_const(other):
                        v = (a[1] + b[1]) if op.op == "INT_ADD" else (a[1] * b[1])
                        op.op, op.ins, op.out = "COPY", [const(v)], op.out
                        stats["const"] += 1
                        changed = True
        if not changed:
            break
    return stats


def normalize_sub(func):
    """把 INT_SUB 规范化为「加二进制补码」(主简化循环的做法),记录改写条数。"""
    n = 0
    for name in func.order:
        for op in func.blocks[name].all_ops():
            if op.op == "INT_SUB":
                op.op = "INT_ADD"
                op.ins = [op.ins[0], ("neg", op.ins[1])]
                n += 1
    return n


def denormalize_sub(func):
    """07 阶段:把 `x + (-y)` 转回 `x - y`,消除输出里的补码加法。"""
    n = 0
    for name in func.order:
        for op in func.blocks[name].all_ops():
            if op.op == "INT_ADD" and len(op.ins) == 2 and op.ins[1] \
                    and isinstance(op.ins[1], tuple) and op.ins[1][0] == "neg":
                op.op, op.ins = "INT_SUB", [op.ins[0], op.ins[1][1]]
                n += 1
    return n


def main():
    print("== 1. 循环函数:支配树 / 支配边界 / phi 放置 ==")
    f = sum_loop()
    check(f.rpo()[0] == "B0", "逆后序以入口 B0 开始", f.rpo())
    idom = f.idoms()
    check(idom == {"B0": "B0", "B1": "B0", "B2": "B1", "B3": "B1"},
          "立即支配者:idom[B2]=B1,idom[B3]=B1", idom)
    frontier = {k: sorted(v) for k, v in f.df().items()}
    check(frontier.get("B2") == ["B1"],
          "支配边界 DF[B2]={B1} —— 回边的汇合点就是 B1", frontier)
    info = f.place_phis()
    names = sorted((fmt(p.out), b) for b in f.order for p in f.blocks[b].phis)
    check(("i", "B1") in names and ("t", "B1") in names,
          "循环变量 i/t 在 B1 各插入一个 phi", names)
    check(info["phis"] == 2, "共插入 2 个 phi(i 与 t)", info)
    f.rename()
    print("== 2. SSA 校验 ==")
    problems = verify_ssa(f)
    check(problems == [], "唯一命名 + 定义支配所有使用 + phi 入边数匹配", problems)
    versions = sorted(v for v in dir_versions(f))
    check(len(versions) >= 4, "重命名产生多版本 varnode", versions)
    ver_i = sorted(v for v in versions if v.startswith("i#"))
    check(len(ver_i) >= 3, "i 至少 3 个版本:B0/i#1,B1/phi,B2/i#2", ver_i)
    dump(f, "循环函数 SSA 形式")

    print("== 3. 死代码消除(06b) ==")
    removed = dce(f)
    check(any("dead" in t for _, t in removed), "删除无人使用的 COPY dead", removed)
    check(all("STORE" not in t for _, t in removed), "STORE 有副作用 → 不删")
    check(all("RETURN" not in t for _, t in removed), "RETURN 不删")
    f2 = sum_loop()
    f2.place_phis()
    f2.rename()
    dce(f2)
    check(verify_ssa(f2) == [], "DCE 之后 SSA 性质仍成立(不产生悬空使用)")

    print("== 4. 项重写(06d) ==")
    st = rewrite(f2)
    check(st["copy"] + st["ident"] + st["const"] > 0, "至少发生一次改写", st)
    txt = " ".join(op.text() for n in f2.order for op in f2.blocks[n].all_ops())
    check("MULT z#" not in txt and "MULT 1" not in txt, "乘 1 被折叠(若有)")

    print("== 5. INT_SUB 的规范化往返(06 → 07) ==")
    f3 = diamond()
    for op in f3.blocks["B0"].ops:
        if op.op == "INT_LESS":
            op.op, op.ins = "INT_SUB", [op.ins[1], op.ins[0]]
    n1 = normalize_sub(f3)
    check(n1 == 1, "主简化循环把 INT_SUB 规范化为 INT_ADD(加补码)", n1)
    mid = f3.blocks["B0"].ops[-2]
    check(mid.op == "INT_ADD" and mid.ins[1][0] == "neg",
          "规范化后形如 x + (-y)", mid.text())
    n2 = denormalize_sub(f3)
    check(n2 == 1, "07 阶段转回 INT_SUB(避免输出 x + -y)", n2)
    back = f3.blocks["B0"].ops[-2]
    check(back.op == "INT_SUB" and fmt(back.ins[0]) == "a" and fmt(back.ins[1]) == "b",
          "转回后与原语义一致(cc = a - b,即 b < a)", back.text())

    print("== 6. 菱形汇合:phi 只出现在真正的汇合点 ==")
    f4 = diamond()
    f4.place_phis()
    f4.rename()
    phis = [(b, sorted(p.out[1].split("#")[0] for p in f4.blocks[b].phis))
            for b in f4.order if f4.blocks[b].phis]
    check(phis == [("B3", ["m"])], "只在 B3 插入 m 的 phi(a/b 各一次定义)", phis)
    problems4 = verify_ssa(f4)
    check(problems4 == [], "菱形 SSA 校验通过", problems4)
    m = [p for p in f4.blocks["B3"].phis][0]
    check(len(m.ins) == 2 and all(v is not None for v in m.ins),
          "phi 两条入边对应 B1/B2 两条前驱", [fmt(v) for v in m.ins])

    print("\n结果: %d 项失败" % len(FAIL))
    for x in FAIL:
        print("  FAIL: %s" % x)
    return 1 if FAIL else 0


def dir_versions(func):
    out = set()
    for n in func.order:
        for op in func.blocks[n].all_ops():
            for v in ([op.out] if op.out else []) + list(op.ins):
                if v is not None and not is_const(v):
                    out.add(v[1])
    return out


if __name__ == "__main__":
    sys.exit(main())
