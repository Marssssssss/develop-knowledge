#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""autoformat.py 自检脚本（实际运行）：污点规则 / 字段树 / 并行与顺序字段。"""

from autoformat import (Monitor, Rec, dedup, build_field_tree, mark_parallel,
                        sequential_fields, history_of, similar, render,
                        H_SIMILAR)

SSCANF = ("main", "read_header", "sscanf")
COPY_M = SSCANF + ("copy_method",)
COPY_U = SSCANF + ("copy_uri",)
COPY_V = SSCANF + ("copy_version",)
SGETS = ("main", "read_header", "sgets")
PARSE = ("main", "read_header", "parse_hdr")

LINE1 = b"GET /news.html HTTP/1.0\r\n"
H1 = b"User-Agent: Wget/1.10.2\r\n"
H2 = b"Accept: */*\r\n"
H3 = b"Host: 1.2.3.4\r\n"
MSG = LINE1 + H1 + H2 + H3
o1 = len(LINE1)
o2 = o1 + len(H1)
o3 = o2 + len(H2)

SPANS = [
    (0, 3, COPY_M),                     # Method
    (4, 14, COPY_U),                    # Request-URI
    (15, 23, COPY_V),                   # HTTP-Version
    (o1, o2, SGETS), (o1, o1 + 12, PARSE),      # header 1 + 关键字解析
    (o2, o3, SGETS), (o2, o2 + 8, PARSE),       # header 2
    (o3, len(MSG), SGETS), (o3, o3 + 6, PARSE)  # header 3
]


def _check(label, cond, detail=""):
    assert cond, "FAIL %s %s" % (label, detail)
    print("  ok  %-52s %s" % (label, detail))


def build_log():
    mon = Monitor(MSG)
    for s, e, stack in SPANS:
        mon.mark_input("buf", range(s, e))
        mon.read("buf", stack)
    return mon.log


def main():
    print("== §3.1 污点传播规则 ==")
    mon = Monitor(b"ABCD")
    mon.mark_input("m", {0, 1})
    mon.mov("r1", "m")
    _check("mov：源被标记 → 目的继承同一组偏移",
            mon.taint.get("r1") == frozenset({0, 1}), str(sorted(mon.taint.get("r1", ()))))
    mon.mov("r2", "clean")
    _check("mov：源未标记 → 取消目的标记", "r2" not in mon.taint, "")
    mon.taint["a"], mon.taint["b"] = frozenset({2}), frozenset({3})
    mon.arith("d", "a", "b")
    _check("算术/逻辑指令：两个被标记操作数 → 标注取并集",
            mon.taint["d"] == frozenset({2, 3}), str(sorted(mon.taint["d"])))
    before = len(mon.log)
    mon.read("r1", ("main",))
    _check("读被标记内存才落记录（每条偏移一条）",
            len(mon.log) - before == 2, "+%d" % (len(mon.log) - before))

    print("== §3.2.1 预处理与字段树生成（Algorithm 1）==")
    log = build_log()
    dup = Monitor(MSG)
    dup.mark_input("buf", {7})
    dup.read("buf", COPY_U)
    dup.read("buf", COPY_U)          # 连续两次完全相同的读
    _check("连续相同记录被去重（%d → %d）" % (len(dup.log), len(dedup(dup.log))),
            len(dup.log) == 2 and len(dedup(dup.log)) == 1, "")
    tree = build_field_tree(log, len(MSG))
    top = [(c["lo"], c["hi"]) for c in tree["children"]]
    _check("顶层 6 个节点：Method/URI/Version + 3 个 header",
            top == [(0, 3), (4, 14), (15, 23), (o1, o2), (o2, o3), (o3, len(MSG))], str(top))
    _check("最后一段被冲刷（论文 Algorithm 1 伪代码漏了这一步）",
            any(c["lo"] == o3 and c["hi"] == len(MSG) for c in tree["children"]), "")
    hdr1 = [c for c in tree["children"] if c["lo"] == o1][0]
    _check("header 1 内部出现层次节点（关键字解析区间成为子节点）",
            len(hdr1["children"]) == 1 and hdr1["children"][0]["hi"] == o1 + 12,
            str([(c["lo"], c["hi"]) for c in hdr1["children"]]))

    print("== §3.2.2 并行字段识别（Algorithm 2，h=%d）==" % H_SIMILAR)
    _check("Method/URI 历史首个栈帧就不同（copy_method vs copy_uri）→ 共享前缀 0",
            not similar(history_of(log, 0), history_of(log, 4)),
            "%s vs %s" % (history_of(log, 0)[0][-1], history_of(log, 4)[0][-1]))
    _check("三个 header 历史完全相同 → 判为并行",
            similar(history_of(log, o1), history_of(log, o2)) and
            similar(history_of(log, o2), history_of(log, o3)), "")
    A, B, C = ("a",), ("a", "b"), ("a", "b", "c")
    h75a = [A, B, C, ("a", "b", "c", "d1")]
    h75b = [A, B, C, ("a", "b", "c", "d2")]
    _check("阈值敏感性：共享前缀 3/4 = 75% 在 h=80 下不相似",
            not similar(h75a, h75b), "")
    _check("阈值敏感性：同一对历史在 h=70 下变成相似（h 直接决定误判）",
            similar(h75a, h75b, h=70), "")
    tree = mark_parallel(tree, log)
    pars = [c for c in tree["children"] if c["parallel"]]
    _check("恰好生成 1 个并行字段节点，覆盖 [o1, len)",
            len(pars) == 1 and pars[0]["lo"] == o1 and pars[0]["hi"] == len(MSG),
            str([(p["lo"], p["hi"]) for p in pars]))
    _check("并行字段含 3 个候选 header", len(pars[0]["children"]) == 3,
            str(len(pars[0]["children"])))
    _check("Method/URI/Version 未被并入并行字段",
            len(tree["children"]) == 4, str([(c["lo"], c["hi"]) for c in tree["children"]]))

    print("== §3.2.2 顺序字段与 BNF 骨架 ==")
    lists = sequential_fields(tree)
    _check("顶层顺序字段 = 4 个（3 个请求行字段 + 并行字段）",
            [ (n["lo"], n["hi"]) for n in lists[0] ] ==
            [(0, 3), (4, 14), (15, 23), (o1, len(MSG))],
            str([(n["lo"], n["hi"]) for n in lists[0]]))
    _check("并行字段内部再走一遍 → 3 项；候选下沉到各 header 的关键字子字段"
           "（header 行本身不是叶子，故被跳过）",
           len(lists[1]) == 3 and lists[1][0]["hi"] == o1 + 12,
           str([(n["lo"], n["hi"]) for n in lists[1]]))
    print("  ---- 推断出的字段树 ----")
    for ln in render(tree):
        print("  " + ln)
    print("AutoFormat: ALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
