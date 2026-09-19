#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CFI 展开行(Row)与回溯引擎 —— 单独成文件只为实现「单源文件 ≤ 300 行」。"""

from cfi import (Reader, reg_name, REG_RSP, DW_CFA_advance_loc, DW_CFA_advance_loc1, DW_CFA_advance_loc2,
                 DW_CFA_advance_loc4, DW_CFA_set_loc, DW_CFA_def_cfa,
                 DW_CFA_def_cfa_register, DW_CFA_def_cfa_offset,
                 DW_CFA_def_cfa_sf, DW_CFA_def_cfa_offset_sf,
                 DW_CFA_offset, DW_CFA_offset_extended, DW_CFA_val_offset,
                 DW_CFA_register, DW_CFA_undefined, DW_CFA_same_value,
                 DW_CFA_restore, DW_CFA_restore_extended,
                 DW_CFA_remember_state, DW_CFA_restore_state,
                 DW_CFA_GNU_args_size, DW_CFA_nop,
                 X86_64_REG_NAMES)


class Row(object):
    """一次展开了若干 CFI 指令后的寄存器规则快照。"""

    def __init__(self):
        self.cfa = None          # (register, offset)
        self.rules = {}          # reg -> ("undefined"/"same"/("offset",N)/("register",R)/("val_offset",N))
        self.stack = []          # remember_state 压进去的历史快照
        self.args_size = 0
        self.loc = 0

    def copy(self):
        r = Row()
        r.cfa = self.cfa
        r.rules = dict(self.rules)
        r.stack = list(self.stack)
        r.args_size = self.args_size
        r.loc = self.loc
        return r

    def cfa_of(self, regs):
        if self.cfa is None:
            raise ValueError("当前位置尚未定义 CFA")
        reg, off = self.cfa
        return regs[reg] + off

    def value_of(self, reg, regs, memory_read):
        """按规则取一个寄存器的「上一帧值」。"""
        rule = self.rules.get(reg, ("undefined",))
        if rule[0] == "undefined":
            raise KeyError("寄存器 %s 的规则是 undefined" % reg_name(reg))
        if rule[0] == "same":
            return regs[reg]
        if rule[0] == "offset":
            return memory_read(self.cfa_of(regs) + rule[1])
        if rule[0] == "val_offset":
            return self.cfa_of(regs) + rule[1]
        if rule[0] == "register":
            return regs[rule[1]]
        raise ValueError("未知规则 %r" % (rule,))


def decode_instructions(blob, cie):
    """把 CFI 字节流解成 (opcode, operands...) 列表，不做语义。

    注意高两位是主操作码、低 6 位内嵌操作数 —— 这是 CFI 压缩率的主要来源：
    最常见的三条（推进位置 / 保存寄存器 / 恢复寄存器）都只要一个字节。
    """
    rd = Reader(blob, 0)
    out = []
    while rd.off < len(blob):
        op = rd.u8()
        primary = op & 0xC0
        arg = op & 0x3F
        if primary == DW_CFA_advance_loc:
            out.append((DW_CFA_advance_loc, arg))
        elif primary == DW_CFA_offset:
            out.append((DW_CFA_offset, arg, rd.uleb()))
        elif primary == DW_CFA_restore:
            out.append((DW_CFA_restore, arg))
        elif op == DW_CFA_nop:
            out.append((DW_CFA_nop,))
        elif op == DW_CFA_set_loc:
            out.append((DW_CFA_set_loc, rd.u64()))
        elif op == DW_CFA_advance_loc1:
            out.append((DW_CFA_advance_loc1, rd.u8()))
        elif op == DW_CFA_advance_loc2:
            out.append((DW_CFA_advance_loc2, struct.unpack_from("<H", rd.raw(2))[0]))
        elif op == DW_CFA_advance_loc4:
            out.append((DW_CFA_advance_loc4, rd.u32()))
        elif op == DW_CFA_offset_extended:
            out.append((DW_CFA_offset_extended, rd.uleb(), rd.uleb()))
        elif op == DW_CFA_restore_extended:
            out.append((DW_CFA_restore_extended, rd.uleb()))
        elif op == DW_CFA_undefined:
            out.append((DW_CFA_undefined, rd.uleb()))
        elif op == DW_CFA_same_value:
            out.append((DW_CFA_same_value, rd.uleb()))
        elif op == DW_CFA_register:
            out.append((DW_CFA_register, rd.uleb(), rd.uleb()))
        elif op == DW_CFA_remember_state:
            out.append((DW_CFA_remember_state,))
        elif op == DW_CFA_restore_state:
            out.append((DW_CFA_restore_state,))
        elif op == DW_CFA_def_cfa:
            out.append((DW_CFA_def_cfa, rd.uleb(), rd.uleb()))
        elif op == DW_CFA_def_cfa_register:
            out.append((DW_CFA_def_cfa_register, rd.uleb()))
        elif op == DW_CFA_def_cfa_offset:
            out.append((DW_CFA_def_cfa_offset, rd.uleb()))
        elif op == DW_CFA_def_cfa_sf:
            out.append((DW_CFA_def_cfa_sf, rd.uleb(), rd.sleb()))
        elif op == DW_CFA_def_cfa_offset_sf:
            out.append((DW_CFA_def_cfa_offset_sf, rd.sleb()))
        elif op == DW_CFA_val_offset:
            out.append((DW_CFA_val_offset, rd.uleb(), rd.uleb()))
        elif op == DW_CFA_GNU_args_size:
            out.append((DW_CFA_GNU_args_size, rd.uleb()))
        else:
            raise NotImplementedError("未实现的 DW_CFA 0x%02x" % op)
    return out


