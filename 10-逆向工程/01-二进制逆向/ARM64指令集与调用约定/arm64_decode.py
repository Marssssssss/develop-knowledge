#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A64(AArch64)固定 32 位定长指令解码器 —— 字段级还原 + 别名折叠。

权威依据(本轮实读,见 README §参考资料):
  * Arm《Learn the architecture - A64 Instruction Set Architecture Guide》102374 1.3
    —— Procedure Call Standard 一节(寄存器角色、XR/IP0/IP1/PR/FP/LR、ALU flags 不保留)
  * Arm A-profile A64 ISA「Index by Encoding — Data Processing -- Register」位域表(ddi0602)
    —— sf/op/S/shift/Rm/imm6/Rn/Rd 的精确位号
  * AAPCS64 规范(ARM-software/abi-aa, aapcs64.rst, 2025Q4)
    —— Table 2 寄存器角色、SP mod 16 = 0、帧记录(frame record)两 64 位值布局

本模块只做「编码 ↔ 文本」,序言形态识别与自检在 `prologue_shape.py`。
运行: python prologue_shape.py     退出码 0 表示全部断言通过。
"""

# ---------------------------------------------------------------- 基础工具

def bits(w, hi, lo):
    """取 [hi:lo] 闭区间的位域(两端均含)。"""
    return (w >> lo) & ((1 << (hi - lo + 1)) - 1)


def sx(v, n):
    """把 n 位无符号值按二进制补码做符号扩展。"""
    return v - (1 << n) if v >> (n - 1) else v


def xr(i):
    return "xzr" if i == 31 else "x%d" % i


def wr(i):
    return "wzr" if i == 31 else "w%d" % i


def spr(i):
    """SP 在寄存器编号里占 31,与 XZR 同号 —— 由指令语义决定是哪个。"""
    return "sp" if i == 31 else "x%d" % i


def simm(v):
    return "#-%d" % (-v) if v < 0 else "#%d" % v


def himm(v):
    return "#0x%x" % v


# cond 字段 4 位 → 16 个条件码(来源: A64 ISA Guide 条件分支/条件选择一节)
COND = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc",
        "hi", "ls", "ge", "lt", "gt", "le", "al", "nv"]

PAIR_IDX = {0b00: "no-alloc", 0b01: "post", 0b10: "offset", 0b11: "pre"}
LDST_IDX = {0b00: "unscaled", 0b01: "post", 0b10: "unprivileged", 0b11: "pre"}

SHIFT = {0: "lsl", 1: "lsr", 2: "asr", 3: "ror"}


def tgt_str(pc, off):
    """分支目标:给了 PC 就打印绝对地址,否则打印相对偏移。"""
    return "0x%x" % (pc + off) if pc is not None else simm(off)


# ---------------------------------------------------------------- 解码主体

def decode(w, pc=None):
    """解码一条 32 位 A64 指令,返回 dict(group/text/alias + 语义字段)。

    覆盖分支、条件分支、比较分支、PC 相对、加/减(立即数与移位寄存器)、
    逻辑(移位寄存器)、宽立即数搬移、载入/存储(无符号偏移、imm9、pair)、NOP。
    其余编码返回 group='unallocated-or-uncovered'。
    """
    # --- 分支(寄存器): 1101011 opc(24:21) 11111(20:16) 000000(15:10) Rn 00000
    if (w >> 25) == 0b1101011 and bits(w, 20, 16) == 0x1F and \
            bits(w, 15, 10) == 0 and bits(w, 4, 0) == 0:
        opc, rn = bits(w, 24, 21), bits(w, 9, 5)
        name = {0b0000: "br", 0b0001: "blr", 0b0010: "ret", 0b0100: "eret"}.get(opc)
        if name is None:
            return {"group": "unallocated-or-uncovered", "text": "??"}
        if name == "eret":
            return {"group": "branch-reg", "text": "eret", "alias": None}
        return {"group": "branch-reg", "text": "%s x%d" % (name, rn),
                "alias": "ret" if (name == "ret" and rn == 30) else None}

    # --- 条件分支 B.cond: 0101010 0 imm19(23:5) 0 cond(3:0) ----------------
    if (w & 0xFF000010) == 0x54000000:
        off = sx(bits(w, 23, 5), 19) << 2
        return {"group": "cond-branch", "offset": off, "alias": None,
                "text": "b.%s %s" % (COND[bits(w, 3, 0)], tgt_str(pc, off))}

    # --- 无条件分支 B / BL: 000101 / 100101 + imm26(25:0) ------------------
    if (w >> 26) in (0b000101, 0b100101):
        off = sx(bits(w, 25, 0), 26) << 2
        return {"group": "branch-imm", "offset": off, "alias": None,
                "text": "%s %s" % ("bl" if bits(w, 31, 31) else "b", tgt_str(pc, off))}

    # --- CBZ / CBNZ: sf 011010 op(24) imm19(23:5) Rt(4:0) ------------------
    if (w & 0x7E000000) == 0x34000000:
        off = sx(bits(w, 23, 5), 19) << 2
        sel = xr if bits(w, 31, 31) else wr
        return {"group": "compare-branch", "offset": off, "alias": None,
                "text": "%s %s, %s" % ("cbnz" if bits(w, 24, 24) else "cbz",
                                       sel(bits(w, 4, 0)), tgt_str(pc, off))}

    # --- ADR / ADRP: op(31) immlo(30:29) 10000 immhi(23:5) Rd(4:0) ---------
    if (w & 0x1F000000) == 0x10000000:
        imm = sx((bits(w, 23, 5) << 2) | bits(w, 30, 29), 21)
        rd = bits(w, 4, 0)
        if bits(w, 31, 31):                 # ADRP: 立即数为页偏移(4 KiB 页)
            return {"group": "adr", "alias": None, "page_imm": imm,
                    "text": "adrp x%d, #0x%x" % (rd, imm << 12)}
        return {"group": "adr", "alias": None, "text": "adr x%d, %s" % (rd, simm(imm))}

    # --- 加/减(立即数): sf op S 10001 sh(23:22) imm12(21:10) Rn Rd --------
    if (w & 0x1F000000) == 0x11000000:
        sf, op, s = bits(w, 31, 31), bits(w, 30, 30), bits(w, 29, 29)
        sh, imm12 = bits(w, 23, 22), bits(w, 21, 10)
        rn, rd = bits(w, 9, 5), bits(w, 4, 0)
        base = ("subs" if s else "sub") if op else ("adds" if s else "add")
        sel = xr if sf else wr
        alias = None
        if base == "add" and imm12 == 0 and sh == 0 and rn == 31 and rd != 31:
            alias = "mov %s, sp" % sel(rd)          # ADD Xd, SP, #0 → MOV Xd, SP
        return {"group": "add-sub-imm", "alias": alias, "sf": sf, "op": op,
                "S": s, "imm12": imm12, "shift": sh, "rn": rn, "rd": rd,
                "text": "%s %s, %s, %s" % (base, sel(rd), spr(rn),
                                           himm(imm12 << (12 if sh else 0)))}

    # --- 加/减(移位寄存器): sf op S 01011 shift(23:22) 0 Rm imm6 Rn Rd ----
    if (w & 0x1F200000) == 0x0B000000:
        sf, op, s = bits(w, 31, 31), bits(w, 30, 30), bits(w, 29, 29)
        shift, rm, imm6 = bits(w, 23, 22), bits(w, 20, 16), bits(w, 15, 10)
        rn, rd = bits(w, 9, 5), bits(w, 4, 0)
        base = ("subs" if s else "sub") if op else ("adds" if s else "add")
        sel = xr if sf else wr
        tail = "" if imm6 == 0 else ", %s #%d" % (SHIFT[shift], imm6)
        return {"group": "add-sub-shift-reg", "alias": None, "sf": sf, "op": op,
                "S": s, "rn": rn, "rd": rd, "rm": rm,
                "text": "%s %s, %s, %s%s" % (base, sel(rd), sel(rn), sel(rm), tail)}

    # --- 逻辑(移位寄存器): sf opc(30:29) 01010 shift N(21) Rm imm6 Rn Rd -
    if (w & 0x1F000000) == 0x0A000000:
        sf, opc, n = bits(w, 31, 31), bits(w, 30, 29), bits(w, 21, 21)
        shift, rm, imm6 = bits(w, 23, 22), bits(w, 20, 16), bits(w, 15, 10)
        rn, rd = bits(w, 9, 5), bits(w, 4, 0)
        # opc 两位 + N 位共同决定助记符;逻辑组的 S 变体是 opc=11,没有独立 S 位
        #   00/0 AND 00/1 BIC 01/0 ORR 01/1 ORN 10/0 EOR 10/1 EON 11/0 ANDS 11/1 BICS
        name = {0b00: "and", 0b01: "orr", 0b10: "eor", 0b11: "ands"}[opc]
        if n:
            name = {"and": "bic", "orr": "orn", "eor": "eon", "ands": "bics"}[name]
        sel = xr if sf else wr
        alias = None
        if name == "orr" and rn == 31 and imm6 == 0 and shift == 0:
            alias = "mov %s, %s" % (sel(rd), sel(rm))   # ORR Xd,XZR,Xm → MOV Xd,Xm
        tail = "" if imm6 == 0 else ", %s #%d" % (SHIFT[shift], imm6)
        return {"group": "logical-shift-reg", "alias": alias, "sf": sf, "opc": opc,
                "text": "%s %s, %s, %s%s" % (name, sel(rd), sel(rn), sel(rm), tail)}

    # --- 宽立即数搬移: sf opc(30:29) 100101 hw(22:21) imm16(20:5) Rd ------
    if (w & 0x1F800000) == 0x12800000:
        name = {0b00: "movn", 0b10: "movz", 0b11: "movk"}.get(bits(w, 30, 29))
        if name is None:
            return {"group": "unallocated-or-uncovered", "text": "??"}
        hw, imm16, rd = bits(w, 22, 21), bits(w, 20, 5), bits(w, 4, 0)
        sel = xr if bits(w, 31, 31) else wr
        shift = "" if hw == 0 else ", lsl #%d" % (hw * 16)
        alias = ("mov %s, %s" % (sel(rd), himm(imm16))
                 if (name == "movz" and hw == 0) else None)
        return {"group": "move-wide", "alias": alias, "hw": hw, "imm16": imm16,
                "text": "%s %s, %s%s" % (name, sel(rd), himm(imm16), shift)}

    # --- 载入/存储(无符号偏移): size(31:30) 111 V(26) 01 opc(23:22) imm12 Rn Rt
    if (w & 0x3B000000) == 0x39000000:
        v, opc = bits(w, 26, 26), bits(w, 23, 22)
        name = {(0, 0b00): "str", (0, 0b01): "ldr", (0, 0b10): "ldrsb"}.get((v, opc))
        if name is None:
            return {"group": "unallocated-or-uncovered", "text": "??"}
        scale, imm12 = bits(w, 31, 30), bits(w, 21, 10)
        rn, rt = bits(w, 9, 5), bits(w, 4, 0)
        return {"group": "ldst-unsigned-imm", "access": name, "alias": None,
                "rt": rt, "rn": rn, "offset": imm12 << scale,
                "text": "%s %s, [%s, %s]" % (name, xr(rt), spr(rn), himm(imm12 << scale))}

    # --- 载入/存储(imm9): size 111 V 00 opc imm9(20:12) idx(11:10) Rn Rt ---
    if (w & 0x3B200000) == 0x38000000:
        v, opc = bits(w, 26, 26), bits(w, 23, 22)
        name = {(0, 0b00): "str", (0, 0b01): "ldr"}.get((v, opc))
        if name is None:
            return {"group": "unallocated-or-uncovered", "text": "??"}
        off, idx = sx(bits(w, 20, 12), 9), bits(w, 11, 10)
        rn, rt = bits(w, 9, 5), bits(w, 4, 0)
        mem = "[%s, %s]%s" % (spr(rn), simm(off), "!" if idx == 0b11 else "")
        if idx == 0b01:
            mem = "[%s], %s" % (spr(rn), simm(off))
        return {"group": "ldst-imm9", "access": name, "alias": None, "rt": rt,
                "rn": rn, "offset": off, "mode": LDST_IDX[idx],
                "text": "%s %s, %s" % (name, xr(rt), mem)}

    # --- 载入/存储 pair: opc(31:30)=10 101 V 0 idx(24:23) L(22) imm7 Rt2 Rn Rt
    if (w & 0x3A000000) == 0x28000000:
        v, idx, l = bits(w, 26, 26), bits(w, 24, 23), bits(w, 22, 22)
        if bits(w, 31, 30) != 0b10:
            return {"group": "unallocated-or-uncovered", "text": "??"}
        name = "ldp" if l else "stp"
        off = sx(bits(w, 21, 15), 7) << (3 if v == 0 else 4)
        rt2, rn, rt = bits(w, 14, 10), bits(w, 9, 5), bits(w, 4, 0)
        mem = "[%s, %s]%s" % (spr(rn), simm(off), "!" if idx == 0b11 else "")
        if idx == 0b01:
            mem = "[%s], %s" % (spr(rn), simm(off))
        return {"group": "ldst-pair", "access": name, "alias": None, "rt": rt,
                "rt2": rt2, "rn": rn, "offset": off, "mode": PAIR_IDX[idx],
                "text": "%s x%d, x%d, %s" % (name, rt, rt2, mem)}

    if w == 0xD503201F:
        return {"group": "hint", "text": "nop", "alias": None}

    return {"group": "unallocated-or-uncovered", "text": "??"}


def dis(w, pc=None):
    """带别名折叠的最短显示形式(与 objdump 习惯一致)。"""
    d = decode(w, pc)
    return d.get("alias") or d["text"]


def decode_bytes(blob, base=0):
    """按 4 字节小端切分并解码一段机器码 —— A64 定长,无需 length disassembler。"""
    assert len(blob) % 4 == 0, "A64 指令长度恒为 4 字节"
    return [(base + i, decode(int.from_bytes(blob[i:i + 4], "little"), base + i))
            for i in range(0, len(blob), 4)]
