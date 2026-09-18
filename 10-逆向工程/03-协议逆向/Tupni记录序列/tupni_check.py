#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tupni.py 自检脚本（实际运行）。夹具模拟 Tupni 论文 §3.2 的解析器：

    1  num_records = *(ULONG*)input          # [0,4)
    2  while (i < num_records) {             # 循环入口，每次迭代都重读计数
    3      record_type = *(USHORT*)(input+offset)   # B
    4      record_size = *(USHORT*)(input+offset+2) # C
    5      call record_hdlr[record_type]            # D / H1 / H5
    6      offset += record_size; i++
    7  }
"""

from tupni import (build_chunks, greedy_packing, virtual_fields, split_iterations,
                   iteration_dependent, find_record_boundaries, qi_of,
                   collapse_child_loops, single_value_constraints,
                   functional_constraints, determine_length)

A, B, C, D = "L7_cmp", "L9_type", "L11_size", "L12_hdlr"
H1, H1A, H5 = "hdlr1", "hdlr1_loop", "hdlr5"
A_ENTRY = A

# 报文：num_records(4B) + 3 条记录，每条 type(2B) + size(2B) + payload
R1 = (4, 16)      # type[4,6) size[6,8) payload[8,16)
R2 = (16, 24)     # type[16,18) size[18,20) payload[20,24)
R3 = (24, 32)     # type[24,26) size[26,28) payload[28,32)
MSGLEN = 32

# 循环入口 A 出现在**每次迭代的开头**（末次只有它 —— 论文：末次往往不是真迭代）
TRACE = [
    (A, [0, 1, 2, 3]),
    (B, [4, 5]), (C, [6, 7]), (H1, [8, 9, 10, 11]), (D, list(range(8, 16))),
    (A, [0, 1, 2, 3]),
    (B, [16, 17]), (C, [18, 19]), (H5, [20, 21, 22, 23]), (D, list(range(20, 24))),
    (A, [0, 1, 2, 3]),
    (B, [24, 25]), (C, [26, 27]), (H5, [28, 29, 30, 31]), (D, list(range(28, 32))),
    (A, [0, 1, 2, 3]),
]
# 干扰：整包批量访问（memcpy / 校验和）与优化过的 32 位字符串处理
TRACE += [("bulk", list(range(0, MSGLEN))), ("str32", [8, 9, 10, 11]),
          ("str32", [8, 9, 10, 11])]


def _check(label, cond, detail=""):
    assert cond, "FAIL %s %s" % (label, detail)
    print("  ok  %-52s %s" % (label, detail))


def main():
    print("== §3.3 chunk 识别与权重 ==")
    w = build_chunks(TRACE)
    _check("num_records 被循环条件读了 4 次 → chunk (0,4) 权重 4",
            w[(0, 4)] == 4, "w=%d" % w[(0, 4)])
    _check("批量访问产生覆盖整包的 chunk (0,32)，权重 1",
            w[(0, MSGLEN)] == 1, "")
    _check("优化字符串处理产生与 payload 重叠的 chunk (8,12)，权重 3",
            w[(8, 12)] == 3, "w=%d" % w[(8, 12)])
    _check("一条指令里不连续的两段会拆成两个 chunk",
            (0, 2) in build_chunks([("x", [0, 1, 7, 8])]) and
            (7, 9) in build_chunks([("x", [0, 1, 7, 8])]), "")

    print("== §3.3.1 加权 Maximum k-Set Packing（贪心）==")
    fields = greedy_packing(w)
    _check("贪心选中高权重的 (8,12)，挤掉了与之重叠的 (8,16)",
            (8, 12) in fields and (8, 16) not in fields, str(fields))
    _check("贪心选中权重 4 的 (0,4)，挤掉覆盖整包的 (0,32)",
            (0, 4) in fields and (0, MSGLEN) not in fields, "")
    _check("选出的字段互不重叠",
            all(fields[i][1] <= fields[i + 1][0] for i in range(len(fields) - 1)), "")
    vf = virtual_fields(fields, MSGLEN)
    gaps = [f for f in vf if f[2]]
    _check("未被访问的区间被补成 virtual field", len(gaps) >= 1,
           str([(g[0], g[1]) for g in gaps]))
    _check("virtual field 覆盖 [12,16)（payload 后 4 字节没被读）",
            (12, 16) in [(g[0], g[1]) for g in gaps], "")

    print("== §3.4.1-§3.4.2 循环识别与迭代相关指令 ==")
    iters = split_iterations(TRACE[:-3], A_ENTRY)
    _check("按入口点切成 4 段迭代", len(iters) == 4, str(len(iters)))
    I, n = iteration_dependent(iters, fields)
    _check("末次迭代 I_n 为空 → 迭代数 n 由 4 降到 3", n == 3, "n=%d" % n)
    _check("A（循环条件）不是迭代相关指令（每次都读同一字段）",
            A not in I[0], str(sorted(I[0])))
    _check("B/C/D 都是迭代相关指令", B in I[0] and C in I[0] and D in I[0],
           str(sorted(I[0])))
    _check("循环是 iteration dependent（I_i 对 i<n 全非空）",
            all(I[i] for i in range(n - 1)), "")

    print("== §3.4.3 Figure 4 记录边界 ==")
    s, e = find_record_boundaries(n, I, fields, iters)
    _check("s = [4, 16, 24]", s == [4, 16, 24], str(s))
    _check("e = [15, 23, 32]", e == [15, 23, 32], str(e))
    # 论文 Figure 4 第 17 行 e_j = s_{j+1} - 1 给的是**闭区间末字节**，
    # 第 18 行 e_n = Offset+sizeof 给的却是**开区间右边界**，两者口径差 1
    _check("Figure 4 的 e 口径不一致：前 n-1 个是闭区间、末个是开区间",
            e[0] + 1 == s[1] and e[2] == 32, "e0=%d e2=%d" % (e[0], e[2]))
    recs = [(s[i], e[i] + 1) for i in range(n - 1)] + [(s[n - 1], e[n - 1])]
    _check("换算回半开区间后记录区间与真值一致", recs == [R1, R2, R3], str(recs))

    print("== §3.5 记录类型（子循环折叠成虚指令）==")
    q1 = qi_of(iters[0], fields, R1)
    q2 = qi_of(iters[1], fields, R2)
    q3 = qi_of(iters[2], fields, R3)
    _check("Q1 含 hdlr1、Q2/Q3 含 hdlr5 → Q1 ≠ Q2",
            q1 != q2, "%s vs %s" % (q1, q2))
    _check("Q2 == Q3 → 记录 2、3 同类型", q2 == q3, str(q2))
    raw = ["a", "x", "x", "x", "b"]
    _check("折叠：连续子循环段被压成一条虚指令 ('V', 'L1')",
            collapse_child_loops(raw, [(1, 4, "L1")]) == ["a", ("V", "L1"), "b"],
            str(collapse_child_loops(raw, [(1, 4, "L1")])))

    print("== §3.4.4 长度决定方式 ==")
    _check("循环条件依赖字段 → case (b) 长度字段",
            determine_length(3, [], {(0, 4)}) == "b", "")
    _check("前 n-1 次比较失败、第 n 次成功 → case (a) 终止记录",
            determine_length(3, [(1, 0, False), (2, 0, False), (3, 0, True)], set()) == "a",
            "")
    _check("两者都没有 → case (c) 隐式固定长度",
            determine_length(3, [], set()) == "c", "")

    print("== §3.6 约束 ==")
    preds = [("len", "<", 4096, True), ("len", "<", 4096, True),
             ("mix", "<", 7, False)]
    _check("单值约束只保留「只依赖输入与硬编码常量」的谓词",
            len(single_value_constraints(preds)) == 2, "")
    _check("函数式约束刻画 input[x] = f(input[y], input[z])",
            functional_constraints([], [("csum", ["f1", "f2"])]) ==
            [("csum", ("f1", "f2"))], "")
    print("Tupni: ALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