def apply_one(row, ins, cie, initial):
    """执行一条 CFI 指令对规则集的影响（advance_* 由调用方单独判停）。"""
    op = ins[0]
    if op == DW_CFA_def_cfa:
        row.cfa = (ins[1], ins[2])
    elif op == DW_CFA_def_cfa_register:
        row.cfa = (ins[1], row.cfa[1])
    elif op == DW_CFA_def_cfa_offset:
        row.cfa = (row.cfa[0], ins[1])
    elif op == DW_CFA_def_cfa_sf:
        row.cfa = (ins[1], ins[2] * cie.data_align)
    elif op == DW_CFA_def_cfa_offset_sf:
        row.cfa = (row.cfa[0], ins[1] * cie.data_align)
    elif op in (DW_CFA_offset, DW_CFA_offset_extended):
        row.rules[ins[1]] = ("offset", ins[2] * cie.data_align)
    elif op == DW_CFA_val_offset:
        row.rules[ins[1]] = ("val_offset", ins[2] * cie.data_align)
    elif op == DW_CFA_register:
        row.rules[ins[1]] = ("register", ins[2])
    elif op == DW_CFA_undefined:
        row.rules[ins[1]] = ("undefined",)
    elif op == DW_CFA_same_value:
        row.rules[ins[1]] = ("same",)
    elif op in (DW_CFA_restore, DW_CFA_restore_extended):
        row.rules[ins[1]] = initial.get(ins[1], ("undefined",))
    elif op == DW_CFA_remember_state:
        row.stack.append((row.cfa, dict(row.rules)))
    elif op == DW_CFA_restore_state:
        if not row.stack:
            raise ValueError("restore_state 但状态栈为空")
        row.cfa, saved = row.stack.pop()
        row.rules = dict(saved)
    elif op == DW_CFA_GNU_args_size:
        row.args_size = ins[1]
    elif op == DW_CFA_nop:
        pass
    else:
        raise NotImplementedError("未实现的语义 0x%02x" % op)
    return row


def build_row_to_pc(fde, pc):
    """跑到目标 pc 为止,产出那一时刻的 Row。

    两件容易写错的事:
    1. **必须先跑 CIE 的 initial instructions**,建立函数入口处的默认规则;
       这套规则同时充当 DW_CFA_restore 的「还原目标」。
    2. **一条 advance_* 会把 loc 推到 pc 之后时必须整条不生效** ——
       CFI 的规则从「推进之后的地址」开始生效,越过 pc 就不能再应用。
    """
    cie = fde.cie
    row = Row()
    for ins in decode_instructions(cie.instructions, cie):
        row = apply_one(row, ins, cie, {})
    initial = dict(row.rules)          # 函数入口处的规则 = initial rules
    row.loc = fde.pc_begin
    advance_ops = (DW_CFA_advance_loc, DW_CFA_advance_loc1,
                   DW_CFA_advance_loc2, DW_CFA_advance_loc4)
    for ins in decode_instructions(fde.instructions, cie):
        op = ins[0]
        if op in advance_ops:
            if row.loc + ins[1] * cie.code_align > pc:
                break
            row.loc += ins[1] * cie.code_align
        elif op == DW_CFA_set_loc:
            if ins[1] > pc:
                break
            row.loc = ins[1]
        else:
            row = apply_one(row, ins, cie, initial)
    return row


def unwind_step(fde, pc, regs, memory_read):
    """做一帧回溯。返回 (新的 pc, 新的寄存器表, Row)。

    栈指针特殊处理:标准里 CFA 就是「上一帧的 %rsp」,所以这里直接写 CFA,
    而不是去查 %rsp 自己那条规则(%rsp 列常常标成 undefined)。
    """
    row = build_row_to_pc(fde, pc)
    cfa = row.cfa_of(regs)
    new_regs = dict(regs)
    for i in range(len(X86_64_REG_NAMES)):
        try:
            new_regs[i] = row.value_of(i, regs, memory_read)
        except KeyError:
            pass          # undefined:调用者没保存它,上一帧没有可恢复的值
    ra = row.value_of(row_rules_ra(fde.cie), regs, memory_read)
    new_regs[REG_RSP] = cfa
    return ra, new_regs, row


def row_rules_ra(cie):
    """返回地址列的编号写在 CIE 的 Return Address Register 字段里。"""
    return cie.ra_reg
