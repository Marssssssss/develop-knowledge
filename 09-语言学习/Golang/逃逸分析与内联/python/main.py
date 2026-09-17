#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go 编译器「逃逸分析 + 函数内联」模型。

依据官方源码 `cmd/compile/internal/escape/escape.go` 的头部注释与本 demo 实测：
  1. 位置图（location graph）：每个「分配语句/表达式」是一个顶点，每次赋值是一条带权有向边，
     权重 = **解引用次数减取地址次数**（原文 "derefs"）：`p = &q` 为 -1、`p = q` 为 0、
     `p = *q` 为 1、`p = **q` 为 2。`&x` 本身不可寻址，故权重不会低于 -1。
  2. 两条不变量：(a) 指向栈对象的指针不得存入堆；(b) 指向栈对象的指针不得比该对象活得更久。
     从 heapLoc / calleeLoc 反向传播即可判定哪些 location 必须上堆。
  3. 过程间分析：每个函数把「参数 → 堆」「参数 → 返回值」的信息总结成 **parameter tag**，
     在静态调用点被复用。
  4. 内联预算：`inlineMaxBudget = 80`、一次调用 57、调用参数额外 17、panic 1、throw 80；
     `-l=1`（默认）只内联叶子函数，`-l=4` 允许非叶子。
