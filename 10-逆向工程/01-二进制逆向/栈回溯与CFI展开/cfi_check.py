#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CFI 自检：合成一条 .eh_frame，跑 CIE/FDE 解析 + DW_CFA 程序 + 三帧回溯。

运行：python cfi_check.py

栈布局是手工算过的（见 README §五），每一条断言都是对「这段时间这条
CFI 规则是否生效」的直接检验，不是照抄实现的快照。
"""

import struct
import sys

from cfi import (CIE, FDE, Reader, PtrEnc, parse_eh_frame, find_fde,
                 X86_64_REG_NAMES, REG_RSP, REG_RBP, REG_RA,
                 DW_CFA_def_cfa, DW_CFA_def_cfa_offset, DW_CFA_def_cfa_register,
                 DW_CFA_offset, DW_CFA_advance_loc, DW_CFA_remember_state,
                 DW_CFA_restore_state, DW_CFA_GNU_args_size, DW_CFA_undefined,
                 DW_CFA_register, DW_CFA_restore)
from cfi_unwind import (apply_one, build_row_to_pc, unwind_step, Row,
                        decode_instructions)

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def raises(fn, exc, label):
    try:
        fn()
    except exc:
        return True
    except Exception as e:
        FAIL.append("%s:应抛 %s,实抛 %r" % (label, exc.__name__, e))
        return False
    FAIL.append("%s:应抛 %s 却没有" % (label, exc.__name__))
    return False


def uleb(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | 0x80 if v else b)
        if not v:
            return bytes(out)


def sleb(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        done = (v == 0 and not (b & 0x40)) or (v == -1 and (b & 0x40))
        out.append(b if done else b | 0x80)
        if done:
            return bytes(out)


def pad_to(blob, total_mod8):
    """填 DW_CFA_nop 把「length 字段 + 本体」凑成寻址单位边界的倍数。"""
    need = total_mod8 - ((4 + len(blob)) % total_mod8)
    if need == total_mod8:
        need = 0
    return blob + b"\x00" * need


def build_cie(code_align=1, data_align=-8, ra_reg=REG_RA, instructions=b""):
    body = struct.pack("<IB", 0, 1) + b"\x00"          # CIE ID=0, version=1, aug=""
    body += uleb(code_align) + sleb(data_align) + uleb(ra_reg)
    body += instructions
    body = pad_to(body, 8)
    return struct.pack("<I", len(body)) + body


def build_fde(cie_ptr_off_of_self, cie_offset, pc_begin, pc_range, instructions):
    body = struct.pack("<I", cie_ptr_off_of_self - cie_offset)
    body += struct.pack("<QQ", pc_begin, pc_range) + instructions
    body = pad_to(body, 8)
    return struct.pack("<I", len(body)) + body


def main():
    print("== 1. psABI Figure 3.36 寄存器编号 ==")
    check(REG_RA == 16, "返回地址 RA = 16（不是物理寄存器，却在 0(%rsp)）")
    check(REG_RSP == 7 and REG_RBP == 6, "%rsp=7 / %rbp=6")
    check(X86_64_REG_NAMES[0] == "%rax" and X86_64_REG_NAMES[1] == "%rdx",
          "编号 0=%rax、1=%rdx（紧跟顺序不是字母序）")
    check(X86_64_REG_NAMES[15] == "%r15", "编号 15 = %r15")

    print("== 2. psABI Figure 3.37 指针编码掩码 ==")
    e = PtrEnc(0x00)
    check(e.format == 0 and not any([e.signed, e.pc_rel, e.text_rel, e.func_rel]),
          "0x00 = 直接的绝对指针")
    check(PtrEnc(0x10).pc_rel, "0x10 = PC 相对")
    check(PtrEnc(0x20).text_rel, "0x20 = text 段相对")
    check(PtrEnc(0x30).data_rel and PtrEnc(0x30).text_rel,
          "0x30 = 0x20|0x10 = data 段相对")
    check(PtrEnc(0x40).func_rel, "0x40 = 函数起始相对")
    # 0x08 是 signed 标志位,与「存法」位 0x1..0x4 是叠加关系,不是新编码
    check(PtrEnc(0x0B).signed and (PtrEnc(0x0B).format & 0x07) == 0x03,
          "0x0b = 0x08(signed) + 0x03(4 字节) = sdata4")
    # 往返：编码侧 4 字节 + pcrel
    rd = Reader(struct.pack("<i", -4) + b"\x00" * 8, 0)
    check(rd.encoded_ptr(0x0B, place=0) == -4, "sdata4 带符号读")
    rd2 = Reader(struct.pack("<I", 0x100) + b"\x00" * 8, 0)
    check(rd2.encoded_ptr(0x13, place=0x401000) == 0x401100, "udata4 + PC 相对")
    raises(lambda: Reader(b"\x00" * 16, 0).encoded_ptr(0x44), ValueError,
           "未实现的编码要报错")

    print("== 3. 合成一条 .eh_frame（1 CIE + 3 FDE + terminator） ==")
    # CIE initial: def_cfa %rsp, 8  ;  offset RA, 1(× -8 = CFA-8)
    cie_ins = bytes([DW_CFA_def_cfa]) + b"\x07\x08" + bytes([0x80 | REG_RA]) + b"\x01"
    cie_bytes = build_cie(instructions=cie_ins)
    check(len(cie_bytes) % 8 == 0, "CIE 记录整体是寻址单位的倍数：%d 字节" % len(cie_bytes))

    # 每个函数的 FDE 指令：push rbp 后 → mov rsp,rbp 后
    fde_ins = (bytes([DW_CFA_advance_loc | 1])
               + bytes([DW_CFA_def_cfa_offset]) + b"\x10"
               + bytes([0x80 | REG_RBP]) + b"\x02"
               + bytes([DW_CFA_advance_loc | 3])
               + bytes([DW_CFA_def_cfa_register]) + b"\x06")
    frame = bytearray(cie_bytes)
    func_addrs = [(0x400500, 0x40), (0x400560, 0x40), (0x400600, 0x40)]
    fde_starts = []
    for pc_begin, pc_range in func_addrs:
        start = len(frame)
        ptr_field_off = start + 4          # Length 之后就是 CIE Pointer 字段
        frame += build_fde(ptr_field_off, 0, pc_begin, pc_range, fde_ins)
        fde_starts.append(start)
    frame += struct.pack("<I", 0)          # terminator
    frame = bytes(frame)

    cies, fdes = parse_eh_frame(frame)
    check(len(cies) == 1 and len(fdes) == 3, "1 个 CIE、3 个 FDE")
    cie = cies[0]
    check(cie.version == 1, "CIE Version = 1")
    check(cie.aug == "", "augmentation 串为空 ⇒ 没有 Augmentation Data 两段")
    check(cie.code_align == 1 and cie.data_align == -8, "code_align=1 / data_align=-8")
    check(cie.ra_reg == REG_RA, "Return Address Register = 16")
    for f, (pc_begin, _) in zip(fdes, func_addrs):
        check(f.pc_begin == pc_begin, "FDE pc_begin = 0x%x" % pc_begin)
        check(f.cie is cie, "FDE 关联到同一个 CIE")
    check(find_fde(fdes, 0x400610) is fdes[2], "按 pc 命中第三个 FDE")
    check(find_fde(fdes, 0x400700) is None, "超出范围返回 None")
    check(find_fde(fdes, 0x400560) is fdes[1], "pc_begin 自身也算命中（左闭右开）")

    print("== 4. DW_CFA 解码：高两位主操作码 + 低 6 位内嵌操作数 ==")
    ops = decode_instructions(cie_ins, cie)
    check(ops[0] == (DW_CFA_def_cfa, 7, 8), "CIE:def_cfa %rsp, 8")
    check(ops[1] == (DW_CFA_offset, REG_RA, 1), "CIE:offset RA, 1（0x90 的低 6 位=16）")
    fops = decode_instructions(fde_ins, cie)
    check(fops[0] == (DW_CFA_advance_loc, 1), "advance_loc 1")
    check(fops[2] == (DW_CFA_offset, REG_RBP, 2), "offset %rbp, 2")
    check(fops[4] == (DW_CFA_def_cfa_register, REG_RBP), "def_cfa_register %rbp")
    # 0x10 = DW_CFA_expression 未实现 —— 表达式型 resource quote 必须显式报错,
    # 静默跳过会让后面的指令整体错位。
    raises(lambda: decode_instructions(b"\x10\x02\x91\x78", cie),
           NotImplementedError, "未实现的 DW_CFA")

    print("== 5. 不同位置上的规则（这才是展开的关键） ==")
    fde = fdes[2]
    pc0 = fde.pc_begin
    r0 = build_row_to_pc(fde, pc0)
    check(r0.cfa == (REG_RSP, 8), "入口处：CFA = %rsp + 8")
    check(r0.rules[REG_RA] == ("offset", -8), "入口处：RA 在 CFA-8（1 × data_align -8）")
    check(REG_RBP not in r0.rules, "入口处：%rbp 还是 undefined（尚未 push）")
    r1 = build_row_to_pc(fde, pc0 + 1)     # push %rbp 之后
    check(r1.cfa == (REG_RSP, 16), "push 之后：CFA = %rsp + 16")
    check(r1.rules[REG_RBP] == ("offset", -16), "%rbp 保存在 CFA-16（2 × -8）")
    r4 = build_row_to_pc(fde, pc0 + 4)     # mov %rsp,%rbp 之后
    check(r4.cfa == (REG_RBP, 16), "建立帧指针后：CFA = %rbp + 16")
    # 「把 loc 推过 pc 的那条指令整条不生效」
    r3 = build_row_to_pc(fde, pc0 + 3)
    check(r3.cfa == (REG_RSP, 16), "loc 3 时尚未切换到 %rbp")
    check(r4.rules[REG_RBP] == ("offset", -16), "%rbp 的保存位置一直没变")

    print("== 6. 三帧回溯（手工算过的栈） ==")
    rbp_o, rbp_m, rbp_i = 0x7FFFFFFFE100, 0x7FFFFFFFE0F0, 0x7FFFFFFFE0E0
    pc_in_o, pc_in_m = 0x400520, 0x400580
    mem = {
        rbp_i: rbp_m,          # inner 保存的 %rbp = middle 的帧指针
        rbp_i + 8: pc_in_m,    # inner 的返回地址
        rbp_m: rbp_o,          # middle 保存的 %rbp = outer 的帧指针
        rbp_m + 8: pc_in_o,    # middle 的返回地址
    }
    # 布局自检：保存的 %rbp 必须正好等于调用者的 %rbp，CFA-8 是返回地址。
    # 这两条写在注释里容易算错，用断言把它钉住。
    for addr, val in sorted(mem.items()):
        check(val in (rbp_o, rbp_m, pc_in_o, pc_in_m), "内存布局 %#x 取值合法" % addr)
    check(mem[rbp_i] == rbp_m and mem[rbp_i + 8] == pc_in_m,
          "inner 帧：CFA-16 存调用者 rbp、CFA-8 存返回地址")
    check(mem[rbp_m] == rbp_o and mem[rbp_m + 8] == pc_in_o,
          "middle 帧同理")

    def memory_read(addr):
        if addr not in mem:
            raise KeyError("未映射的栈地址 0x%x" % addr)
        return mem[addr]

    regs = dict([(i, 0) for i in range(len(X86_64_REG_NAMES))])
    regs[REG_RSP] = rbp_i
    regs[REG_RBP] = rbp_i
    pc = 0x400610
    chain = []
    for _ in range(2):
        f = find_fde(fdes, pc)
        check(f is not None, "pc 0x%x 能找到 FDE" % pc)
        pc, regs, row = unwind_step(f, pc, regs, memory_read)
        chain.append((pc, regs[REG_RBP]))
    check(chain[0] == (pc_in_m, rbp_m), "第一帧：回到 middle，rbp=0x%x" % rbp_m)
    check(chain[1] == (pc_in_o, rbp_o), "第二帧：回到 outer，rbp=0x%x" % rbp_o)

    print("== 7. CFA 就是「上一帧的 %rsp」，不是查 %rsp 的规则 ==")
    regs2 = dict([(i, 0) for i in range(len(X86_64_REG_NAMES))])
    regs2[REG_RSP] = rbp_i
    regs2[REG_RBP] = rbp_i
    pc2, regs3, row = unwind_step(fdes[2], 0x400610, regs2, memory_read)
    check(regs3[REG_RSP] == rbp_i + 16, "%rsp 被写成 CFA = %rbp + 16")
    check(row.rules.get(REG_RSP, ("undefined",))[0] == "undefined",
          "%rsp 列本身是 undefined —— 这正是必须特判它的原因")

    print("== 8. remember_state / restore_state / GNU_args_size ==")
    ins2 = (bytes([DW_CFA_def_cfa]) + b"\x07\x08"
            + bytes([0x80 | REG_RBP]) + b"\x01"
            + bytes([DW_CFA_remember_state])
            + bytes([DW_CFA_def_cfa_register]) + b"\x06"
            + bytes([0x80 | REG_RBP]) + b"\x05"
            + bytes([DW_CFA_restore_state])
            + bytes([DW_CFA_GNU_args_size]) + b"\x20")
    cie2 = CIE(aug="", code_align=1, data_align=-8, ra_reg=REG_RA,
               instructions=bytes([DW_CFA_def_cfa]) + b"\x07\x08")
    row = Row()
    initial = {}
    for ins in decode_instructions(ins2, cie2):
        if ins[0] in (DW_CFA_remember_state, DW_CFA_restore_state,
                      DW_CFA_GNU_args_size):
            row = apply_one(row, ins, cie2, initial)
        elif ins[0] == DW_CFA_def_cfa:
            row = apply_one(row, ins, cie2, initial)
        elif ins[0] == DW_CFA_offset:
            row = apply_one(row, ins, cie2, initial)
        elif ins[0] == DW_CFA_def_cfa_register:
            row = apply_one(row, ins, cie2, initial)
    check(row.cfa == (REG_RSP, 8), "restore_state 把 CFA 拉回 remember 时的 %rsp+8")
    check(row.rules[REG_RBP] == ("offset", -8), "并同时把 %rbp 的规则拉回去")
    check(row.args_size == 0x20, "GNU_args_size 0x20 被记录")
    raises(lambda: apply_one(Row(), (DW_CFA_restore_state,), cie2, {}), ValueError,
           "空栈时 restore_state")
    r2 = Row()
    r2.rules[REG_RBP] = ("undefined",)
    check(apply_one(r2, (DW_CFA_register, REG_RBP, REG_RSP), cie2,
                    {}).rules[REG_RBP] == ("register", REG_RSP),
          "DW_CFA_register：%rbp 的值存在 %rsp 里")
    check(apply_one(Row(), (DW_CFA_undefined, REG_RBP), cie2,
                    {}).rules[REG_RBP] == ("undefined",),
          "DW_CFA_undefined")
    check(apply_one(Row(), (DW_CFA_restore, REG_RBP), cie2,
                    {REG_RBP: ("same",)}).rules[REG_RBP] == ("same",),
          "DW_CFA_restore 还原到 initial rules")
    check(apply_one(Row(), (DW_CFA_restore, REG_RBP), cie2,
                    {}).rules[REG_RBP] == ("undefined",),
          "initial 里没有它时还原成 undefined")

    print("\n结果: %d 项失败" % len(FAIL))
    for x in FAIL:
        print("  FAIL: %s" % x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
