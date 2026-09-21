"""BinPRE 语义精化的自检。

E1~E6 NW_f 与格式聚类（Algorithm 2）；E7~E10 熵与类型精化（§3.5.2）；
E11~E12 Table 3 的功能-类型约束与端到端。

断言基于实读 arXiv 2409.01994（BinPRE, CCS'24）§3.5 与 Table 3。

运行：python selfcheck_refine.py
"""

import sys

from main import (FORMATS, FUNCTION_TYPE_CONSTRAINT, GAP_SCORE, MA_SCORE, MSG,
                  MISMA_SCORE, align_score, cluster_by, entropy_type_refinement,
                  explore_optimal, fill_unknown_by_nearest_entropy,
                  function_refinement, median, nw_format, shannon_entropy)

PASS = [0]
FAIL = [0]


def ok(cond, msg):
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
        print("  FAIL:", msg)


def eq(got, want, msg):
    ok(got == want, "%s (got=%r want=%r)" % (msg, got, want))


def close(got, want, msg, tol=1e-9):
    ok(abs(got - want) < tol, "%s (got=%r want=%r)" % (msg, got, want))


# ------------------------------------------------------------ E1 常量

def e1_constants():
    eq((MA_SCORE, MISMA_SCORE, GAP_SCORE), (1, -1, -2), "E1 论文默认分值")


# ------------------------------------------------------------ E2~E4 NW_f

def e2_nw():
    eq(nw_format([0, 2, 4], [0, 2, 4]), 3 * MA_SCORE, "E2 完全相同 -> 长度×MA")
    eq(nw_format([], []), 0, "E2 两个空序列得 0")
    eq(nw_format([0], []), GAP_SCORE, "E2 一边为空 -> 全 gap")
    eq(nw_format([0, 1], [0, 9]), MA_SCORE + MISMA_SCORE, "E2 一处不匹配")
    eq(nw_format([0, 1], [9, 0]), nw_format([9, 0], [0, 1]), "E2 对称")


def e3_nw_gap_tradeoff():
    """全部换掉 vs 打两个空位：长度 1 对长度 3 时只能靠 gap。"""
    eq(nw_format([5], [1, 2, 3]), MISMA_SCORE + 2 * GAP_SCORE,
       "E3 一匹配不上加两个空位")
    ok(nw_format([1, 2, 3], [1, 2, 3]) > nw_format([1, 2, 3], [1, 2, 9]),
       "E3 全对比一处不符得分更高")


def e4_nw_monotone():
    a = [0, 2, 4, 6, 8]
    ok(nw_format(a, a) > nw_format(a, [0, 2, 4, 6]) > nw_format(a, [0, 2, 4]),
       "E4 前缀越长得分越高（全命中时）")


# ------------------------------------------------------------ E5~E6 聚类

def e5_align_score():
    good = [[0, 1], [2], [3], [4]]
    bad = [[0, 2, 3, 4], [1]]
    ok(align_score(good, FORMATS) > align_score(bad, FORMATS),
       "E5 正确分簇的对齐分更高")
    eq(align_score([[0]], FORMATS), 0.0, "E5 单元素簇没有配对，分为 0")


def e6_explore():
    cl, pos, sc = explore_optimal(MSG, FORMATS)
    flat = sorted(i for c in cl for i in c)
    eq(flat, list(range(len(MSG))), "E6 分簇覆盖全部报文且不重不漏")
    lo, hi = pos
    ok(0 <= lo < hi <= min(len(m) for m in MSG), "E6 command 字段位置合法")
    ok(sc > 0, "E6 最优分为正")
    cl2 = cluster_by(MSG, lo, hi)
    eq(sorted(map(tuple, cl2)), sorted(map(tuple, cl)), "E6 分簇可复现")


# ------------------------------------------------------------ E7~E8 熵与中位数

def e7_entropy():
    close(shannon_entropy([1, 1, 1, 1]), 0.0, "E7 恒定字段熵为 0")
    close(shannon_entropy([0, 1]), 1.0, "E7 两种等概率 -> 1 bit")
    close(shannon_entropy([0, 1, 2, 3]), 2.0, "E7 四种等概率 -> 2 bit")
    close(shannon_entropy([0, 0, 0, 1]), 0.8112781244591328, "E7 3:1 分布的熵")


def e8_median():
    close(median([1, 2, 3]), 2.0, "E8 奇数个取中间")
    close(median([1, 2, 3, 4]), 2.5, "E8 偶数个取均值")
    close(median([5]), 5.0, "E8 单元素")


# ------------------------------------------------------------ E9~E10 类型精化

