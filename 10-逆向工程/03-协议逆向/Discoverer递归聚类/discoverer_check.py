#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""discoverer.py 自检脚本（实际运行）：逐节核对 Discoverer 三阶段行为。"""

from discoverer import (tokenize, token_pattern, infer_format, find_fd,
                        recursive_cluster, can_merge, mismatch_count,
                        MAX_PREFIX, TEXT_MIN, B, T)
from discoverer_fixture import FAMILY_A, FAMILY_A_ODD, FAMILY_B


def _check(label, cond, detail=""):
    assert cond, "FAIL %s %s" % (label, detail)
    print("  ok  %-52s %s" % (label, detail))


def main():
    print("== §3.2.1 标识化 ==")
    t = tokenize(FAMILY_A[0])
    _check("magic 4 字节 → 4 个 binary token", [c for c, _, _ in t[:4]] == [B] * 4,
           "".join(c for c, _, _ in t))
    _check("body 'HELLO'(5 可打印字节) → 1 个 text token",
           t[7][0] == T and t[7][1] == b"HELLO", repr(t[7][1]))
    _check("共 8 个 token（7 binary + 1 text）", len(t) == 8, str(len(t)))
    t6 = tokenize(FAMILY_A_ODD[0])
    _check("body 'OK' 仅 2 字节 < TEXT_MIN=%d → 退化为 2 个 binary token" % TEXT_MIN,
           [c for c, _, _ in t6] == [B] * 9, "".join(c for c, _, _ in t6))
    u = tokenize(b"\x01\x00" + b"A\x00B\x00C\x00" + b"\x02")
    _check("UTF-16LE 段被识别为 1 个 text token",
           any(c == T and v == b"A\x00B\x00C\x00" for c, v, _ in u), "")
    big = tokenize(b"\x00" * 5000)
    _check("长报文被截到 MAX_PREFIX=%d" % MAX_PREFIX,
           len(big) == MAX_PREFIX and max(o for _, _, o in big) == MAX_PREFIX - 1,
           "len=%d" % len(big))

    print("== §3.2.2 按 token 模式初始聚类 ==")
    pa = token_pattern("C2S", tokenize(FAMILY_A[0]))
    pb = token_pattern("C2S", tokenize(FAMILY_A_ODD[0]))
    pc = token_pattern("S2C", tokenize(FAMILY_A[0]))
    _check("同格式同方向 → 同一 token 模式",
           pa == ("C2S", B, B, B, B, B, B, B, T), str(pa))
    _check("'OK' 短文本导致 token 模式不同（过度分类的来源）", pa != pb, str(pb))
    _check("方向参与模式 → 反向报文必分到不同簇", pa != pc, str(pc))

    print("== §3.3.1 格式推断 ==")
    fmt = infer_format(FAMILY_A)
    _check("magic 4 字节全常量", all(fmt[i]["const"] for i in range(4)), "")
    _check("cmd 是变量（2 个取值）", not fmt[4]["const"], str(sorted(fmt[4]["values"])))
    _check("token[5:7] 被判为 length 字段",
           fmt[5]["sem"] == "length" and fmt[6]["sem"] == "length", "")
    _check("body 既非常量也无语义", not fmt[7]["const"] and fmt[7]["sem"] == "", "")
    fb = infer_format(FAMILY_B)
    _check("家族 B 的 token[1:3] 被判为 offset 字段",
           fb[1]["sem"] == "offset" and fb[2]["sem"] == "offset",
           "sems=%s" % [x["sem"] for x in fb])
    _check("offset 不是 length（值差 -6 ≠ 报文长度差 -10）",
           all(x["sem"] != "length" for x in fb), "")

    print("== §3.3.3 递归聚类（FD 三判据）==")
    k = find_fd(FAMILY_A)
    _check("FD token 落在下标 4（cmd，仅 2 个取值）", k == 4, "k=%s" % k)
    clusters = recursive_cluster(FAMILY_A)
    _check("按 cmd 切成 2 个子簇", len(clusters) == 2, str([len(c) for c in clusters]))
    _check("子簇内 cmd 各自变常量",
           all(infer_format(c)[4]["const"] for c in clusters), "")
    _check("同一子簇内已无 FD（cmd 变常量后无 token 能再切）",
           find_fd(FAMILY_A[:3]) is None, "")

    print("== §3.4 基于类型的序列比对合并 ==")
    f_main = infer_format(FAMILY_A[:3])
    f_odd = infer_format(FAMILY_A_ODD)
    ok, pairs = can_merge(f_main, f_odd)
    _check("'OK' 簇与主流簇可合并（2 个 binary 对 gap ≤ text size 5）", ok, "")
    _check("失配恰为 1 处（len 低字节取值集 {05,03,08} 与 {02} 不相交）"
           "—— §3.4 允许至多 1 处失配仍合并",
           mismatch_count(pairs, f_main, f_odd) == 1,
           "mm=%d" % mismatch_count(pairs, f_main, f_odd))
    ok2, _ = can_merge(f_main, infer_format(FAMILY_B))
    _check("异构格式不可合并（class 序列 B*7+T vs B*3+T*4）", not ok2, "")
    print("Discoverer: ALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
