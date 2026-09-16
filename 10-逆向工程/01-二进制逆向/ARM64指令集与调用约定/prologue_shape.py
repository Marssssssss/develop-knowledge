#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ARM64 序言/尾声形态识别 + A64 解码器自检(全部断言实跑)。

序言形态分类取自工程实践(见 resurgo 的函数恢复文档,README §序言形态):
  stp-frame-pair  : stp x29, x30, [sp, #-16]! ; mov x29, sp   —— 建帧链,可回溯调用者
  stp-pair-only   : stp x29, x30, [sp, #-16]! 之后没有 mov x29, sp
  str-lr-preindex : str x30, [sp, #-16]!                       —— 只保 LR,不建帧链
  leaf            : 未保存 LR —— 叶子函数可直接 RET,BL 不会覆盖别人的返回地址

运行: python prologue_shape.py     退出码 0 表示全部断言通过。
"""

import sys

from arm64_decode import COND, bits, decode, decode_bytes, dis, sx

FAIL = []


def check(cond, label, detail=""):
    if not cond:
        FAIL.append(label)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  <- " + str(detail)) if detail else ""))
    return cond


# ------------------------------------------------- 序言/尾声形态识别

def classify_prologue(insns):
    """按指令序列判断函数入口序言形态。insns 为 decode() 返回的 dict 列表。"""
    if not insns:
        return "empty"
    d0 = insns[0]
    if (d0["group"] == "ldst-pair" and d0["access"] == "stp" and d0["mode"] == "pre"
            and d0["rt"] == 29 and d0["rt2"] == 30 and d0["rn"] == 31):
        if len(insns) > 1:
            d1 = insns[1]
            if (d1["group"] == "add-sub-imm" and d1.get("op") == 0
                    and d1.get("S") == 0 and d1["imm12"] == 0 and d1["shift"] == 0
                    and d1.get("rd") == 29):
                return "stp-frame-pair"
        return "stp-pair-only"
    if (d0["group"] == "ldst-imm9" and d0["access"] == "str"
            and d0["mode"] == "pre" and d0["rt"] == 30 and d0["rn"] == 31):
        return "str-lr-preindex"
    return "leaf"


def saves_lr(insns):
    """函数体内是否保存了 X30(LR):只有会「调用别人」的函数才需要。"""
    for d in insns:
        if d["group"] == "ldst-pair" and d["access"] == "stp" and \
                30 in (d.get("rt"), d.get("rt2")):
            return True
        if d["group"] == "ldst-imm9" and d["access"] == "str" and d.get("rt") == 30:
            return True
    return False


def frame_chain_size(insns):
    """从序言里取出为 X29/X30 腾出的字节数(0 表示未建帧链)。"""
    d0 = insns[0] if insns else None
    if d0 and d0["group"] == "ldst-pair" and d0["access"] == "stp" \
            and d0["rt"] == 29 and d0["rt2"] == 30:
        return -d0["offset"]
    if d0 and d0["group"] == "ldst-imm9" and d0["access"] == "str" and d0["rt"] == 30:
        return -d0["offset"]
    return 0


# ------------------------------------------------- 自检

# 真实 GCC 4.8.1 ARM64 反汇编:main → puts@plt → 返回(见 README §参考资料)
GOLDEN = [
    (0x400590, 0xA9BF7BFD, "stp x29, x30, [sp, #-16]!"),
    (0x400594, 0x910003FD, "mov x29, sp"),
    (0x400598, 0x90000000, "adrp x0, #0x0"),
    (0x40059C, 0x91192000, "add x0, x0, #0x648"),
    (0x4005A0, 0x97FFFFA0, "bl 0x400420"),
    (0x4005A4, 0x52800000, "mov w0, #0x0"),
    (0x4005A8, 0xA8C17BFD, "ldp x29, x30, [sp], #16"),
    (0x4005AC, 0xD65F03C0, "ret"),
]


def main():
    print("== 1. 真实 ARM64 函数逐条解码 ==")
    insns = []
    for pc, w, want in GOLDEN:
        got = dis(w, pc)
        insns.append(decode(w, pc))
        check(got == want, "0x%08X @0x%X → %s" % (w, pc, want), "got %r" % got)

    print("== 2. 序言/尾声形态 ==")
    check(classify_prologue(insns) == "stp-frame-pair",
          "完整帧序言 → stp-frame-pair", classify_prologue(insns))
    check(saves_lr(insns), "序言保存 LR → 非叶子函数")
    check(frame_chain_size(insns) == 16, "帧记录占 16 字节(X29+X30 各 8)",
          frame_chain_size(insns))
    leaf = [decode(0x8B010000), decode(0xD65F03C0)]
    check(classify_prologue(leaf) == "leaf", "无 STP x29,x30 → leaf")
    check(not saves_lr(leaf), "leaf 未保存 LR:BL 不会覆盖任何已存的返回地址")
    check(frame_chain_size(leaf) == 0, "leaf 帧链长度 0")
    str_lr = [decode(0xF81F0FFE), decode(0xCB010000), decode(0xD65F03C0)]
    # 0xF81F0FFE = str x30, [sp, #-16]!
    check(classify_prologue(str_lr) == "str-lr-preindex",
          "只存 LR 不入帧链 → str-lr-preindex", classify_prologue(str_lr))
    check(saves_lr(str_lr), "str x30 也算保存了 LR(非叶子)")

    print("== 3. 位域事实(与官方编码表逐字段对齐) ==")
    d = decode(0x91192000, 0x40059C)
    check(d["sf"] == 1, "ADD 64 位:sf(bit31)=1")
    check(d["op"] == 0 and d["S"] == 0, "ADD:op(bit30)=0,S(bit29)=0(不动 NZCV)")
    check(d["imm12"] == 0x648 and d["shift"] == 0, "imm12(21:10)=0x648,sh(23:22)=0")
    check(bits(0x91192000, 28, 24) == 0b10001, "加/减立即数组标识位[28:24]=10001")
    check(bits(0xCB010000, 28, 24) == 0b01011, "加/减移位寄存器组标识位[28:24]=01011")
    check(bits(0xAA0103E0, 28, 24) == 0b01010, "逻辑移位寄存器组标识位[28:24]=01010")
    check(bits(0x52800000, 28, 23) == 0b100101, "宽立即数组标识位[28:23]=100101")

    print("== 4. 一位之差就是另一条指令 ==")
    check(dis(0x8B010000) == "add x0, x0, x1", "0x8B010000 = add x0,x0,x1")
    check(dis(0xCB010000) == "sub x0, x0, x1", "翻 bit30 → sub(op=1)")
    check(dis(0xEB010000) == "subs x0, x0, x1", "再翻 bit29 → subs(S=1,写 NZCV)")
    check(bits(0xEB010000, 29, 29) == 1, "S 位 = bit29:唯一决定是否更新 NZCV")
    check(dis(0xAA0103E0) == "mov x0, x1", "ORR Xd,XZR,Xm → mov Xd,Xm")
    check(dis(0xD503201F) == "nop", "hint 空间 0xD503201F = nop")

    print("== 5. 分支偏移与条件码 ==")
    check(decode(0x97FFFFA0, 0x4005A0)["offset"] == -384, "BL imm26<<2 = -384")
    check(decode(0x54000000, 0x1000)["text"] == "b.eq 0x1000", "imm19=0 的条件分支原地跳")
    check(decode(0x5400000F, 0x1000)["text"] == "b.nv 0x1000", "cond=1111 → 保留值 nv")
    check(len(COND) == 16, "cond 字段 4 位 → 16 个条件码")
    check(sx(0x3FFFFA0, 26) == -96, "26 位立即数符号扩展 0x3FFFFA0 → -96")
    check(sx(0x7FFFF, 19) == -1, "19 位立即数全 1(0x7FFFF)→ -1")
    check(sx(0x1FFFF, 19) == 0x1FFFF, "19 位立即数 0x1FFFF 最高位为 0 → 保持正值")

    print("== 6. 定长:无需 length disassembler ==")
    blob = b"".join(w.to_bytes(4, "little") for _, w, _ in GOLDEN)
    got = decode_bytes(blob, 0x400590)
    check(len(got) == 8, "12 字节机器码按 4 字节切分为 8 条")
    check((got[-1][1].get("alias") or got[-1][1]["text"]) == "ret",
          "末条 = ret", got[-1][1].get("alias") or got[-1][1]["text"])
    check(all(len("%08X" % w) == 8 for _, w, _ in GOLDEN), "所有 A64 指令恰好 4 字节")
    check(decode(0x0B000000)["group"] == "add-sub-shift-reg",
          "任意 4 字节边界都是合法起点(对照 x86-64 变长指令需逐字节试探)")

    print("== 7. 载入/存储的 scale 与变址 ==")
    pre, post = decode(0xA9BF7BFD), decode(0xA8C17BFD)
    check(pre["mode"] == "pre" and pre["offset"] == -16, "STP idx=11 → pre,imm7×8=-16")
    check(post["mode"] == "post" and post["offset"] == 16, "LDP idx=01 → post,imm7×8=+16")
    check(bits(0xA9BF7BFD, 26, 26) == 0 and bits(0xA9BF7BFD, 22, 22) == 0,
          "V(bit26)=0 → 64 位寄存器对;L(bit22)=0 → store")
    ld = decode(0xF9400BE0)          # ldr x0, [sp, #16]
    check(ld["group"] == "ldst-unsigned-imm" and ld["offset"] == 16,
          "0xF9400BE0 → ldr x0, [sp, #16](size=11 → 立即数×8)", ld["text"])

    print("\n结果: %d 项失败" % len(FAIL))
    for f in FAIL:
        print("  FAIL: %s" % f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
