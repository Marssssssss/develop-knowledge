#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""reformat.py 自检脚本（实际运行）。

夹具模拟论文 §3.3 的 shttpd 例子：解密阶段以 sha1_block_asm_data_order 收尾，
随后进入 HMAC_Init_ex 开始的正常协议处理阶段，末尾还有一次**输出加密**——
正是这次输出加密让「累计百分比」不能直接用作判据，必须走两步。
"""

from reformat import (TABLE1, DECRYPT_NAMES, THRESHOLD, pct, cumulative_pct,
                      function_fragments, phase_profile, data_lifetime)

# (函数名, 指令条数, 其中算术与位运算的条数)
FRAGS = [
    ("ssl3_read_bytes", 40, 26),
    ("EVP_DecryptUpdate", 30, 27),
    ("AES_decrypt", 60, 55),
    ("sha1_block_asm_data_order", 50, 48),     # ← 真正的解密收尾
    ("HMAC_Init_ex", 30, 5),
    ("http_parse_request", 80, 8),
    ("build_response", 40, 4),
    ("AES_encrypt", 60, 55),                   # ← 输出加密，AB 占比同样很高
]


def _check(label, cond, detail=""):
    assert cond, "FAIL %s %s" % (label, detail)
    print("  ok  %-52s %s" % (label, detail))


def build_itrace():
    """展开成指令流；每条指令记 (func, 栈帧 id, 是否算术/位运算)。"""
    tr, sid = [], 0
    for name, n, nab in FRAGS:
        sid += 1
        for i in range(n):
            tr.append((name, sid, i < nab))    # 每段前 nab 条是算术/位运算
    return tr


def main():
    print("== Table 1：解密 vs 明文处理的指令分布 ==")
    dec = [pct(ab, tot) for nm, ab, tot in TABLE1 if nm in DECRYPT_NAMES]
    plain = [pct(ab, tot) for nm, ab, tot in TABLE1 if nm not in DECRYPT_NAMES]
    _check("4 种解密算法的算术+位运算占比全部 > 80%", min(dec) > 80, "min=%.2f" % min(dec))
    _check("6 种明文处理程序的占比全部 < 25%", max(plain) < 25, "max=%.2f" % max(plain))
    _check("AES 实测 6892/8475 = %.2f%%" % pct(6892, 8475),
            abs(pct(6892, 8475) - 81.32) < 0.01, "")
    _check("BMP 是明文侧最高者 229/956 = %.2f%%" % pct(229, 956),
            abs(pct(229, 956) - 23.95) < 0.01, "")

    print("== §3.3 相位划分器 ==")
    tr = build_itrace()
    cum = cumulative_pct(tr)
    trans, imax, imin = phase_profile(tr)
    _check("累计百分比的最大值出现在解密阶段（首条指令即算术指令）",
            imax == 0, "imax=%d" % imax)
    _check("累计百分比的最小值落在处理阶段（早于末尾的输出加密）",
            FRAGS[3][0] != "AES_encrypt" and imin > 180, "imin=%d" % imin)
    _check("跃迁片段 = sha1_block_asm_data_order（与论文手工分析一致）",
            trans["func"] == "sha1_block_asm_data_order",
            trans["func"] if trans else "None")
    _check("跃迁点 = 该片段最后一条指令 = 179",
            trans["end"] - 1 == 179, "%d" % (trans["end"] - 1))
    _check("片段级百分比正确：sha1 段 48/50 = 96%",
            abs(trans["pct"] - 96.0) < 1e-9, "%.2f" % trans["pct"])
    frags = function_fragments(tr)
    _check("函数片段把 AES_encrypt 与解密段切成不同片段（栈帧不同）",
            len(frags) == len(FRAGS), str(len(frags)))
    same = [phase_profile(tr, t)[0]["func"] for t in (25, 50, 80)]
    _check("阈值取 25/50/80 得到同一个跃迁片段（正文：25%~80% 皆可）",
            len(set(same)) == 1 and same[0] == "sha1_block_asm_data_order", str(same))
    _check("阈值高过所有解密片段（97%）时无片段达标 → 阈值必须落在实测分布之内",
            phase_profile(tr, 97)[0] is None,
            str(phase_profile(tr, 97)[0]))

    print("== §3.4 数据生命周期分析 ==")
    T = trans["end"] - 1
    ops = [
        (10, "alloc", "A"), (12, "write", "A"),
        (20, "alloc", "B"), (22, "write", "B"), (30, "free", "B"),   # 进入阶段 2 前已释放
        (40, "alloc", "C"), (42, "write", "C"),
        (50, "alloc", "D"), (52, "write", "D"),
        (60, "alloc", "E"),                                          # 从未被写
        (200, "read", "C"), (210, "read", "A"),                      # 顺序：先 C 后 A
        (220, "alloc", "F"), (222, "read", "F"),                     # 阶段 2 新建
        (230, "write", "D"),                                         # 被写 → 立即失效
    ]
    ws, rs, ans = data_lifetime(ops, T)
    _check("write set = {A, C, D}（B 已释放、E 未被写）",
            ws == {"A", "C", "D"}, str(sorted(ws)))
    _check("read set = {C, A, F}（F 是阶段 2 新建，也会被读）",
            rs == {"C", "A", "F"}, str(sorted(rs)))
    _check("交集按首次读取时序排序 → [C, A]", ans == ["C", "A"], str(ans))
    _check("多条命中时被当作一个虚拟缓冲区（论文 §3.4）", len(ans) == 2, "")
    ops2 = [o for o in ops if not (o[1] == "read" and o[2] == "C")]
    _, _, ans2 = data_lifetime(ops2, T)
    _check("只命中一条时直接作为明文缓冲区", ans2 == ["A"], str(ans2))
    ops3 = [o for o in ops if o[2] != "D"] + [(231, "read", "D")]
    _, _, ans3 = data_lifetime(ops3, T)
    _check("被写失效的 D 即使之后再读也不进交集", "D" not in ans3, str(ans3))
    print("ReFormat: ALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