所有断言实跑通过。
"""

from escape_graph import *          # noqa: F401,F403
from inline_budget import *         # noqa: F401,F403

STATS = {"n": 0, "fail": []}


def check(label, cond, detail=""):
    STATS["n"] += 1
    if not cond:
        STATS["fail"].append(label)
    print(("PASS  " if cond else "FAIL  ") + label
          + (("  | " + str(detail)) if detail else ""))


def main():
    print("=== A. 位置图与边权（escape.go 头部注释逐条复刻）===")
    for expr, want in [("p = &q", -1), ("p = q", 0), ("p = *q", 1), ("p = **q", 2),
                       ("p = **&**&q", 2)]:
        got = derefs_of(expr)
        check("A %-12s 权重 %s" % (expr, "%+d" % want), got == want, got)
    check("A6 `&x` 不可寻址 → 权重不会低于 -1",
          derefs_of("p = &q") >= -1 and derefs_of("p = &*q") == 0)

    print("\n=== B. 两条不变量：什么必须上堆 ===")
    g = scenario_return_local()
    check("B1 返回值位置放 &x → x 逃逸（不变量 b：不能活得比对象久）",
          g.loc("x").escapes)
    g = scenario_stored_into_heap()
    check("B2 存进堆 → 逃逸（不变量 a：栈对象指针不得入堆）", g.loc("x").escapes)
    g = scenario_local_only()
    check("B3 只在函数内做值拷贝 → 三个局部变量全部留栈",
          g.stack_locals() == ["x", "y", "z"], g.stack_locals())
    g = scenario_deref_no_escape()
    check("B4 `heap = *q`（权重 +1）不会让 q 自身逃逸（传播要求 derefs <= 0）",
          not g.loc("q").escapes)
    g2 = EscapeGraph()
    g2.assign(HEAP, "q", 0)                 # heap = q（权重 0）
    g2.solve()
    check("B4b 权重从 +1 改成 0（`heap = q`）同一个 q 立刻逃逸",
          g2.loc("q").escapes)
    g = EscapeGraph()
    g.loc("h", kind=HEAP)              # h 是堆位置
    g.assign("h", "q", -1)             # h = &q
    g.solve()
    check("B5 但 `dst = &q`（权重 -1）会让 q 逃逸", g.loc("q").escapes)
    g = scenario_closure()
    check("B6 闭包逃逸 → 捕获变量随之逃逸", g.loc("captured").escapes)
    check("B7 闭包记录本身被标记为 closure", g.loc("clo").is_closure)
    g = scenario_interface_box()
    check("B8 装箱进 interface 且该接口值上堆 → 被装箱变量逃逸", g.loc("v").escapes)
    g = scenario_loop_local()
    check("B9 循环内只用不逃逸的临时变量留在栈上（每轮复用同一槽）",
          not g.loc("x").escapes and not g.loc("tmp").escapes)

    print("\n=== C. 过程间：parameter tag ===")
    g = scenario_param_tag()
    check("C1 实参传给 body 里存进堆的参数 → 逃逸",
          g.loc("a").escapes)
    check("C2 实参传给被作为结果返回的参数 → 逃逸", g.loc("b").escapes)
    check("C3 实参传给干净参数 → 留在栈上", not g.loc("c").escapes)
    check("C4 tag 记录了两处信息（leaks / results）",
          g.param_tags["sink"]["leaks"] == {0}
          and g.param_tags["identity"]["results"] == {0}
          and not g.param_tags["pure"]["leaks"])

    print("\n=== D. 内联预算闸 ===")
    check("D1 inlineMaxBudget = 80", BUDGET == 80)
    check("D2 一次调用 = 57（故默认最多内联一个调用：2×57=114 > 80）",
          COST_CALL == 57 and 2 * COST_CALL > BUDGET and COST_CALL <= BUDGET)
    check("D3 调用参数额外 17（可能暴露常量函数）", COST_PARAM_CALL == 17)
    check("D4 panic 只记 1（几乎不惩罚）", COST_PANIC == 1)
    check("D5 throw 直接吃满 80 预算（官方结论：内联 runtime.throw 无收益）",
          COST_THROW == BUDGET)
    ok, why = can_inline(nodes=80)
    check("D6 恰好 80 个节点 → 可内联", ok, why)
    ok, why = can_inline(nodes=81)
    check("D7 81 个节点 → 超预算，拒内联", not ok, why)
    check("D8 拒绝理由文案与官方一致",
          why == "function too complex: cost 81 exceeds budget 80", why)
    ok, _ = can_inline(nodes=23, calls=1)
    check("D9 23 节点 + 1 次调用 = 80 → 仍可内联", ok)
    ok, why = can_inline(nodes=24, calls=1)
    check("D10 24 节点 + 1 次调用 = 81 → 拒内联", not ok, why)
    ok, why = can_inline(nodes=30, param_calls=3)
    check("D11 30 节点 + 3 次参数调用 = 81 → 拒内联", not ok, why)
    ok, why = can_inline(nodes=30, param_calls=2)
    check("D12 30 节点 + 2 次参数调用 = 64 → 可内联", ok, why)
    ok, why = can_inline(nodes=5, throws=1)
    check("D13 单个 throw 就让预算归零", not ok, why)
    ok, _ = can_inline(nodes=70, panics=5)
    check("D14 5 个 panic 只加 5（70+5 = 75 ≤ 80）", ok)
    ok, why = can_inline(nodes=100)
    check("D15 -l=0 完全关闭内联", not ok, why)
    ok, why = can_inline(nodes=10, is_leaf=False)
    check("D16 -l=1（默认）只内联叶子函数", not ok, why)
    ok, why = can_inline(nodes=10, is_leaf=False, debug_l=4)
    check("D17 -l=4 允许非叶子函数（10 节点 ≤ 80）", ok, why)

    print("\n=== E. 结构闸与特殊预算 ===")
    for b in ("closure", "defer", "recover", "go", "select", "go:noinline",
              "go:uintptrescapes", "no_body"):
        ok, why = can_inline(nodes=1, blockers=[b])
        check("E 阻断项 %-20s → 不可内联" % b, not ok, why)
    ok, why = can_inline(nodes=21, caller_nodes=BIG_FUNC_NODES)
    check("E9 调用方 ≥ 5000 节点（big function）→ 预算压到 20，21 节点越界",
          not ok, why)
    ok, why = can_inline(nodes=20, caller_nodes=BIG_FUNC_NODES)
    check("E10 big function 里 20 节点仍可内联", ok, why)
    ok, why = can_inline(nodes=19, caller_nodes=BIG_FUNC_NODES - 1)
    check("E11 caller 差一个节点未达 big 阈值 → 用常规 80 预算", ok, why)
    ok, why = can_inline(nodes=700, closure_called_once=True)
    check("E12 闭包只被调用一次 → 预算放宽到 800，700 节点可内联", ok, why)
    ok, why = can_inline(nodes=801, closure_called_once=True)
    check("E13 801 节点越界 → 拒内联", not ok, why)
    check("E14 hot 函数预算 2000（PGO 时预算可扩到 2000）", HOT_BUDGET == 2000)

    print("\n=== F. 内联为什么能改变逃逸结论 ===")
    # 未内联：被调函数是黑盒，只有 parameter tag 可用；若 tag 显示「参数入堆」，实参只能逃逸。
    g1 = EscapeGraph()
    g1.define_func("helper", 1, body_leaks=[0])
    g1.call("helper", ["x"])
    g1.solve()
    # 内联后：helper 的函数体可见，`heap = 1` 这种常量传播让分配留在栈上。
    g2 = EscapeGraph()
    g2.loc("helper_body_temp")
    g2.notes.append("helper 体已内联，实参未被存进任何逃逸位置")
    g2.solve()
    check("F1 未内联：黑盒调用 + tag=leaks → 实参 x 逃逸", g1.loc("x").escapes)
    check("F2 内联后：函数体可见 → 同一实参留栈", not g2.loc("helper_body_temp").escapes)
    check("F3 go:uintptrescapes 阻止内联的原因就是「内联会丢逃逸信息」",
          "go:uintptrescapes" in BLOCKERS)
    costs = [inline_cost(nodes=n)[0] for n in (10, 20, 40, 79)]
    check("F4 代价对节点数单调不减，且 79 节点仍在 80 预算内",
          costs == sorted(costs) and costs[-1] == 79, costs)

    print("\n" + "=" * 62)
    print("断言总数 %d；失败 %d %s" % (STATS["n"], len(STATS["fail"]), STATS["fail"] or "（全绿）"))
    print("=" * 62)
    return 1 if STATS["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
