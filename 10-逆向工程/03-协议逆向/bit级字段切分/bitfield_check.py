#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bitfield.py 自检脚本（实际运行）。

语料 = 4000 条「DNS 头标志位」，位布局严格照 RFC 1035 §4.1.1：

    bit0        QR
    bit1-4      Opcode      （RFC：0 QUERY / 1 IQUERY / 2 STATUS / 3-15 保留）
    bit5        AA
    bit6        TC
    bit7        RD
    bit8        RA
    bit9-11     Z           （RFC 原文：Reserved for future use. Must be zero）
    bit12-15    RCODE
"""

import random

from bitfield import (cardinality, varying_bits, constant_runs, segment,
                      byte_view, entropy_bits, NBITS)

N = 4000
SEED = 20260919


def _check(label, cond, detail=""):
    assert cond, "FAIL %s %s" % (label, detail)
    print("  ok  %-52s %s" % (label, detail))


def build_corpus():
    rnd = random.Random(SEED)
    out = []
    for _ in range(N):
        qr = rnd.randint(0, 1)
        opcode = rnd.choice([0, 0, 0, 1, 2])        # 刻意不含 3 → 非笛卡尔积
        aa = rnd.randint(0, 1)
        tc = 1 if rnd.random() < 0.1 else 0
        rd = 0 if rnd.random() < 0.15 else 1
        ra = rnd.randint(0, 1)
        z = 0                                        # RFC：必须为零
        rcode = rnd.choice([0, 1, 2, 3, 5])          # 刻意跳过 4/6/7
        flags = (qr << 15) | (opcode << 11) | (aa << 10) | (tc << 9) | \
                (rd << 8) | (ra << 7) | (z << 4) | rcode
        out.append(flags)
    return out


def main():
    msgs = build_corpus()

    print("== 逐位常量/变量剖面 ==")
    vb = varying_bits(msgs)
    const = sorted(set(range(NBITS)) - set(vb))
    _check("Z 字段（bit9-11）恒为 0 → 全是常量位",
            all(i in const for i in (9, 10, 11)), str(const))
    _check("Opcode 高位（bit1-2）在本语料里恒为 0 → 常量位",
            1 in const and 2 in const, str(const))
    _check("RCODE 最高位（bit12）恒为 0（取值未达 8）→ 常量位",
            12 in const, str(const))
    _check("变量位共 10 个", len(vb) == 10, str(vb))
    _check("常量位段 = [1,3) 与 [9,13)",
            constant_runs(set(const)) == [(1, 3), (9, 13)],
            str(constant_runs(set(const))))

    print("== 变量位段内的相关性切分 ==")
    blocks, cruns = segment(msgs)
    _check("bit0（QR）单独成块", (0, 1) in blocks, str(blocks))
    _check("bit3-4（Opcode 的变量部分）因非笛卡尔积被判为同一字段",
            (3, 5) in blocks, str(blocks))
    _check("bit5/6/7/8（AA/TC/RD/RA）各自独立成块",
            (5, 6) in blocks and (6, 7) in blocks and (7, 8) in blocks
            and (8, 9) in blocks, str(blocks))
    _check("bit13-15（RCODE 的低三位）被判为同一字段",
            (13, 16) in blocks, str(blocks))

    print("== bit 级能力边界（关键结论）==")
    _check("常量段 [9,13) 横跨 Z(3 位) 与 RCODE 最高位，统计上无法切分",
            (9, 13) in cruns, str(cruns))
    _check("→ 变量块 7 个 + 待定常量段 2 个 = 9 段，正好铺满 16 位",
            len(blocks) == 7 and len(cruns) == 2 and
            sum(b[1] - b[0] for b in blocks) + sum(r[1] - r[0] for r in cruns) == NBITS,
            "blocks=%d cruns=%d" % (len(blocks), len(cruns)))
    _check("常量位不携带任何统计信息：给它单独开窗基数恒为 1",
            cardinality(msgs, [9]) == 1 and cardinality(msgs, [12]) == 1, "")
    _check("即便换用信息论判据也一样失效：Z 段的熵为 0",
            abs(entropy_bits(msgs, [9, 10, 11])) < 1e-12, "")

    print("== 字节粒度对照 ==")
    bv = byte_view(msgs)
    _check("字节粒度只看到 2 个字段（每 8 位一个）", len(bv) == 2, str(bv))
    _check("字节 0 的取值基数 = 2×3×2×2×2 = 48（5 个子字段的笛卡尔积）",
            bv[0][1] == 48, "byte0=%d" % bv[0][1])
    _check("字节 1 的取值基数 = 2×5 = 10", bv[1][1] == 10, "byte1=%d" % bv[1][1])
    _check("位级切出 9 段 vs 字节粒度只有 2 段 —— 这就是位级切分要解决的问题",
            len(blocks) + len(cruns) == 9 and len(bv) == 2, "9 vs 2")

    print("== 反例：语料覆盖不足/过足都会翻车 ==")
    # 笛卡尔积语料：opcode 取满 0..15 → 联合基数 16 = 2*2*2*2 → 判为 4 个独立位
    full = [(qr << 15) | (op << 11) | (aa << 10)
            for qr in (0, 1) for op in range(16) for aa in (0, 1)]
    fb, _ = segment(full)
    _check("语料取满笛卡尔积时 opcode 被**过度切分**成 4 个 1 位字段",
            fb == [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6)], str(fb))
    _check("原因：card(bit1..4)=16 恰等于各 bit 基数之积 2^4 → 判据判定独立",
            cardinality(full, [1, 2, 3, 4]) == 16, "")
    print("bit级字段切分: ALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