def e9_entropy_refine():
    fields = [("const", [b"a", b"a", b"a", b"a"]),
              ("var", [b"a", b"b", b"c", b"d"]),
              ("mid", [b"a", b"b", b"a", b"b"])]
    inferred = {"const": "Bytes", "var": "Static", "mid": "Integer"}
    out, ent, med = entropy_type_refinement(fields, inferred)
    ok("const" not in out, "E9 Bytes 但熵为 0（低于中位数）-> 撤掉")
    ok("var" not in out, "E9 Static 但熵最高（不低于中位数）-> 撤掉")
    eq(out.get("mid"), "Integer", "E9 Integer 不受熵判据影响")
    close(ent["const"], 0.0, "E9 恒定字段熵为 0")
    ok(ent["var"] > ent["mid"] > ent["const"], "E9 三字段熵的大小关系")


def e10_fill_unknown():
    fields = [("a", [b"1", b"2", b"3", b"4"]),
              ("b", [b"x", b"x", b"x", b"x"]),
              ("u", [b"p", b"q", b"p", b"q"])]
    inferred = {"a": "Bytes", "b": "Static"}
    ent = {n: shannon_entropy(v) for n, v in fields}
    out = fill_unknown_by_nearest_entropy(fields, inferred, ent)
    # u 的熵是 1.0，a 是 2.0、b 是 0.0 —— 距离相等，取遍历到的第一个（a）
    ok("u" in out, "E10 未知类型被补上")
    ok(out["u"] in ("Bytes", "Static"), "E10 补的是簇内已知类型之一")


# ------------------------------------------------------------ E11 Table 3

def e11_constraints():
    table = {"Command": "Group", "Length": "Integer", "Delim": "Static",
             "Checksum": "Integer", "Filename": "String", "Aligned": "Bytes"}
    eq(set(table), set(FUNCTION_TYPE_CONSTRAINT), "E11 六条约束齐备")
    for fn, good in table.items():
        kept, dropped = function_refinement({"f": good}, {"f": fn})
        eq(kept, {"f": fn}, "E11 %s + %s -> 保留" % (fn, good))
    for fn, good in table.items():
        bad = next(t for t in ("Integer", "Bytes", "Static", "Group", "String")
                   if t not in FUNCTION_TYPE_CONSTRAINT[fn])
        kept, dropped = function_refinement({"f": bad}, {"f": fn})
        eq(kept, {}, "E11 %s + %s -> 砍掉" % (fn, bad))
        eq(len(dropped), 1, "E11 被砍的功能进入 dropped")
    # Aligned 允许 Group 或 Bytes 两种
    for t in ("Group", "Bytes"):
        eq(function_refinement({"f": t}, {"f": "Aligned"})[0], {"f": "Aligned"},
           "E11 Aligned 接受 %s" % t)
    # 类型缺失（上游推断被撤）时功能也保不住
    eq(function_refinition_safe(), {}, "E11 类型为空 -> 功能被砍")


def function_refinition_safe():
    return function_refinement({}, {"f": "Length"})[0]


# ------------------------------------------------------------ E12 端到端

def e12_end_to_end():
    cl, pos, _sc = explore_optimal(MSG, FORMATS)
    fields = [("f0,2", [bytes(m[0:2]) for m in MSG]),
              ("f2,6", [bytes(m[2:6]) for m in MSG]),
              ("f6,7", [bytes(m[6:7]) for m in MSG])]
    inferred = {"f0,2": "Integer", "f2,6": "Bytes", "f6,7": "Static"}
    refined, ent, med = entropy_type_refinement(fields, inferred)
    ok("f2,6" not in refined, "E12 论文 Example-4：低熵字段的 Bytes 被撤")
    ok("f6,7" in refined, "E12 Static 且熵最低 -> 保留")
    fn = {"f0,2": "Length", "f2,6": "Checksum", "f6,7": "Delim"}
    kept, dropped = function_refinement(refined, fn)
    eq(kept, {"f0,2": "Length", "f6,7": "Delim"}, "E12 只剩类型对得上的功能")
    eq(dropped, [("f2,6", "Checksum", None)], "E12 Checksum 随类型一起被撤")


def main():
    for fn_ in (e1_constants, e2_nw, e3_nw_gap_tradeoff, e4_nw_monotone,
                e5_align_score, e6_explore, e7_entropy, e8_median,
                e9_entropy_refine, e10_fill_unknown, e11_constraints,
                e12_end_to_end):
        fn_()
    print("PASS=%d FAIL=%d" % (PASS[0], FAIL[0]))
    return 1 if FAIL[0] else 0


if __name__ == "__main__":
    sys.exit(main())
