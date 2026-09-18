#!/usr/bin/env python3
"""taint_analysis.py 自检：断言三档精度的判定差异与 TRUTH 表一致。

运行：python taint_analysis_selftest.py
"""

import taint_analysis as T

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL: %s %s" % (label, detail))


def main():
    res = T.run_all()
    progs = T.programs()

    # ---- 1. 误报集 / 漏报集（TRUTH 是"应然"，三档分析的**偏离**才是有意义的断言）----
    #     误报 FP = 报了但 truth 为干净；漏报 FN = 没报但 truth 有漏洞
    def fp_fn(key):
        fp, fn = set(), set()
        for n, want in T.TRUTH.items():
            got = set(res[n][key])
            want_s = set(want)
            if got - want_s:
                fp.add(n)
            if want_s - got:
                fn.add(n)
        return fp, fn

    for key, want_fp, want_fn in (
        ("insensitive/taint", {"P1_read_before_write", "P4_correlated_flag"},
         {"P5_implicit_flow"}),
        ("sensitive/taint", {"P4_correlated_flag"}, {"P5_implicit_flow"}),
        ("path/taint", set(), {"P5_implicit_flow"}),
    ):
        got_fp, got_fn = fp_fn(key)
        check(key + "/误报集", got_fp == want_fp,
              "got=%s want=%s" % (sorted(got_fp), sorted(want_fp)))
        check(key + "/漏报集", got_fn == want_fn,
              "got=%s want=%s" % (sorted(got_fn), sorted(want_fn)))

    # 顺序敏感后 P1 的误报消失，P4 仍在
    check("P1/flow-sensitive 消除先读后写误报",
          res["P1_read_before_write"]["sensitive/taint"] == [])
    check("P4/flow-sensitive 仍有误报（分支相关性丢失）",
          res["P4_correlated_flag"]["sensitive/taint"] == ["S"])
    check("P4/path-sensitive 消除误报",
          res["P4_correlated_flag"]["path/taint"] == [])
    check("P4b/path-sensitive 仍能报出真阳性",
          res["P4b_broken_correlation"]["path/taint"] == ["S"])

    # ---- 2. DataFlow vs TaintTracking：非保值步骤 ----
    check("P2/DataFlow 漏报（拼接非保值）",
          res["P2_concat"]["sensitive/flow"] == [])
    check("P2/TaintTracking 命中",
          res["P2_concat"]["sensitive/taint"] == ["S"])

    # ---- 3. barrier（isSink 前被 isBarrier 拦掉）----
    check("P3/sanitizer 是 barrier", res["P3_sanitizer"]["path/taint"] == [])

    # ---- 4. 隐式流：数据经控制流传递，三档全部漏报 ----
    for k in ("insensitive/taint", "sensitive/taint", "path/taint"):
        check("P5/隐式流漏报@" + k, res["P5_implicit_flow"][k] == [])
    check("P5/TRUTH 认定这是真漏洞（即漏报）",
          T.TRUTH["P5_implicit_flow"] == ["S"])

    # ---- 5. 精度阶梯是单调的：越精确，报告数不增 ----
    fp = sum(1 for n in T.TRUTH if res[n]["insensitive/taint"])
    sp = sum(1 for n in T.TRUTH if res[n]["sensitive/taint"])
    pp = sum(1 for n in T.TRUTH if res[n]["path/taint"])
    check("报告数单调递减 insensitive>=sensitive>=path",
          fp >= sp >= pp, "%d/%d/%d" % (fp, sp, pp))
    check("三档并非全等（说明精度确实有差别）", fp > pp, "%d vs %d" % (fp, pp))

    # ---- 6. flow-insensitive 的并集语义（防振荡回归）----
    conflict = [("let", "x", ("source",)), ("let", "x", ("const",))]
    check("同一变量冲突赋值时不振荡、取并集",
          T.flow_insensitive(conflict, "taint") is not None)

    # ---- 7. 表达式求值细节 ----
    env = {"x": True}
    check("copy 传播污点", T.eval_expr(("copy", "x"), env, {}, "taint") is True)
    check("const 干净", T.eval_expr(("const",), env, {}, "taint") is False)
    check("sanitize 恒干净",
          T.eval_expr(("sanitize", ("copy", "x")), env, {}, "taint") is False)

    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
